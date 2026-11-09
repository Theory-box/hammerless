"""Bounded Blender upload/readback benchmark; no user scene or preferences touched.

Run in a separate factory-startup GUI process; background mode has no GPU context.
This measures the Blender half of a possible host-memory bridge, not Vulkan
interop, ray tracing, frame rate, or asynchronous overlap.
"""
import json
from pathlib import Path
import statistics
import tempfile
import time
import traceback

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader


def run():
    info = gpu.types.GPUShaderCreateInfo()
    iface = gpu.types.GPUStageInterfaceInfo('transfer_uv')
    iface.smooth('VEC2', 'uv')
    info.vertex_in(0, 'VEC2', 'position')
    info.vertex_out(iface)
    info.sampler(0, 'FLOAT_2D', 'sourceImage')
    info.fragment_out(0, 'VEC4', 'color')
    info.vertex_source('void main(){uv=position*0.5+0.5;gl_Position=vec4(position,0,1);}')
    info.fragment_source('void main(){color=texture(sourceImage,uv);}')
    shader = gpu.shader.create_from_info(info)
    batch = batch_for_shader(shader, 'TRIS', {'position': [(-1,-1),(3,-1),(-1,3)]})
    result = {'gpu': gpu.platform.renderer_get(), 'backend': gpu.platform.backend_type_get(),
              'blender': bpy.app.version_string, 'scope': __doc__, 'measurements': []}
    for width, height in [(640,360), (1280,720), (1920,1080)]:
        data = np.full(width*height*4, 0.25, dtype=np.float32)
        buffer = gpu.types.Buffer('FLOAT', len(data), data)
        offscreen = gpu.types.GPUOffScreen(width,height,format='RGBA32F')
        timings = []
        try:
            for iteration in range(9):
                start = time.perf_counter()
                texture = gpu.types.GPUTexture((width,height),format='RGBA32F',data=buffer)
                uploaded = time.perf_counter()
                with offscreen.bind():
                    gpu.state.viewport_set(0,0,width,height)
                    gpu.state.depth_test_set('NONE')
                    gpu.state.blend_set('NONE')
                    shader.bind()
                    shader.uniform_sampler('sourceImage',texture)
                    batch.draw(shader)
                    submitted = time.perf_counter()
                    pixels = gpu.state.active_framebuffer_get().read_color(0,0,width,height,4,0,'FLOAT')
                    finished = time.perf_counter()
                    assert abs(pixels[0][0][0]-0.25)<1e-6
                if iteration:
                    timings.append([(uploaded-start)*1000,(submitted-uploaded)*1000,
                                    (finished-submitted)*1000,(finished-start)*1000])
                del texture, pixels
        finally:
            offscreen.free()
        result['measurements'].append({'size':[width,height], 'bytes_each_direction':data.nbytes,
            'samples':len(timings), 'median_ms':dict(zip(
                ['upload_submission','draw_submission','blocking_readback','total_roundtrip'],
                [statistics.median(row[i] for row in timings) for i in range(4)])),
            'max_total_ms':max(row[3] for row in timings)})
    return result


try:
    result = run()
except Exception:
    result = {'error':traceback.format_exc()}
out = Path(tempfile.gettempdir()) / 'hammerless_preview_transfer.json'
out.write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
bpy.ops.wm.quit_blender()
