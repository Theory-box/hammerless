"""Factory-scene integration checks; never opens or saves a user map."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy
import numpy as np
import hammerless
from mathutils import Matrix
hammerless.register()
from hammerless.blender import particlepreview as preview
from hammerless.core.particleeffect import EffectSimulation
from tests.unit.test_particlecontrol import burst

def empty(name,location):
    obj=bpy.data.objects.new(name,None)
    bpy.context.scene.collection.objects.link(obj)
    obj.location=location
    return obj

def kv(obj,key,value):
    item=obj.hammerless.keyvalues.add();item.key=key;item.value=value

emitter=empty('Preview emitter',(3,4,5))
target=empty('Endpoint',(5,4,5))
kv(target,'targetname','beam_end');kv(emitter,'cpoint1','beam_end')
kv(emitter,'cpoint2','absent')
bpy.context.view_layer.update()
frame=emitter.matrix_world.copy()
bindings,warnings=preview.control_point_bindings(bpy.context.scene,emitter)
assert bindings[1]==target and len(warnings)==1
points=preview.sample_control_points(bindings,frame,32)
np.testing.assert_allclose(points[0],np.eye(4))
np.testing.assert_allclose(points[1][:3,3],[64,0,0])
free=EffectSimulation({'root':burst()},'root',control_points=points)
locked=EffectSimulation({'root':burst([{'functionName':'Movement Lock to Control Point'}])},'root',control_points=points)
for tree in (free,locked):tree.advance(1/60)
emitter.location.x+=1
bpy.context.view_layer.update()
points=preview.sample_control_points(bindings,frame,32)
for tree in (free,locked):tree.advance(.5,points)
np.testing.assert_allclose(free.systems[0].position,[[0,0,0]])
np.testing.assert_allclose(locked.systems[0].position,[[32,0,0]])
np.testing.assert_allclose(points[1][:3,3],[64,0,0])
duplicate=empty('Duplicate endpoint',(0,0,0));kv(duplicate,'targetname','beam_end')
bindings,warnings=preview.control_point_bindings(bpy.context.scene,emitter)
assert 1 not in bindings and any('Ambiguous' in w for w in warnings)
before=(len(bpy.data.objects),len(bpy.data.meshes),tuple(bpy.context.scene.cursor.location))
preview.stop()
assert before==(len(bpy.data.objects),len(bpy.data.meshes),tuple(bpy.context.scene.cursor.location))
hammerless.unregister()
print('PASS Blender named control points, ambiguity reporting, moving emitter, free/locked particles and cleanup')
