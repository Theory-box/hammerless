"""Run with Blender --factory-startup --python (requires a GPU window, not -b).

Uses synthetic pixels: no game installation or redistributed game assets needed.
Exits nonzero on failure and never loads or saves a blend file.
"""
import sys
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import gpu
import numpy as np
from mathutils import Matrix
from gpu_extras.batch import batch_for_shader


def run():
    offscreen = None
    try:
        from hammerless.blender.particlepreview import shader
        pixels = np.zeros((8, 8, 4), dtype=np.float32)
        pixels[:, :4] = (1, 0, 0, 1)
        pixels[:, 4:] = (0, 1, 0, 1)
        texture = gpu.types.GPUTexture((8, 8), format='RGBA32F',
            data=gpu.types.Buffer('FLOAT', pixels.size, pixels.ravel()))
        # Buffer.to_list avoids incorrect NumPy buffer strides on Blender 4.4.
        uploaded = np.array(texture.read().to_list(), dtype=np.float32).reshape(pixels.shape)
        np.testing.assert_allclose(uploaded, pixels)
        offscreen = gpu.types.GPUOffScreen(32, 32)
        sh = shader()
        with offscreen.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=(0, 0, 0, 0))
            gpu.state.viewport_set(0, 0, 32, 32)
            gpu.state.blend_set('NONE')
            gpu.state.depth_test_set('NONE')
            sh.bind()
            sh.uniform_float('mvp', Matrix.Identity(4))
            sh.uniform_sampler('sprite', texture)
            batch_for_shader(sh, 'TRIS', {
                'pos': [(-1,-1,0),(1,-1,0),(1,1,0),(-1,-1,0),(1,1,0),(-1,1,0)],
                'uv': [(0,0),(1,0),(1,1),(0,0),(1,1),(0,1)],
                'tint': [(0.5,0.5,0.5,1)] * 6,
            }).draw(sh)
            result = np.array(fb.read_color(0,0,32,32,4,0,'FLOAT').to_list(),
                              dtype=np.float32).reshape(32,32,4)
            np.testing.assert_allclose(result[16,4], (0.5,0,0,1), atol=0.005)
            np.testing.assert_allclose(result[16,27], (0,0.5,0,1), atol=0.005)
            from hammerless.core.particlegeometry import build_geometry
            from hammerless.core.particlepreview import Definition, Simulation, CATEGORIES
            sim=Simulation(Definition('gpu','synthetic',{}, {c:[] for c in CATEGORIES}))
            sim.spawn(1);sim.position[:]=[.75,0,0];sim.previous_position[:]=[-.25,0,0]
            sim.radius[:]=.1;sim.trail[:]=1/60
            geometry,gaps=build_geometry(sim,{'functionName':'render_sprite_trail','constrain radius to length':False},
                {},np.eye(4),[1,0,0],[0,1,0],[0,0,10])
            assert not gaps and len(geometry['pos'])==6
            fb.clear(color=(0,0,0,0))
            batch_for_shader(sh,'TRIS',geometry).draw(sh)
            result=np.array(fb.read_color(0,0,32,32,4,0,'FLOAT').to_list(),dtype=np.float32).reshape(32,32,4)
            assert result[16,20,3]>.9, 'trail missing from expected screen region'
            assert result[2,2,3]<.01, 'trail escaped its bounds'
        print('PASS particle GPU upload, shader, UV orientation, tint and production trail geometry', flush=True)
    except Exception:
        traceback.print_exc()
        # Blender can otherwise exit successfully after a Python error.
        import os
        os._exit(1)
    finally:
        if offscreen is not None:
            offscreen.free()
    bpy.ops.wm.quit_blender()


bpy.app.timers.register(run, first_interval=1)
