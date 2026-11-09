"""Experimental camera-independent surface irradiance cache.

Lighting remains linear irradiance / 255, separate from material color. Explicit
readback can feed export experiments; ordinary refinement stays on the GPU.
Caller supplies surface samples, invalidation signature and optional chart IDs
to refine. Visibility changes scheduling, never the retained ray scene.
"""
import math
import numpy as np
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix
from .l4d2preview import diagnostic_shader, draw_geometry


def accumulation_shader():
    """Preserve untouched luxels; average newly sampled ones by their own count."""
    info=gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0,'VEC2','position')
    info.sampler(0,'FLOAT_2D','sampleImage');info.sampler(1,'FLOAT_2D','previousImage')
    info.fragment_out(0,'VEC4','color')
    info.vertex_source('void main(){gl_Position=vec4(position,0,1);}')
    info.fragment_source('''void main(){
        ivec2 p=ivec2(gl_FragCoord.xy);
        vec4 old=texelFetch(previousImage,p,0), fresh=texelFetch(sampleImage,p,0);
        if(fresh.a==0.0){color=old;return;}
        float count=old.a+1.0;
        color=vec4(mix(old.rgb,fresh.rgb,1.0/count),count);
    }''')
    shader=gpu.shader.create_from_info(info)
    return shader,batch_for_shader(shader,'TRIS',{'position':[(-1,-1),(3,-1),(-1,3)]})

