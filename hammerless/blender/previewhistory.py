"""GPU-only linear frame averaging. Camera/scene signatures invalidate history."""
import operator
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


def cached_depth_display():
    """OpenGL compatibility path for Blender 4.4's missing depth_write binding."""
    if gpu.platform.backend_type_get() != 'OPENGL':
        return None
    shader=gpu.types.GPUShader(
        'in vec2 position; out vec2 uv; void main(){uv=position*.5+.5;gl_Position=vec4(position,0,1);}',
        'uniform sampler2D image; uniform sampler2D sceneDepth; uniform float exposure;\nin vec2 uv; out vec4 color;\nvoid main(){vec3 c=max(texture(image,uv).rgb*exposure,vec3(0));\nc=mix(12.92*c,1.055*pow(c,vec3(1.0/2.4))-.055,greaterThan(c,vec3(.0031308)));\ncolor=vec4(c,1);gl_FragDepth=texture(sceneDepth,uv).r;}')
    return shader,batch_for_shader(shader,'TRIS',{'position':[(-1,-1),(3,-1),(-1,3)]})


class FrameHistory:
    def __init__(self):
        self.size=None;self.signature=None;self.frames=0;self.batch_index=0
        self.average_shader,self.average_batch=fullscreen(False)
        self.display_shader,self.display_batch=fullscreen(True)
        try:
            self.cached_display=cached_depth_display()
        except (AttributeError, TypeError, RuntimeError):
            # Legacy GLSL construction is absent on some newer/back-end builds.
            self.cached_display=None

    def render(self,signature,draw_sample,draw_depth,exposure=0.,max_frames=64,sample_weights=None):
        """draw_sample(frame_index) writes linear RGB and depth to the bound target.

        OpenGL restores cached depth with a full-screen shader. Other backends
        use draw_depth to restore geometry depth without color outputs.
        Always displays into the caller's framebuffer. No CPU readbacks. Stops
        tracing at max_frames until signature or dimensions change.

        Optional positive integer sample_weights splits each logical frame into
        draw_sample(frame_index, batch_index) calls. Weighted means preserve the
        complete logical-frame result, including uneven final slices. A reduced
        max_frames finishes any already-started frame; exposure never resets it.
        """
        sliced=sample_weights is not None
        weights=tuple(operator.index(w) for w in sample_weights) if sliced else (1,)
        if not weights or any(w<=0 for w in weights):
            raise ValueError('Sample weights must be a nonempty sequence of positive integers')
        total_weight=sum(weights)
        signature=(signature,weights)
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
            self.frames=0;self.batch_index=0;self.signature=signature
        try:
            if self.frames<max(1,max_frames) or self.batch_index:
                with self.sample_fb.bind():
                    gpu.state.viewport_set(0,0,*size)
                    self.sample_fb.clear(color=(.0019349845,.0019349845,.0019349845,1),depth=1)
                    if sliced:draw_sample(self.frames,self.batch_index)
                    else:draw_sample(self.frames)
                with self.history_fb.bind():
                    gpu.state.viewport_set(0,0,*size)
                    gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False)
                    gpu.state.blend_set('ALPHA')
                    if self.frames==0 and self.batch_index==0:self.history_fb.clear(color=(0,0,0,0))
                    shader=self.average_shader;shader.bind()
                    shader.uniform_sampler('image',self.sample_color)
                    completed=self.frames*total_weight+sum(weights[:self.batch_index])
                    weight=weights[self.batch_index]
                    shader.uniform_float('weight',weight/(completed+weight))
                    self.average_batch.draw(shader)
                self.batch_index+=1
                if self.batch_index==len(weights):
                    self.batch_index=0;self.frames+=1
            gpu.state.viewport_set(*viewport)
            gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False);gpu.state.blend_set('NONE')
            if self.cached_display is not None:
                gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True)
                gpu.state.active_framebuffer_get().clear(depth=1)
                shader,batch=self.cached_display;shader.bind()
                shader.uniform_sampler('sceneDepth',self.depth)
            else:
                shader,batch=self.display_shader,self.display_batch;shader.bind()
            shader.uniform_sampler('image',self.history)
            shader.uniform_float('exposure',2.0**exposure)
            batch.draw(shader)
            if self.cached_display is None:
                gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True)
                gpu.state.active_framebuffer_get().clear(depth=1)
                draw_depth()
        finally:
            gpu.state.viewport_set(*viewport);gpu.state.depth_test_set(old_depth)
            gpu.state.depth_mask_set(old_mask);gpu.state.blend_set(old_blend)
        return self.frames
