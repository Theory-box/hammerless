"""Experimental camera-independent surface irradiance cache.

Lighting remains linear irradiance / 255, separate from material color. Explicit
readback can feed export experiments; ordinary refinement stays on the GPU.
Caller supplies surface samples and invalidation signature, never a camera.
"""
import math
import numpy as np
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix
from .l4d2preview import diagnostic_shader, draw_geometry
from .previewhistory import fullscreen

class SurfaceLightCache:
    def __init__(self,positions,normals):
        self.positions=np.asarray(positions,dtype=np.float32).reshape(-1,3)
        self.normals=np.asarray(normals,dtype=np.float32).reshape(-1,3)
        if self.positions.shape!=self.normals.shape or not len(self.positions):
            raise ValueError('Surface cache requires matching nonempty positions and normals')
        if not np.isfinite(self.positions).all() or not np.isfinite(self.normals).all():
            raise ValueError('Surface samples must be finite')
        self.count=len(self.positions)
        width=min(2048,max(1,math.ceil(math.sqrt(self.count))))
        height=math.ceil(self.count/width)
        if height>8192:raise ValueError('Surface sample budget exceeded')
        self.size=(width,height)
        ids=np.arange(self.count)
        clips=np.column_stack(((ids%width+.5)/width*2-1,(ids//width+.5)/height*2-1)).astype(np.float32)
        self.shader=diagnostic_shader(lit=True,shadows=True,bounce=True,surface_samples=True)
        self.batch=batch_for_shader(self.shader,'POINTS',{'position':self.positions,'normal':self.normals,
            'faceNormal':self.normals,'sampleClip':clips})
        self.sample=gpu.types.GPUTexture(self.size,format='RGBA32F')
        self.texture=gpu.types.GPUTexture(self.size,format='RGBA32F')
        self.depth=gpu.types.GPUTexture(self.size,format='DEPTH_COMPONENT32F')
        self.sample_fb=gpu.types.GPUFrameBuffer(color_slots=self.sample,depth_slot=self.depth)
        self.result_fb=gpu.types.GPUFrameBuffer(color_slots=self.texture)
        self.average_shader,self.average_batch=fullscreen(False)
        self.signature=None;self.frames=0;self.evaluations=0

    def refine(self,scene,signature,*,max_frames=72,rays=8,bounces=1):
        signature=(signature,rays,bounces)
        if signature!=self.signature:self.signature=signature;self.frames=0
        if self.frames>=max(1,max_frames):return False
        viewport=gpu.state.viewport_get()
        depth=gpu.state.depth_test_get();mask=gpu.state.depth_mask_get();blend=gpu.state.blend_get()
        try:
            with self.sample_fb.bind():
                gpu.state.viewport_set(0,0,*self.size)
                self.sample_fb.clear(color=(0,0,0,0),depth=1)
                draw_geometry(scene,self.shader,Matrix.Identity(4),lit=True,shadows=True,
                    bounce_samples=rays,bounce_depth=bounces,bounce_seed=self.frames,
                    sampled_bounce_sky=True,sampled_bounce_area=True,
                    primary_sky_index=self.frames*9,primary_sky_count=9,
                    primary_area_phase=self.frames%8,lighting_samples=self.batch)
            with self.result_fb.bind():
                gpu.state.viewport_set(0,0,*self.size)
                gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False);gpu.state.blend_set('ALPHA')
                if self.frames==0:self.result_fb.clear(color=(0,0,0,0))
                shader=self.average_shader;shader.bind();shader.uniform_sampler('image',self.sample)
                shader.uniform_float('weight',1/(self.frames+1));self.average_batch.draw(shader)
            self.frames+=1;self.evaluations+=self.count
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
    def __init__(self,cache,faces,charts,material_binding=None,*,units_per_meter=1):
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
            group=groups.setdefault(key,{'image':image,'unlit':unlit,'positions':[],'coords':[],'metadata':[],'uv':[]})
            group['positions'].extend(p/units_per_meter);group['coords'].extend(face.to_luxel(p));group['uv'].extend(uv)
            group['metadata'].extend([(chart['start'],chart['width'],chart['height'])]*len(p))
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
        self.shader=gpu.shader.create_from_info(info)
        self.groups=[]
        for group in groups.values():
            group['batch']=batch_for_shader(self.shader,'TRIS',{
                'position':np.asarray(group['positions'],dtype=np.float32),
                'luxel':np.asarray(group['coords'],dtype=np.float32),
                'chart':np.asarray(group['metadata'],dtype=np.float32),
                'baseUV':np.asarray(group['uv'],dtype=np.float32)})
            self.groups.append(group)

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
