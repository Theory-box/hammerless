"""Main-thread G-buffer adapter for the asynchronous native preview worker.

Only visible receivers are shaded, but the worker retains all scene blockers.
A changed camera/scene never displays lighting from an older signature.
"""
import numpy as np
import gpu
from gpu_extras.batch import batch_for_shader
from . import previewnative
from .previewhistory import FrameHistory


def _composition():
    info=gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0,'VEC2','position')
    info.sampler(0,'FLOAT_2D','albedoImage')
    info.sampler(1,'FLOAT_2D','lightImage')
    info.sampler(2,'FLOAT_2D','positionImage')
    info.fragment_out(0,'VEC4','color')
    info.vertex_source('void main(){gl_Position=vec4(position,0,1);}')
    info.fragment_source("""void main(){
        ivec2 p=ivec2(gl_FragCoord.xy);
        if(texelFetch(positionImage,p,0).w<0.5){color=vec4(vec3(.0019349845),1);return;}
        vec4 a=texelFetch(albedoImage,p,0);
        vec3 light=texelFetch(lightImage,p,0).rgb;
        color=vec4(a.rgb*(a.a>0.5?vec3(1):light),1);
    }""")
    shader=gpu.shader.create_from_info(info)
    return shader,batch_for_shader(shader,'TRIS',{'position':[(-1,-1),(3,-1),(-1,3)]})


