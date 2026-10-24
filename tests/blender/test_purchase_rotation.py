import sys, math, struct
import numpy as np
from pathlib import Path
import bpy
from mathutils import Euler, Matrix, Vector, Quaternion
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logic_examples import all_examples,build_example
from hammerless.blender.preview import purchase_display_transform
from hammerless.blender.ops import game_content,game_root
from hammerless.blender.extract import extract_scene
from hammerless.blender.logic import compile_logic
from hammerless.core.build import Report
from hammerless.core.gamemodel import load_game_model
from hammerless.core.wall_weapon import ITEMS
index=next(i for i,e in enumerate(all_examples()) if e['title']=='Purchase - Any Weapon or Item')
problems=[];build_example(bpy.context,index,problems);assert not problems,problems
obj=next(o for o in bpy.context.scene.objects if o.name.endswith('purchase_display'))
slot=obj.hammerless.behaviours[0];uid=slot.graph.nodes['item_setting'].setting_id
content=game_content(game_root(bpy.context));scale=bpy.context.scene.hammerless.units_per_meter
corrected=[];unsupported=[]
for item in ITEMS:
 slot.values[uid].choice=item
 obj.location=(2,3,4);obj.rotation_euler=(0.4,-0.3,1.2);bpy.context.view_layer.update()
 result=purchase_display_transform(obj,bpy.context)
 if result is None:
  unsupported.append(item);continue
 corrected.append(item)
 pos,angles=result
 world=Euler(tuple(math.radians(angles[i]) for i in (2,0,1)),'XYZ').to_matrix().to_4x4()
 world.translation=Vector(pos)/scale
 model=load_game_model(content,ITEMS[item]['model'])
 bind=model.bone_world();idle=model.bone_world(model.frame(model.sequences[0],0))
 vvd=content.read(ITEMS[item]['model'][:-4]+'.vvd')
 raw=np.frombuffer(vvd,dtype=np.uint8,count=struct.unpack_from('<i',vvd,16)[0]*48,offset=struct.unpack_from('<i',vvd,56)[0]).reshape(-1,48)
 weights=raw[:,:12].copy().view('<f4').reshape(-1,3)
 indices=sorted(set(raw[:,12:15][weights>1e-6].tolist()))
 for i in indices:
  b,a=bind[i],idle[i]
  def mat(v):
   p,q=v;m=Quaternion((q[3],*q[:3])).to_matrix().to_4x4();m.translation=Vector(p)/scale;return m
  # A point off the bone's origin also checks orientation, not only translation.
  point=Vector((0.17,0.23,-0.31,1))
  expected=obj.matrix_world @ mat(b) @ point
  actual=world @ mat(a) @ point
  assert (expected-actual).length<0.003,(item,expected,actual)
assert all(k in corrected for k in ('smg','rifle_ak47','pumpshotgun','first_aid_kit','pistol','molotov')),unsupported
slot.values[uid].choice='rifle_ak47';bpy.context.view_layer.update()
expected=purchase_display_transform(obj,bpy.context)
report=Report();ir,_=extract_scene(bpy.context,report,defer_textures=True);compile_logic(bpy.context,ir,report)
assert not report.errors,report.errors
# Find the unique display by its model; no other AK exists in this test scene.
entity=next(e for e in ir.entities if e.keyvalues.get('model')==ITEMS['rifle_ak47']['model'])
assert tuple(entity.angles)==expected[1] and tuple(entity.origin)==expected[0]
# A placeholder/manual mesh isn't evidence of the model's bind-pose orientation.
mesh=obj.data;obj.data=bpy.data.meshes.new('Placeholder')
assert purchase_display_transform(obj,bpy.context) is None
obj.data=mesh
print('PASS posed-model export alignment for',len(corrected),'items; unsupported poses:',unsupported)
hammerless.unregister()