class SurfaceLightCache:
    def __init__(self,positions,normals,*,sample_counts=None,reuse=None):
        self.positions=np.asarray(positions,dtype=np.float32).reshape(-1,3)
        self.normals=np.asarray(normals,dtype=np.float32).reshape(-1,3)
        if self.positions.shape!=self.normals.shape or not len(self.positions):
            raise ValueError('Surface cache requires matching nonempty positions and normals')
        if not np.isfinite(self.positions).all() or not np.isfinite(self.normals).all():
            raise ValueError('Surface samples must be finite')
        self.count=len(self.positions)
        raw_sizes=np.asarray(sample_counts if sample_counts is not None else [self.count],dtype=np.float64)
        if raw_sizes.ndim!=1 or not np.isfinite(raw_sizes).all() or np.any(raw_sizes<=0) or np.any(raw_sizes!=np.floor(raw_sizes)) or raw_sizes.sum()!=self.count:
            raise ValueError('Chart sample counts must partition the surface samples')
        self.group_sizes=raw_sizes.astype(np.int64)
        self.group_starts=np.r_[0,np.cumsum(self.group_sizes)[:-1]]
        self.group_frames=np.zeros(len(self.group_sizes),dtype=np.int32)
        width=min(2048,max(1,math.ceil(math.sqrt(self.count))))
        height=math.ceil(self.count/width)
        if height>8192:raise ValueError('Surface sample budget exceeded')
        self.size=(width,height)
        ids=np.arange(self.count)
        clips=np.column_stack(((ids%width+.5)/width*2-1,(ids//width+.5)/height*2-1)).astype(np.float32)
        self.shader=reuse.shader if reuse is not None else diagnostic_shader(lit=True,shadows=True,bounce=True,surface_samples=True)
        # Share the immutable vertex buffer across changing point-index subsets.
        fmt=gpu.types.GPUVertFormat()
        for name,size in (('position',3),('normal',3),('faceNormal',3),('sampleClip',2)):
            fmt.attr_add(id=name,comp_type='F32',len=size,fetch_mode='FLOAT')
        self.vbo=gpu.types.GPUVertBuf(fmt,self.count)
        for name,data in (('position',self.positions),('normal',self.normals),('faceNormal',self.normals),('sampleClip',clips)):
            self.vbo.attr_fill(name,data)
        self.batch=gpu.types.GPUBatch(type='POINTS',buf=self.vbo)
        self.selected_key=None;self.selected_batch=None;self.batch_builds=0
        if reuse is not None and reuse.size==self.size:
            for name in ('sample','texture','spare_texture','depth','sample_fb','result_fb','spare_fb'):
                setattr(self,name,getattr(reuse,name))
        else:
            self.sample=gpu.types.GPUTexture(self.size,format='RGBA32F')
            self.texture=gpu.types.GPUTexture(self.size,format='RGBA32F')
            self.spare_texture=gpu.types.GPUTexture(self.size,format='RGBA32F')
            self.depth=gpu.types.GPUTexture(self.size,format='DEPTH_COMPONENT32F')
            self.sample_fb=gpu.types.GPUFrameBuffer(color_slots=self.sample,depth_slot=self.depth)
            self.result_fb=gpu.types.GPUFrameBuffer(color_slots=self.texture)
            self.spare_fb=gpu.types.GPUFrameBuffer(color_slots=self.spare_texture)
        if reuse is not None:self.average_shader,self.average_batch=reuse.average_shader,reuse.average_batch
        else:self.average_shader,self.average_batch=accumulation_shader()
        self.signature=None;self.frames=0;self.evaluations=0
        self.last_samples=0

    def refine(self,scene,signature,*,max_frames=72,rays=8,bounces=1,visible_groups=None):
        visible=np.arange(len(self.group_sizes)) if visible_groups is None else np.asarray(visible_groups,dtype=np.float64)
        if visible.ndim!=1 or not np.isfinite(visible).all() or np.any(visible!=np.floor(visible)) or np.any(visible<0) or np.any(visible>=len(self.group_sizes)):
            raise ValueError('Invalid visible chart index')
        visible=np.unique(visible.astype(np.int64))
        signature=(signature,rays,bounces)
        reset=signature!=self.signature
        if reset:self.signature=signature;self.frames=0;self.group_frames[:]=0
        self.last_samples=0
        viewport=gpu.state.viewport_get()
        depth=gpu.state.depth_test_get();mask=gpu.state.depth_mask_get();blend=gpu.state.blend_get()
        try:
            if reset:
                with self.result_fb.bind():self.result_fb.clear(color=(0,0,0,0))
                with self.spare_fb.bind():self.spare_fb.clear(color=(0,0,0,0))
            pending=visible[self.group_frames[visible]<max(1,max_frames)]
            if not len(pending):return False
            seed=int(self.group_frames[pending].min())
            selected=pending[self.group_frames[pending]==seed]
            count=int(self.group_sizes[selected].sum())
            if count==self.count:batch=self.batch
            else:
                key=selected.tobytes()
                if key!=self.selected_key:
                    indices=np.concatenate([np.arange(self.group_starts[i],self.group_starts[i]+self.group_sizes[i],dtype=np.int32) for i in selected])
                    index_buffer=gpu.types.GPUIndexBuf(type='POINTS',seq=indices)
                    self.selected_batch=gpu.types.GPUBatch(type='POINTS',buf=self.vbo,elem=index_buffer)
                    self.selected_key=key;self.batch_builds+=1
                batch=self.selected_batch
            with self.sample_fb.bind():
                gpu.state.viewport_set(0,0,*self.size)
                self.sample_fb.clear(color=(0,0,0,0),depth=1)
                draw_geometry(scene,self.shader,Matrix.Identity(4),lit=True,shadows=True,
                    bounce_samples=rays,bounce_depth=bounces,bounce_seed=seed,
                    sampled_bounce_sky=True,sampled_bounce_area=True,
                    primary_sky_index=seed*9,primary_sky_count=9,
                    primary_area_phase=seed%8,lighting_samples=batch)
            with self.spare_fb.bind():
                gpu.state.viewport_set(0,0,*self.size)
                gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False);gpu.state.blend_set('NONE')
                shader=self.average_shader;shader.bind();shader.uniform_sampler('sampleImage',self.sample)
                shader.uniform_sampler('previousImage',self.texture);self.average_batch.draw(shader)
            self.texture,self.spare_texture=self.spare_texture,self.texture
            self.result_fb,self.spare_fb=self.spare_fb,self.result_fb
            self.group_frames[selected]+=1;self.frames=int(self.group_frames.min())
            self.evaluations+=count;self.last_samples=count
            return True
        finally:
            gpu.state.viewport_set(*viewport);gpu.state.depth_test_set(depth)
            gpu.state.depth_mask_set(mask);gpu.state.blend_set(blend)

    def readback(self):
        """Explicit diagnostic/export readback; not used for viewport navigation."""
        with self.result_fb.bind():
            data=np.asarray(self.result_fb.read_color(0,0,*self.size,4,0,'FLOAT').to_list(),dtype=np.float32)
        return data.reshape(-1,4)[:self.count,:3].copy()

class SurfaceCacheDisplay:
    """Full-resolution raster lookup of compiled charts; no lighting rays."""
    def __init__(self,cache,faces,charts,material_binding=None,*,units_per_meter=1,reuse=None,face_tokens=None):
        if not math.isfinite(units_per_meter) or units_per_meter<=0:
            raise ValueError('Surface display units must be positive and finite')
        self.cache=cache
        by_id={f.index:f for f in faces}
        groups={}
        for chart in charts:
            face=by_id[chart['face']]
            p=np.asarray(face.positions,dtype=np.float32)
            image,uv,unlit=material_binding(face) if material_binding else (None,np.zeros((len(p),2)),False)
            key=(image.as_pointer() if image else 0,bool(unlit))
            group=groups.setdefault(key,{'image':image,'unlit':unlit,'faces':[],'signature':[]})
            group['faces'].append((face,chart,p,uv))
            token=face_tokens[face.index] if face_tokens is not None else id(face)
            group['signature'].append((token,chart['start'],chart['width'],chart['height']))
        # Only the most recent display is eligible for reuse. Offsets belong in
        # each group signature: an earlier chart resizing can shift later ones.
        reusable=getattr(reuse,'by_material',{}) if reuse is not None and reuse.units==units_per_meter and face_tokens is not None else {}
        self.units=units_per_meter
        self.shader=reuse.shader if reuse is not None else self._shader()
        self.depth_shader=reuse.depth_shader if reuse is not None else None
        self.groups=[];self.by_material={};self.reused_groups=0
        for key,group in groups.items():
            signature=tuple(group['signature'])
            old=reusable.get(key)
            if old is not None and old['signature']==signature:
                self.groups.append(old);self.by_material[key]=old;self.reused_groups+=1
                continue
            positions=[];coords=[];metadata=[];uvs=[]
            for face,chart,p,uv in group.pop('faces'):
                positions.append(p/units_per_meter);coords.append(face.to_luxel(p));uvs.append(uv)
                metadata.append(np.tile((chart['start'],chart['width'],chart['height']),(len(p),1)))
            group['signature']=signature
            group['batch']=batch_for_shader(self.shader,'TRIS',{
                'position':np.concatenate(positions).astype(np.float32),
                'luxel':np.concatenate(coords).astype(np.float32),
                'chart':np.concatenate(metadata).astype(np.float32),
                'baseUV':np.concatenate(uvs).astype(np.float32)})
            self.groups.append(group);self.by_material[key]=group

    @staticmethod
    def _shader():
        info=gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0,'VEC3','position');info.vertex_in(1,'VEC2','luxel');info.vertex_in(2,'VEC3','chart');info.vertex_in(3,'VEC2','baseUV')
        interface=gpu.types.GPUStageInterfaceInfo('surface_cache_chart')
        interface.smooth('VEC2','sampleUV');interface.smooth('VEC2','materialUV');interface.flat('VEC3','sampleChart');info.vertex_out(interface)
        info.push_constant('MAT4','mvp');info.push_constant('INT','atlasWidth')
        info.push_constant('FLOAT','exposure');info.push_constant('INT','hasTexture');info.push_constant('INT','unlit');info.sampler(1,'FLOAT_2D','baseTexture');info.sampler(0,'FLOAT_2D','lighting');info.fragment_out(0,'VEC4','color')
        info.vertex_source('void main(){gl_Position=mvp*vec4(position,1);sampleUV=luxel;sampleChart=chart;materialUV=baseUV;}')
        info.fragment_source('''
        vec3 fetchLuxel(ivec2 p){
            ivec2 size=ivec2(sampleChart.yz);p=clamp(p,ivec2(0),size-1);
            int index=int(sampleChart.x)+p.y*size.x+p.x;
            return texelFetch(lighting,ivec2(index%atlasWidth,index/atlasWidth),0).rgb;
        }
        void main(){
            ivec2 p=ivec2(floor(sampleUV));vec2 t=fract(sampleUV);
            vec3 c=mix(mix(fetchLuxel(p),fetchLuxel(p+ivec2(1,0)),t.x),
                       mix(fetchLuxel(p+ivec2(0,1)),fetchLuxel(p+ivec2(1,1)),t.x),t.y);
            vec3 albedo=hasTexture!=0?texture(baseTexture,fract(materialUV)).rgb:vec3(1);
            c=max(albedo*(unlit!=0?vec3(1):c)*exposure,vec3(0));
            c=mix(12.92*c,1.055*pow(c,vec3(1.0/2.4))-.055,greaterThan(c,vec3(.0031308)));
            color=vec4(c,1);
        }''')
        return gpu.shader.create_from_info(info)

    def draw_depth(self,view_projection):
        """Use exactly the displayed chart vertices to hide occluded prop work."""
        from .l4d2preview import depth_shader
        if self.depth_shader is None:self.depth_shader=depth_shader()
        old_depth=gpu.state.depth_test_get();old_mask=gpu.state.depth_mask_get()
        try:
            gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True)
            self.depth_shader.bind();self.depth_shader.uniform_float('mvp',view_projection)
            for group in self.groups:group['batch'].draw(self.depth_shader)
        finally:
            gpu.state.depth_test_set(old_depth);gpu.state.depth_mask_set(old_mask)

    def draw(self,view_projection,exposure=0):
        old_depth=gpu.state.depth_test_get();old_mask=gpu.state.depth_mask_get();old_blend=gpu.state.blend_get()
        try:
            gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True);gpu.state.blend_set('NONE')
            shader=self.shader;shader.bind();shader.uniform_float('mvp',view_projection)
            shader.uniform_float('exposure',2**exposure);shader.uniform_int('atlasWidth',self.cache.size[0])
            shader.uniform_sampler('lighting',self.cache.texture)
            for group in self.groups:
                image=group['image']
                shader.uniform_int('hasTexture',int(image is not None));shader.uniform_int('unlit',int(group['unlit']))
                shader.uniform_sampler('baseTexture',gpu.texture.from_image(image) if image else self.cache.texture)
                group['batch'].draw(shader)
        finally:
            gpu.state.depth_test_set(old_depth);gpu.state.depth_mask_set(old_mask);gpu.state.blend_set(old_blend)