class NativeView:
    def __init__(self,owner):
        self.owner=owner
        self.client=previewnative.get_client()
        self.geometry_token=None;self.lights_token=None
        self.vertices=self.metadata=self.lights=None
        self.signature=None;self.size=None;self.request=None
        self.serial=0;self.completed=0;self.error=''
        self.history=None;self.receiver_shader=None;self.depth_shader=None
        self.compose=None;self.light_texture=None
        self.points=None;self.covered=None

    def prepare(self,cache,resolver,geometry_token,lights_token):
        """Main-thread immutable scene snapshot; no native process is started here."""
        if self.vertices is None or geometry_token!=self.geometry_token:
            self.vertices,self.metadata=previewnative.flatten_scene(cache,resolver)
            self.geometry_token=geometry_token
        if self.lights is None or lights_token!=self.lights_token:
            self.lights=previewnative.pack_lights(cache)
            self.lights_token=lights_token

    def pause(self):
        self.client.pause(self.owner)
        self.request=None

    def close(self):
        self.client.release(self.owner)
        self.request=None
        self.history=None
        self.framebuffer=None
        self.albedo=self.position=self.normal=self.depth=None
        self.light_texture=None
        self.vertices=self.metadata=self.points=self.covered=None

    def _capture(self,cache,matrix,size):
        from .l4d2preview import diagnostic_shader,draw_geometry,depth_shader
        if self.receiver_shader is None:
            self.receiver_shader=diagnostic_shader(lit=True,textured=True,receiver_data=True)
            self.depth_shader=depth_shader()
            self.compose=_composition()
            self.history=FrameHistory()
        if self.size!=size:
            self.albedo=gpu.types.GPUTexture(size,format='RGBA32F')
            self.position=gpu.types.GPUTexture(size,format='RGBA32F')
            self.normal=gpu.types.GPUTexture(size,format='RGBA32F')
            self.depth=gpu.types.GPUTexture(size,format='DEPTH_COMPONENT32F')
            self.framebuffer=gpu.types.GPUFrameBuffer(color_slots=(self.albedo,self.position,self.normal),depth_slot=self.depth)
            self.size=size
        viewport=gpu.state.viewport_get()
        old_mask=gpu.state.depth_mask_get()
        scissor=gpu.state.scissor_get() if hasattr(gpu.state,'scissor_get') else None
        try:
            with self.framebuffer.bind():
                gpu.state.viewport_set(0,0,*size)
                if scissor is not None:gpu.state.scissor_set(0,0,*size)
                gpu.state.depth_mask_set(True)
                self.framebuffer.clear(color=(0,0,0,0),depth=1)
                draw_geometry(cache,self.receiver_shader,matrix,lit=True,textured=True,receiver_data=True)
                position_buffer=self.framebuffer.read_color(0,0,*size,4,1,'FLOAT')
                normal_buffer=self.framebuffer.read_color(0,0,*size,4,2,'FLOAT')
                # Blender 4.4's multidimensional GPUBuffer exports incorrect NumPy
                # strides: asarray silently misreads pixels and frombuffer rejects
                # it as non-contiguous. Verified against direct pixel readback:
                # setting one dimension exposes the actual packed float storage.
                position_buffer.dimensions=(size[0]*size[1]*4,)
                normal_buffer.dimensions=(size[0]*size[1]*4,)
                positions=np.frombuffer(position_buffer,dtype=np.float32,count=size[0]*size[1]*4).reshape(-1,4)
                normals=np.frombuffer(normal_buffer,dtype=np.float32,count=size[0]*size[1]*4).reshape(-1,4)
                self.covered=np.flatnonzero(positions[:,3]>.5)
                self.points=np.empty((len(self.covered),8),dtype=np.float32)
                self.points[:,:4]=positions[self.covered]
                self.points[:,4:]=normals[self.covered]
        finally:
            gpu.state.depth_mask_set(old_mask)
            if scissor is not None:gpu.state.scissor_set(*scissor)
            gpu.state.viewport_set(*viewport)

    def draw(self,cache,matrix,signature,rays,depth,frames,exposure):
        """Return (displayed, pending, status). Caller supplies cheap first-frame fallback."""
        from .l4d2preview import draw_depth
        if self.vertices is None:
            return False,False,'Native scene is not prepared'
        size=tuple(gpu.state.viewport_get()[2:])
        if min(size)<=0:return False,False,''
        if size[0]*size[1]>previewnative.MAX_POINTS:
            return False,False,'Native preview viewport exceeds eight million pixels'
        key=(signature,tuple(float(v) for row in matrix for v in row),size,int(rays),int(depth),float(cache.unit_scale),tuple(cache.sky_color),self.geometry_token,self.lights_token)
        if key!=self.signature:
            self.pause()
            self._capture(cache,matrix,size)
            self.signature=key;self.completed=0;self.error='';self.light_texture=None
        advance=False
        if self.request is not None:
            result=self.client.poll(self.owner,self.request)
            if result is not None:
                self.request=None
                if result.error:self.error=result.error
                else:
                    pixels=np.zeros((size[0]*size[1],4),dtype=np.float32)
                    pixels[self.covered]=result.pixels
                    buffer=gpu.types.Buffer('FLOAT',pixels.size,pixels.reshape(-1))
                    self.light_texture=gpu.types.GPUTexture(size,format='RGBA32F',data=buffer)
                    advance=True
        def restore_depth():
            draw_depth(cache,self.depth_shader,matrix)
        def sample(_frame):
            old_depth=gpu.state.depth_test_get();old_mask=gpu.state.depth_mask_get();old_blend=gpu.state.blend_get()
            try:
                # Populate depth first: a depth-only shader may leave attachment color
                # undefined on some drivers, so composition must overwrite it last.
                gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True);gpu.state.blend_set('NONE')
                restore_depth()
                gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False)
                shader,batch=self.compose;shader.bind()
                shader.uniform_sampler('albedoImage',self.albedo)
                shader.uniform_sampler('lightImage',self.light_texture)
                shader.uniform_sampler('positionImage',self.position)
                batch.draw(shader)
            finally:
                gpu.state.depth_test_set(old_depth);gpu.state.depth_mask_set(old_mask);gpu.state.blend_set(old_blend)
        displayed=self.completed>0 or advance
        if displayed:
            self.completed=self.history.render(key,sample,restore_depth,exposure=exposure,max_frames=max(1,int(frames)),advance=advance)
        if self.request is None and not self.error and self.completed<max(1,int(frames)):
            self.serial+=1;self.request=self.serial
            params=(int(rays),int(depth),self.completed,3,float(cache.unit_scale),*cache.sky_color)
            self.client.submit(self.owner,self.request,self.geometry_token,self.vertices,self.metadata,
                               self.lights_token,self.lights,self.points,params)
        pending=self.request is not None
        status=self.error or f'Native lighting {self.completed}/{max(1,int(frames))}'
        return displayed,pending,status
