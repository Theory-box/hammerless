import sys,tempfile
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import economy_migration, exposed
from hammerless.blender.logic_examples import _socket
from hammerless.blender.logic import compile_logic
from hammerless.blender.extract import extract_scene
from hammerless.core.build import Report

tree=bpy.data.node_groups.new('Legacy world','HL_LogicTree')
tree.use_fake_user=True
tree.scene_name=bpy.context.scene.name
points=tree.nodes.new('HL_NodePersonalPoints')
points.special=150
points.starting=100
graph=bpy.data.node_groups.new('Legacy shop','HL_LogicTree')
graph.is_behaviour=True
old=graph.nodes.new('HL_NodeWallWeapon')
old.item='pumpshotgun';old.price=750
self=graph.nodes.new('HL_NodeSelf')
graph.links.new(self.outputs['Object'],old.inputs['Display Object'])
setting=graph.nodes.new('HL_NodeExposedSetting')
setting.value_type='INT';setting.default_integer=750;setting.ui_name='Price'
graph.links.new(setting.outputs[0],_socket(old,'price',False))
bpy.ops.mesh.primitive_cube_add()
obj=bpy.context.object
obj.hammerless.role='ENTITY';obj.hammerless.classname='prop_dynamic_override'
kv=obj.hammerless.keyvalues.add();kv.key='model';kv.value='models/w_models/weapons/w_shotgun.mdl'
slot=obj.hammerless.behaviours.add();slot.graph=graph;slot.preview_enabled=False
exposed.sync_slot(slot)
uid=setting.setting_id
slot.values[uid].integer=900
copy=obj.copy();bpy.context.scene.collection.objects.link(copy)
copy.hammerless.behaviours[0].values[uid].integer=300
saved=tempfile.TemporaryDirectory()
path=str(Path(saved.name)/'legacy.blend')
bpy.ops.wm.save_as_mainfile(filepath=path)
assert economy_migration.expand(tree)==1
assert economy_migration.expand(graph)==1
assert economy_migration.expand(graph)==0
assert slot.values[uid].integer==900
assert copy.hammerless.behaviours[0].values[uid].integer==300
assert graph.nodes.get(setting.name)==setting
group=next(n for n in tree.nodes if n.bl_idname=='HL_NodeGroup')
assert _socket(group,'special',False).value==150
assert _socket(group,'starting',False).value==100
report=Report();ir,_=extract_scene(bpy.context,report,defer_textures=True);compile_logic(bpy.context,ir,report)
assert not report.errors,report.errors
script='\n'.join(ir.extra_scripts.values())
assert 'HL_WallBuy' not in script and 'HL_PointsAward' not in script
assert '900.0' in script and '300.0' in script and 'pumpshotgun' in script
obj_name,copy_name=obj.name,copy.name
bpy.ops.wm.open_mainfile(filepath=path)
assert not any(n.bl_idname in ('HL_NodeWallWeapon','HL_NodePersonalPoints')
               for g in bpy.data.node_groups if g.bl_idname=='HL_LogicTree' for n in g.nodes)
assert bpy.data.objects[obj_name].hammerless.behaviours[0].values[uid].integer==900
assert bpy.data.objects[copy_name].hammerless.behaviours[0].values[uid].integer==300
saved.cleanup()
print('PASS legacy expansion retains rewards, links, object overrides, duplicate independence and compile')
hammerless.unregister()
