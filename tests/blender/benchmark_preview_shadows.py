"""Synthetic shadow scaling check. GUI factory-startup; never opens user files."""
from pathlib import Path
import sys, time, json, tempfile, traceback, statistics
import bpy, gpu
import numpy as np
from mathutils import Matrix
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hammerless.blender.l4d2preview import Geometry, SceneCache, diagnostic_shader, draw_geometry


def run():
    bpy.ops.mesh.primitive_cube_add()
    mesh = bpy.context.object.data
    mesh.calc_loop_triangles()
    pos = np.array([mesh.vertices[i].co[:] for t in mesh.loop_triangles for i in t.vertices],np.float32)
    normal = np.array([t.normal[:] for t in mesh.loop_triangles for i in range(3)],np.float32)
    cache = SceneCache()
    cache.unit_scale = 1
    cache.lights = [((0,0,-20),(10000,10000,10000),(0,0,1))]
    if '--sky' in sys.argv:
        cache.sky_color = (51.,51.,51.)
    cache.geometry = {1:Geometry(pos,normal)}
    shader = diagnostic_shader(lit=True,shadows=True)
    result = {'gpu':gpu.platform.renderer_get(),'sky_enabled':'--sky' in sys.argv,'scope':'Synthetic repeated cubes, opaque point shadows; synchronous draw plus 1-pixel readback, no materials/GI', 'runs':[]}
    for count in (16,64,256):
        side = int(count**0.5)
        cache.instances = [(1,Matrix.Translation(((i%side-side/2)*2.5,(i//side-side/2)*2.5,0))) for i in range(count)]
        projection = Matrix.Diagonal((1/(side*1.4),1/(side*1.4),0.05,1))
        for width,height in ((640,360),(1280,720)):
            off = gpu.types.GPUOffScreen(width,height,format='RGBA32F')
            samples=[]
            try:
                with off.bind():
                    gpu.state.viewport_set(0,0,width,height)
                    fb = gpu.state.active_framebuffer_get()
                    for frame in range(5):
                        start = time.perf_counter()
                        fb.clear(color=(0,0,0,1),depth=1)
                        draw_geometry(cache,shader,projection,lit=True,shadows=True)
                        pixel = fb.read_color(width//2,height//2,1,1,4,0,'FLOAT')
                        assert pixel[0][0][0]>0.01, 'Benchmark did not shade a lit surface'
                        ms = (time.perf_counter()-start)*1000
                        if frame: samples.append(ms)
            finally:
                off.free()
            result['runs'].append({'instances':count,'resolution':[width,height],
                'median_ms':statistics.median(samples),'max_ms':max(samples),'mesh_bvh_builds':cache.trace.builds})
    return result


try:
    result=run()
except Exception:
    result={'error':traceback.format_exc()}
(Path(tempfile.gettempdir())/('hammerless_preview_sky.json' if '--sky' in sys.argv else 'hammerless_preview_shadows.json')).write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
bpy.ops.wm.quit_blender()
