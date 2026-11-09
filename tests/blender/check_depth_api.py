from pathlib import Path
import bpy,gpu,json
p=Path(__file__).resolve().parents[4]/'outputs/l4d2-preview/depth-api.json'
r={'depth_write':hasattr(gpu.types.GPUShaderCreateInfo,'depth_write'),'framebuffer_methods':[x for x in dir(gpu.types.GPUFrameBuffer) if not x.startswith('_')]}
p.write_text(json.dumps(r));print(r,flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
