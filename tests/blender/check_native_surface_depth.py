"""Real GPU test of current geometry depth versus retained chart geometry."""
from pathlib import Path
import sys, json, traceback
import bpy, gpu, numpy as np
from mathutils import Vector
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from hammerless.blender import props, vmfimport
from hammerless.blender.l4d2preview import SceneCache, diagnostic_shader, draw_geometry
from hammerless.blender.previewlivecache import LiveSurfaceCache
from hammerless.blender.previewnativesurface import NativeSurfaceCache
from hammerless.core.geometry import box_brush
from hammerless.core.vmf import VMFWriter, Block
from hammerless.core.previewlighting import PointLight
OUT = ROOT.parents[1] / 'outputs/l4d2-preview/native-surface-depth.json'


class White:
    unlit = {}; path_unlit = {}; materials = {}; warnings = []
    def resolve(self, material): return None, (512, 512)
    def resolve_path(self, path): return None, (512, 512)


def run():
    props.register()
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    scene = bpy.context.scene
    writer = VMFWriter(); world = Block('world')
    world.kv('id', writer.new_id()); world.kv('classname', 'worldspawn')
    world.add(writer.solid(box_brush((-64, -64, -64), (64, 64, 64), 'test/white')))
    lines = []; world.write(lines)
    vmfimport.import_text(bpy.context, ''.join(lines), 'depth-check.vmf')
    obj = next(o for o in scene.objects if o.get(vmfimport.KIND) == 'solid')
    obj.rotation_euler = (.213, .371, .419); obj.scale = (1.31, .73, 1.17)
    bpy.context.view_layer.update(); deps = bpy.context.evaluated_depsgraph_get()
    cache = SceneCache(); cache.update(deps); cache.update_lights(deps); cache.surface_materials = White()
    cache.unit_scale = scene.hammerless.units_per_meter
    live = LiveSurfaceCache(); live.update(bpy.context, deps, cache, 0)
    assert len(live.faces) == 6, (len(live.faces), live.warnings)
    native = NativeSurfaceCache(); native.sync(live)
    native.values[:, :3] = 0; native.values[:, 3] = 1; native._upload(); native._refresh_display(live)
    assert obj.as_pointer() in native.exclude_keys, 'Ready brush still drawn by direct path'
    light = PointLight.from_keys({'_light': '255 255 255 1000', '_quadratic_attn': '1'})
    cache.lights = [((4., -6., 5.), light.intensity, light.attenuation)]
    cache.sky_color = (0., 0., 0.)
    bpy.ops.object.camera_add(location=(4, -6, 3))
    camera = bpy.context.object
    camera.rotation_euler = (-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'ORTHO'; camera.data.ortho_scale = 5.2
    bpy.context.view_layer.update()
    matrix = camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(), x=256, y=256) @ camera.matrix_world.inverted()
    shader = diagnostic_shader(lit=True, textured=True, display=True)
    off = gpu.types.GPUOffScreen(256, 256, format='RGBA32F')
    original_instances = cache.instances
    def render(direct=True, atlas=True, instances=None):
        cache.instances = original_instances if instances is None else instances
        with off.bind():
            gpu.state.viewport_set(0, 0, 256, 256)
            fb = gpu.state.active_framebuffer_get(); fb.clear(color=(.25, .25, .25, 1), depth=1)
            if direct:
                draw_geometry(cache, shader, matrix, lit=True, textured=True, display=True,
                    exclude_keys=native.exclude_keys if atlas else (),
                    raster_geometry=native.remaining if atlas else None)
            if atlas: native.draw(matrix, 0)
            color = np.asarray(fb.read_color(0, 0, 256, 256, 4, 0, 'FLOAT').to_list(), dtype=np.float32)
            depth = np.asarray(fb.read_depth(0, 0, 256, 256).to_list(), dtype=np.float32)
            return color, depth
    try:
        only, only_depth = render(direct=False)
        direct, _ = render(atlas=False)
        both, both_depth = render()
        mask = only_depth < .999
        interior = mask.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            interior &= np.roll(mask, (dy, dx), (0, 1))
        assert interior.sum() > 1000, int(interior.sum())
        assert np.max(direct[interior, :3]) > .01, 'Direct reference must be visibly lit'
        leakage = np.max(abs(both[interior, :3] - only[interior, :3]), axis=1)
        leak_pixels = int(np.count_nonzero(leakage > 1e-4))
        # A foreground cube is not a cached receiver and must survive the overlay.
        bpy.ops.mesh.primitive_cube_add(size=.6, location=(2, -3, 1.5))
        foreground = bpy.context.object; foreground_key = foreground.as_pointer()
        bpy.context.view_layer.update(); cache.update(bpy.context.evaluated_depsgraph_get())
        cache.surface_materials = White()
        original_instances = cache.instances
        prop_instances = [v for v in original_instances if v[0] == foreground_key]
        assert prop_instances
        prop_only, prop_depth = render(atlas=False, instances=prop_instances)
        with_prop, _ = render()
        prop_mask = (prop_depth < .999) & (prop_depth < only_depth - 1e-5)
        assert prop_mask.sum() > 100, int(prop_mask.sum())
        prop_error = float(np.max(abs(with_prop[prop_mask, :3] - prop_only[prop_mask, :3])))
        result = {'ok': leak_pixels == 0 and prop_error < 1e-5,
            'cached_faces': len(live.faces), 'interior_pixels': int(interior.sum()),
            'direct_leak_pixels': leak_pixels, 'max_direct_leak': float(leakage.max()),
            'foreground_pixels': int(prop_mask.sum()), 'foreground_max_error': prop_error,
            'transform': 'rotated and nonuniformly scaled', 'scope': 'Raster depth parity; no native worker'}
        if not result['ok']:
            image = bpy.data.images.new('Native surface depth diagnostic', width=256, height=256, alpha=True, float_buffer=True)
            image.pixels.foreach_set(both.reshape(-1)); image.filepath_raw = str(OUT.with_suffix('.png'))
            image.file_format = 'PNG'; image.save()
        return result
    finally:
        off.free()


try: result = run()
except Exception: result = {'ok': False, 'traceback': traceback.format_exc()}
OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=.1)
