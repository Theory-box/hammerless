"""Bounded real-map native surface retention check; never saves the input blend.

Run in a disposable interactive Blender with --factory-startup --python this.py
-- MAP.blend. The caller should enforce a 120-second process deadline.
"""
from pathlib import Path
import json, os, sys, time, traceback
import bpy, gpu, numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hammerless.blender import props, l4d2preview as preview, previewnative
from hammerless.blender.previewnativeview import NativeView
from hammerless.blender.previewnativesurface import NativeSurfaceCache
from hammerless.blender.previewlivecache import LiveSurfaceCache
from hammerless.blender.previewmaterials import BounceMaterials, SurfaceMaterials
from hammerless.blender.ops import game_content
from hammerless.core.compile import find_game_root

OUT = ROOT.parents[1] / 'outputs/l4d2-preview/native-surface-realmap.json'
progress = {}


def checkpoint(**fields):
    progress.update(fields)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(progress, indent=2))
    print(json.dumps(fields), flush=True)


def run():
    started = time.perf_counter()
    props.register()
    path = (Path(sys.argv[sys.argv.index('--') + 1]) if '--' in sys.argv
            else ROOT.parents[1] / 'outputs/c1m1_hotel_with_transition.blend').resolve()
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    sc = bpy.context.scene
    scale = sc.hammerless.units_per_meter
    spawns = [o for o in sc.objects if o.hammerless.classname in
              ('info_player_start', 'info_survivor_position')]
    assert spawns, 'Map requires a player spawn'
    camera = bpy.data.objects.new('Surface test camera', bpy.data.cameras.new('Surface test camera'))
    sc.collection.objects.link(camera)
    camera.location = spawns[0].matrix_world.translation + Vector((0, 0, 64 / scale))
    camera.data.clip_end = 10000
    camera.rotation_euler = Vector((1, 0, 0)).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    scene = preview.SceneCache()
    scene.update(deps)
    scene.update_lights(deps)
    root = find_game_root()
    assert root, 'Game root not found'
    content = game_content(root)
    game = os.path.join(root, 'left4dead2')
    resolver = BounceMaterials(content, game, sc.hammerless.default_material)
    surface = SurfaceMaterials(content, game, sc.hammerless.default_material)
    resolver.resolve(None)
    for geometry in scene.geometry.values():
        for mat in geometry.materials or (None,):
            surface.resolve(mat)
            resolver.resolve(mat)
    scene.surface_materials = surface
    checkpoint(file=str(path), scene_setup_ms=(time.perf_counter() - started) * 1000,
               instances=len(scene.instances), lights=len(scene.lights))
    ts = time.perf_counter()
    live = LiveSurfaceCache()
    live.update(bpy.context, deps, scene, 0)
    assert live.faces and live.positions is not None, 'No supported map brush charts'
    checkpoint(chart_setup_ms=(time.perf_counter() - ts) * 1000,
               charts=len(live.charts), samples=len(live.positions), warnings=live.warnings[:10])
    width, height = 160, 90
    native = NativeView(owner='native-surface-realmap')
    cache = NativeSurfaceCache()
    off = None
    try:
        native.stage(scene, resolver, ('realmap', 0), scene.light_signature)
        off = gpu.types.GPUOffScreen(width, height, format='RGBA32F')
        matrix = camera.calc_matrix_camera(deps, x=width, y=height) @ camera.matrix_world.inverted()
        # Every bound lies outside x<=w. Consume the one pending real result
        # with no candidates, preventing a second native submission.
        empty_frustum = Matrix(((0, 0, 0, 2), (0, 0, 0, 0),
                                (0, 0, 0, 0), (0, 0, 0, 1)))
        args = dict(rays=8, bounces=1, frames=1)
        with off.bind():
            gpu.state.viewport_set(0, 0, width, height)
            ts = time.perf_counter()
            pending, status = cache.step(native, live, scene, 'realmap', view_projection=matrix, **args)
            assert pending and cache.request is not None, status
            requested = len(cache.request[1])
            assert 0 < requested <= 8192
            checkpoint(first_submit_ms=(time.perf_counter() - ts) * 1000,
                       requested_samples=requested, native_triangles=len(native.vertices))
            deadline = time.perf_counter() + 30
            while cache.evaluations == 0 and time.perf_counter() < deadline:
                pending, status = cache.step(native, live, scene, 'realmap',
                                             view_projection=empty_frustum, **args)
                assert not cache.error, cache.error
                if cache.evaluations == 0:
                    time.sleep(.005)
            assert cache.evaluations == requested, 'Native batch did not complete within 30 seconds'
            assert cache.request is None and native.serial == 1, 'More than one native batch submitted'
            checkpoint(first_result_ms=(time.perf_counter() - ts) * 1000,
                       evaluations=cache.evaluations, uploads=cache.uploads,
                       populated_samples=int(np.count_nonzero(cache.values[:, 3])))
            cache.pause(native)
            retained = cache.values.copy()
            counts = cache.counts.copy()
            texture = cache.texture
            evaluations = cache.evaluations
            shader = preview.diagnostic_shader(lit=True, textured=True, display=True)
            rows = []
            for direction in (Vector((1, 0, 0)), Vector((0, 1, 0))):
                camera.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
                bpy.context.view_layer.update()
                matrix = camera.calc_matrix_camera(deps, x=width, y=height) @ camera.matrix_world.inverted()
                ts = time.perf_counter()
                pending, status = cache.step(native, live, scene, 'realmap',
                                             view_projection=matrix, paused=True, **args)
                assert not pending and cache.request is None
                assert cache.evaluations == evaluations and cache.texture is texture
                assert np.array_equal(cache.values, retained) and np.array_equal(cache.counts, counts)
                fb = gpu.state.active_framebuffer_get()
                fb.clear(color=(.025, .025, .025, 1), depth=1)
                preview.draw_geometry(scene, shader, matrix, lit=True, textured=True, display=True,
                    exclude_keys=cache.exclude_keys,raster_geometry=cache.remaining)
                cache.draw(matrix)
                pixels = np.asarray(fb.read_color(0, 0, width, height, 4, 0, 'FLOAT').to_list())
                assert np.isfinite(pixels).all(), 'Nonfinite retained display'
                rows.append(dict(direction=list(direction), draw_readback_ms=(time.perf_counter() - ts) * 1000,
                                 finite=True, atlas_retained=True, evaluations=cache.evaluations))
            checkpoint(ok=True, views=rows, total_ms=(time.perf_counter() - started) * 1000,
                       scope='One actual native batch, at most 8192 receivers; full map blocker scene; two paused retained displays. No full-map convergence, parity comparison, or visual artifact verdict.')
    finally:
        native.close()
        previewnative.shutdown()
        if off is not None:
            off.free()


try:
    run()
except Exception:
    previewnative.shutdown()
    checkpoint(ok=False, traceback=traceback.format_exc())
bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=.1)
