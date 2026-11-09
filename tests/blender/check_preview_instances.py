"""Background regression: real collection instances obey export helper policies."""
import sys
from pathlib import Path
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hammerless.blender import props
from hammerless.blender.l4d2preview import SceneCache

props.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
# Source collection is deliberately not linked into the scene/view layer.
source = bpy.data.collections.new('Instance source')
mesh = bpy.data.meshes.new('Triangle')
mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)], [], [(0,1,2)])
obj = bpy.data.objects.new('Source brush', mesh)
source.objects.link(obj)
holder = bpy.data.objects.new('Instance holder', None)
bpy.context.scene.collection.objects.link(holder)
holder.instance_type = 'COLLECTION'
holder.instance_collection = source
cache = SceneCache()

def count():
    bpy.context.view_layer.update()
    cache.update(bpy.context.evaluated_depsgraph_get())
    return len(cache.instances)

assert count() == 1, 'Unlinked source collection must still appear through its instance'
obj.hammerless.role = 'IGNORE'
assert count() == 0, 'Ignored source included'
obj.hammerless.role = 'AUTO'
obj.hammerless.classname = 'info_survivor_position'
assert count() == 0, 'Point marker included'
obj.hammerless.classname = 'prop_static'
obj.data['hl_model'] = 'models/test.mdl'
assert count() == 1, 'Real model excluded'
holder.hammerless.role = 'IGNORE'
assert count() == 0, 'Ignored holder included'
holder.hammerless.role = 'AUTO'
assert count() == 1, 'Restored instance missing'
print('PASS: instance source/holder filtering, hidden source, marker versus real model')

# Direct and instanced lights must obey the same selection rules as export.
bpy.context.scene.hammerless.auto_sun = False
bpy.context.scene.hammerless.ambient_brightness = 0
light_data = bpy.data.lights.new('Point', 'POINT')
light = bpy.data.objects.new('Point', light_data)
bpy.context.scene.collection.objects.link(light)
def lights():
    bpy.context.view_layer.update()
    cache.update_lights(bpy.context.evaluated_depsgraph_get())
    return len(cache.lights)
assert lights() == 1
light.hammerless.role = 'IGNORE'
assert lights() == 0, 'Ignored point still lights scene'
light.hammerless.role = 'AUTO'
assert lights() == 1
light.hide_set(True)
assert lights() == 0, 'Hidden point still lights scene'
light.hide_set(False)
# A sun inside the collection is not exported and must not become environment.
sun = bpy.data.objects.new('Instanced sun', bpy.data.lights.new('Sun', 'SUN'))
source.objects.link(sun)
assert lights() == 1, 'Instanced sun added environment lighting'
assert any('Instanced lights' in w for w in cache.warnings), cache.warnings
print('PASS: ignored/hidden/restored lights and rejected instanced sun')
