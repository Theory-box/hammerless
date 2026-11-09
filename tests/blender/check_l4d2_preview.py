"""Run with Blender --factory-startup --python this_file. No user file touched."""
from pathlib import Path
import sys
import traceback
import json
import tempfile

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hammerless.blender import l4d2preview as preview

OUT = Path(tempfile.gettempdir()) / 'hammerless_l4d2_preview_result.json'


def check():
    from hammerless.blender import props
    props.register()
    bpy.context.scene.hammerless.auto_sun = False
    bpy.context.scene.hammerless.ambient_brightness = 0
    preview.register()
    bpy.context.scene.render.engine = preview.HAMMERLESS_L4D2Preview.bl_idname
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add()
    cube = bpy.context.object
    cache = preview.SceneCache()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    cache.update(depsgraph)
    assert len(cache.instances) == 1
    geom = next(iter(cache.geometry.values()))
    assert len(geom.positions) == 36
    def on_update(scene, graph):
        cache.update(graph)
    bpy.app.handlers.depsgraph_update_post.append(on_update)
    before = cache.extractions
    cube.location.x = 2
    depsgraph.update()
    cache.update(depsgraph)
    assert cache.extractions == before, 'Transform re-extracted geometry'
    assert cache.instances[0][1].translation.x == 2
    cube.data.vertices[0].co.z += 0.5
    cube.data.update()
    depsgraph.update()
    cache.update(depsgraph)
    assert cache.extractions == before + 1, 'Mesh edit not invalidated'
    assert next(iter(cache.geometry.values())) is not geom
    bpy.ops.object.light_add(type='POINT', location=(0, 0, 3))
    lamp = bpy.context.object
    lamp.data.energy = 100
    depsgraph.update()
    cache.update_lights(depsgraph)
    assert len(cache.lights) == 1, cache.warnings
    assert cache.lights[0][2] == (0, 0, 1), 'Export falloff default lost'
    first_intensity = cache.lights[0][1][0]
    lamp.data.energy = 200
    depsgraph.update()
    cache.update_lights(depsgraph)
    assert abs(cache.lights[0][1][0] / first_intensity - 2) < 0.01
    bpy.data.objects.remove(lamp, do_unlink=True)
    depsgraph.update()
    cache.update_lights(depsgraph)
    assert not cache.lights
    bpy.context.scene.hammerless.auto_sun = True
    depsgraph.update()
    cache.update_lights(depsgraph)
    assert len(cache.lights)==1 and cache.lights[0][3][3]==2, 'Automatic sun missing'
    bpy.context.scene.hammerless.auto_sun = False
    depsgraph.update()
    cache.update_lights(depsgraph)
    assert not cache.lights
    if not bpy.app.background:
        import gpu
        from mathutils import Matrix
        cube.location.x = 0
        cube.data.vertices[0].co.z -= 0.5
        cube.data.update()
        depsgraph.update()
        cache.update(depsgraph)
        offscreen = gpu.types.GPUOffScreen(64, 64, format='RGBA32F')
        try:
            shader = preview.diagnostic_shader()
            with offscreen.bind():
                gpu.state.viewport_set(0, 0, 64, 64)
                fb = gpu.state.active_framebuffer_get()
                fb.clear(color=(0, 0, 0, 1), depth=1)
                preview.draw_geometry(cache, shader, Matrix.Scale(0.5, 4))
                pixels = fb.read_color(32, 32, 1, 1, 4, 0, 'FLOAT')
                assert sum(pixels.to_list()[0][0][:3]) > 0.1, 'GPU produced no geometry'
                from hammerless.core.previewlighting import PointLight
                point = PointLight.from_keys({'_light': '255 255 255 200', '_quadratic_attn': '1'})
                cache.lights = [((0, 0, -102), point.intensity, point.attenuation)]
                cache.unit_scale = 1.0
                lit_shader = preview.diagnostic_shader(lit=True)
                fb.clear(color=(0, 0, 0, 1), depth=1)
                preview.draw_geometry(cache, lit_shader, Matrix.Scale(0.5, 4), lit=True)
                sample = fb.read_color(32, 32, 1, 1, 4, 0, 'FLOAT').to_list()[0][0]
                assert all(abs(v - 200/255) < 0.01 for v in sample[:3]), sample
                display_shader = preview.diagnostic_shader(lit=True,display=True)
                fb.clear(color=(0,0,0,1),depth=1)
                preview.draw_geometry(cache,display_shader,Matrix.Scale(0.5,4),lit=True,display=True,exposure=-1)
                display_sample = fb.read_color(32,32,1,1,4,0,'FLOAT').to_list()[0][0][0]
                assert abs(display_sample-(1.055*(100/255)**(1/2.4)-.055))<0.001, display_sample
                from hammerless.core.previewlighting import spot_parameters
                cone = spot_parameters({'_inner_cone':'20','_cone':'45','_exponent':'2'})
                for direction, expected in (((0,0,1),200/255),((0,0,-1),0)):
                    cache.lights = [((0,0,-102),point.intensity,point.attenuation,(*direction,cone[0]),cone[1:])]
                    cache.light_texture = None
                    fb.clear(color=(0,0,0,1),depth=1)
                    preview.draw_geometry(cache,lit_shader,Matrix.Scale(0.5,4),lit=True)
                    value = fb.read_color(32,32,1,1,4,0,'FLOAT').to_list()[0][0][0]
                    assert abs(value-expected)<0.001, (direction,value)
                cache.lights = [((0,0,-102),point.intensity,point.attenuation)]
                cache.light_texture = None
                # Sun intensity does not diminish with distance to its origin.
                cache.lights = [((0,0,0),(200,200,200),(1,0,0),(0,0,1,2),(0,0,0))]
                fb.clear(color=(0,0,0,1),depth=1)
                preview.draw_geometry(cache,lit_shader,Matrix.Scale(0.5,4),lit=True)
                sun_sample = fb.read_color(32,32,1,1,4,0,'FLOAT').to_list()[0][0][0]
                assert abs(sun_sample-200/255)<0.001, sun_sample
                cache.lights = [((0,0,-102),point.intensity,point.attenuation)]
                cache.light_texture = None
                mat = bpy.data.materials.new('Preview test')
                mat.diffuse_color = (0.25, 0.5, 1.0, 1.0)
                cube.data.materials.append(mat)
                depsgraph.update()
                cache.update(depsgraph)
                material_shader = preview.diagnostic_shader(lit=True, textured=True)
                fb.clear(color=(0, 0, 0, 1), depth=1)
                preview.draw_geometry(cache, material_shader, Matrix.Scale(0.5, 4), lit=True, textured=True)
                material_sample = fb.read_color(32, 32, 1, 1, 4, 0, 'FLOAT').to_list()[0][0]
                assert all(abs(v - c*200/255)<0.001 for v,c in zip(material_sample[:3],(0.25,0.5,1))), material_sample
                mat.use_nodes = True
                image = bpy.data.images.new('UV quadrants', width=2, height=2)
                image.pixels.foreach_set([0,0,1,1, 1,0,0,1, 0,1,0,1, 1,1,1,1])
                image.update()
                tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
                tex.image = image
                bsdf = mat.node_tree.nodes.get('Principled BSDF')
                mat.node_tree.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
                mat.hammerless.texture_scale = 0.0625
                # Exported brush mapping uses original texture dimensions.
                mat['hl_tex_size'] = (2, 2)
                for uv in cube.data.uv_layers.active.data:
                    uv.uv = (0.75, 0.25)
                painted = cube.data.attributes.new('hl_tex_uv', 'INT', 'FACE')
                for use_painted in (1, 0):
                    for item in painted.data:
                        item.value = use_painted
                    cube.data.update()
                    depsgraph.update()
                    cache.update(depsgraph)
                    fb.clear(color=(0, 0, 0, 1), depth=1)
                    preview.draw_geometry(cache, material_shader, Matrix.Scale(0.5, 4), lit=True, textured=True)
                    pixel = fb.read_color(32, 32, 1, 1, 4, 0, 'FLOAT').to_list()[0][0]
                    assert abs(pixel[0]-200/255)<0.01 and max(pixel[1:3])<0.01, (use_painted,pixel)
                # Classification is tested against VMTs separately; exercise the
                # GPU output with a prepared unlit material snapshot and no lights.
                from types import SimpleNamespace
                material_key=next(iter(cache.geometry.values())).materials[0].as_pointer()
                cache.surface_materials=SimpleNamespace(materials={material_key:(image,(2,2))},unlit={material_key:True})
                saved_lights=cache.lights
                cache.lights=[];cache.light_texture=None
                fb.clear(color=(0,0,0,1),depth=1)
                from unittest.mock import patch
                with patch('hammerless.blender.extract._base_color_image', side_effect=AssertionError('Draw rescanned material nodes')), \
                     patch('hammerless.blender.extract.texture_size', side_effect=AssertionError('Draw recomputed prepared size')):
                    preview.draw_geometry(cache,material_shader,Matrix.Scale(.5,4),lit=True,textured=True)
                unlit_pixel=fb.read_color(32,32,1,1,4,0,'FLOAT').to_list()[0][0]
                assert abs(unlit_pixel[0]-1)<.001 and max(unlit_pixel[1:3])<.001,unlit_pixel
                cache.surface_materials=None
                cache.lights=saved_lights;cache.light_texture=None
                shadow_shader = preview.diagnostic_shader(lit=True, textured=True, shadows=True)
                def shadow_pixel():
                    fb.clear(color=(0,0,0,1),depth=1)
                    preview.draw_geometry(cache,shadow_shader,Matrix.Scale(0.5,4),lit=True,textured=True,shadows=True)
                    return fb.read_color(32,32,1,1,4,0,'FLOAT').to_list()[0][0][0]
                assert abs(shadow_pixel()-200/255)<0.001, 'Self shadowing on receiver'
                distractors = []
                for x in range(12):
                    bpy.ops.mesh.primitive_cube_add(location=(20+x*3,0,-10))
                    distractors.append(bpy.context.object)
                bpy.ops.mesh.primitive_cube_add(location=(0,0,-10))
                blocker = bpy.context.object
                depsgraph.update()
                cache.update(depsgraph)
                assert shadow_pixel()<1e-6, 'Occluder did not cast shadow'
                tool = bpy.data.materials.new('Shadow tool')
                tool.hammerless.source_material = 'tools/toolsnodraw'
                blocker.data.materials.append(tool)
                depsgraph.update();cache.update(depsgraph)
                assert shadow_pixel()<1e-6, 'Nodraw lost its shadow'
                tool.hammerless.source_material = 'tools/toolsplayerclip'
                depsgraph.update();cache.update(depsgraph)
                assert abs(shadow_pixel()-200/255)<0.001, 'Player clip cast a shadow'
                tool.hammerless.source_material = 'tools/toolsnodraw'
                depsgraph.update();cache.update(depsgraph)
                assert shadow_pixel()<1e-6, 'Changed material did not restore shadow'
                blocker.hammerless.role = 'IGNORE'
                depsgraph.update();cache.update(depsgraph)
                assert abs(shadow_pixel()-200/255)<0.001, 'Ignored object cast a shadow'
                blocker.hammerless.role = 'AUTO'
                depsgraph.update();cache.update(depsgraph)
                assert shadow_pixel()<1e-6, 'Unignored object missing'
                blocker.hammerless.classname = 'info_survivor_position'
                depsgraph.update();cache.update(depsgraph)
                assert abs(shadow_pixel()-200/255)<0.001, 'Spawn marker cast a shadow'
                blocker.hammerless.classname = 'prop_static'
                blocker.data['hl_model'] = 'models/test.mdl'
                depsgraph.update();cache.update(depsgraph)
                assert shadow_pixel()<1e-6, 'Actual model mesh excluded as a point marker'
                del blocker.data['hl_model']
                blocker.hammerless.classname = ''
                depsgraph.update();cache.update(depsgraph)
                assert shadow_pixel()<1e-6, 'Restored brush lost its shadow'
                builds = cache.trace.builds
                blocker.location.x = 10
                depsgraph.update()
                cache.update(depsgraph)
                assert abs(shadow_pixel()-200/255)<0.001, 'Moving blocker did not clear shadow'
                assert cache.trace.builds == builds, 'Transform rebuilt mesh BVH'
                bpy.data.objects.remove(blocker,do_unlink=True)
                depsgraph.update()
                cache.update(depsgraph)
                assert abs(shadow_pixel()-200/255)<0.001, 'Deleted blocker retained'
                for obj in distractors:
                    bpy.data.objects.remove(obj,do_unlink=True)
                depsgraph.update()
                cache.update(depsgraph)
                cache.lights = []
                cache.light_texture = None
                cache.sky_color = (51.,51.,51.)
                assert abs(shadow_pixel()-0.2)<0.001, 'Open sky normalization incorrect'
                bpy.ops.mesh.primitive_cube_add(size=20)
                enclosure = bpy.context.object
                depsgraph.update()
                cache.update(depsgraph)
                assert shadow_pixel()<1e-6, 'Sky leaked into closed enclosure'
                bpy.data.objects.remove(enclosure,do_unlink=True)
                depsgraph.update()
                cache.update(depsgraph)
                assert abs(shadow_pixel()-0.2)<0.001, 'Sky did not recover after enclosure removal'
                cache.sky_color = (0.,0.,0.)
        finally:
            offscreen.free()
    bpy.data.objects.remove(cube, do_unlink=True)
    depsgraph.update()
    cache.update(depsgraph)
    assert not cache.geometry and not cache.instances, 'Deleted geometry retained'
    bpy.app.handlers.depsgraph_update_post.remove(on_update)
    bpy.context.scene.render.engine = 'BLENDER_WORKBENCH'
    preview.unregister()
    props.unregister()
    return {'ok': True, 'gpu': not bpy.app.background, 'checks': ['registration', 'triangles', 'transform reuse', 'mesh edit', 'deletion'], 'point_irradiance': sample[:3] if not bpy.app.background else None, 'gpu_checks': ['point brightness', 'base color', 'painted UV quadrants', 'world-aligned downward face quadrants', 'spotlight direction', 'sRGB exposure', 'nodraw shadow', 'clip does not shadow', 'ignore role', 'open and enclosed sky', 'textured opaque shadow', 'no receiver self-shadow', 'transform preserves BVH', 'deleted blocker removed'] if not bpy.app.background else []}


try:
    result = check()
except Exception:
    result = {'ok': False, 'traceback': traceback.format_exc()}
OUT.write_text(json.dumps(result, indent=2))
print(json.dumps(result), flush=True)
if not bpy.app.background:
    # This isolated factory-startup test owns no user document.
    bpy.context.preferences.view.use_save_prompt = False
    bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
