"""GPU regression: decals must not inherit viewport culling. Run non-background."""
import pathlib
import sys
import tempfile
import traceback
import types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
import bpy


def run():
    result = pathlib.Path(tempfile.gettempdir()) / 'hammerless_overlay_culling_test.txt'
    original_bpy = original_busy = None
    try:
        import hammerless
        import gpu
        import numpy as np
        from mathutils import Matrix
        from gpu_extras.batch import batch_for_shader
        hammerless.register()
        from hammerless.blender import overlays, particlepreview
        original_bpy, original_busy = overlays.bpy, particlepreview.work_busy
        context = types.SimpleNamespace(scene=bpy.context.scene, view_layer=bpy.context.view_layer,
                                        region_data=types.SimpleNamespace(perspective_matrix=Matrix.Identity(4)))
        overlays.bpy = types.SimpleNamespace(context=context)
        particlepreview.work_busy = lambda: False
        overlays._state.update(dirty=False, view=(context.scene.as_pointer(), context.view_layer.name))
        overlays._state.pop('check_needed', None)
        offscreen = gpu.types.GPUOffScreen(32, 32)
        counts = {}
        with offscreen.bind():
            shader = overlays.shader()
            batch = batch_for_shader(shader, 'TRIS', {
                'pos': [(-1,-1,0),(1,-1,0),(1,1,0),(-1,-1,0),(1,1,0),(-1,1,0)],
                'uv': [(0,0),(1,0),(1,1),(0,0),(1,1),(0,1)]})
            texture = gpu.types.GPUTexture((1,1), format='RGBA16F',
                data=gpu.types.Buffer('FLOAT', 4, [.1,.02,.01,1]))
            fb = gpu.state.active_framebuffer_get()
            for blend, modulate in [('ALPHA', True), ('ALPHA', False)]:
                overlays._state['batches'] = [(batch, texture, blend, modulate, 0)]
                for culling in ('NONE', 'FRONT', 'BACK'):
                    fb.clear(color=(.6,.6,.6,1), depth=1)
                    gpu.state.face_culling_set(culling)
                    overlays.draw()
                    pixels = np.array(fb.read_color(0,0,32,32,4,0,'FLOAT').to_list(), dtype=np.float32)
                    counts[modulate,culling] = int(np.sum(pixels[:,:,:3].min(axis=2) < .4))
            assert min(counts.values()) > 500 and len(set(counts.values())) == 1, counts
            # Real POST_VIEW uses transparent overlays, not the opaque scene buffer.
            # Source modulation must survive there and composite as an alpha decal.
            for modulate in (True, False):
                overlays._state['batches'] = [(batch, texture, 'ALPHA', modulate, 0)]
                fb.clear(color=(0,0,0,0), depth=1)
                overlays.draw()
                pixels = np.array(fb.read_color(0,0,32,32,4,0,'FLOAT').to_list(), dtype=np.float32)
                assert np.sum(pixels[:,:,3] > .1) > 500, 'Decal vanished into transparent overlay'
            # Neutral 50% gray is transparent in a modulation material, not a box.
            neutral = gpu.types.GPUTexture((1,1), format='RGBA16F',
                data=gpu.types.Buffer('FLOAT', 4, [.5,.5,.5,1]))
            overlays._state['batches'] = [(batch, neutral, 'ALPHA', True, 0)]
            fb.clear(color=(0,0,0,0), depth=1)
            overlays.draw()
            pixels = np.array(fb.read_color(0,0,32,32,4,0,'FLOAT').to_list(), dtype=np.float32)
            assert pixels[:,:,3].max() < .01, 'Neutral modulation painted a rectangle'
            overlays._state['batches'] = [(batch, texture, 'ALPHA', True, 0)]
            # Depth testing must still hide decals behind other geometry.
            fb.clear(color=(.6,.6,.6,1), depth=.1)
            overlays.draw()
            pixels = np.array(fb.read_color(0,0,32,32,4,0,'FLOAT').to_list(), dtype=np.float32)
            assert pixels[:,:,:3].min() > .5, 'Decal ignored occluding geometry'
        offscreen.free()
        result.write_text('PASS ' + str(counts))
    except Exception:
        result.write_text(traceback.format_exc())
    finally:
        if original_bpy is not None:
            overlays.bpy = original_bpy
            particlepreview.work_busy = original_busy
        bpy.ops.wm.quit_blender()

bpy.app.timers.register(run, first_interval=1)


