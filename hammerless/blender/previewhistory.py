"""GPU-only linear frame averaging. Camera/scene signatures invalidate history."""
import gpu
from gpu_extras.batch import batch_for_shader


def fullscreen(display):
    info=gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0,'VEC2','position')
    interface=gpu.types.GPUStageInterfaceInfo('hl_history_uv')
    interface.smooth('VEC2','uv');info.vertex_out(interface)
    info.sampler(0,'FLOAT_2D','image');info.fragment_out(0,'VEC4','color')
    info.vertex_source('void main(){uv=position*.5+.5;gl_Position=vec4(position,0,1);}')
    if display:
        info.push_constant('FLOAT','exposure')
        info.fragment_source('''void main(){
            vec3 c=max(texture(image,uv).rgb*exposure,vec3(0));
            c=mix(12.92*c,1.055*pow(c,vec3(1.0/2.4))-.055,greaterThan(c,vec3(.0031308)));
            color=vec4(c,1);
        }''')
    else:
        info.push_constant('FLOAT','weight')
        info.fragment_source('void main(){color=vec4(texture(image,uv).rgb,weight);}')
    shader=gpu.shader.create_from_info(info)
    return shader,batch_for_shader(shader,'TRIS',{'position':[(-1,-1),(3,-1),(-1,3)]})


class FrameHistory:
    def __init__(self):
        self.size=None;self.signature=None;self.frames=0
        self.average_shader,self.average_batch=fullscreen(False)
        self.display_shader,self.display_batch=fullscreen(True)

    def render(self,signature,draw_sample,draw_depth,exposure=0.,max_frames=64):
        """draw_sample(frame_index) writes linear RGB and depth to the bound target.

        draw_depth restores geometry depth without color outputs (Blender 4.4
        cannot declare arbitrary fragment depth writes through CreateInfo).
        Always displays into the caller's framebuffer. No CPU readbacks. Stops
        tracing at max_frames until signature or dimensions change.
        """
        viewport=gpu.state.viewport_get();size=tuple(viewport[2:])
        old_depth=gpu.state.depth_test_get();old_mask=gpu.state.depth_mask_get()
        old_blend=gpu.state.blend_get()
        if size!=self.size:
            self.sample_color=gpu.types.GPUTexture(size,format='RGBA32F')
            self.depth=gpu.types.GPUTexture(size,format='DEPTH_COMPONENT32F')
            self.history=gpu.types.GPUTexture(size,format='RGBA32F')
            self.sample_fb=gpu.types.GPUFrameBuffer(color_slots=self.sample_color,depth_slot=self.depth)
            self.history_fb=gpu.types.GPUFrameBuffer(color_slots=self.history)
            self.size=size;self.signature=None
        if signature!=self.signature:
            self.frames=0;self.signature=signature
        try:
            if self.frames<max(1,max_frames):
                with self.sample_fb.bind():
                    gpu.state.viewport_set(0,0,*size)
                    self.sample_fb.clear(color=(.0019349845,.0019349845,.0019349845,1),depth=1)
                    draw_sample(self.frames)
                with self.history_fb.bind():
                    gpu.state.viewport_set(0,0,*size)
                    gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False)
                    gpu.state.blend_set('ALPHA')
                    if self.frames==0:self.history_fb.clear(color=(0,0,0,0))
                    shader=self.average_shader;shader.bind()
                    shader.uniform_sampler('image',self.sample_color)
                    shader.uniform_float('weight',1.0/(self.frames+1))
                    self.average_batch.draw(shader)
                self.frames+=1
            gpu.state.viewport_set(*viewport)
            gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False);gpu.state.blend_set('NONE')
            shader=self.display_shader;shader.bind()
            shader.uniform_sampler('image',self.history)
            shader.uniform_float('exposure',2.0**exposure)
            self.display_batch.draw(shader)
            gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True)
            gpu.state.active_framebuffer_get().clear(depth=1)
            draw_depth()
        finally:
            gpu.state.viewport_set(*viewport);gpu.state.depth_test_set(old_depth)
            gpu.state.depth_mask_set(old_mask);gpu.state.blend_set(old_blend)
        return self.frames
