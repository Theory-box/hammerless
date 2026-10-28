import sys
import pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
import bpy,hammerless
hammerless.register()
from hammerless.blender import overlays as ov
from hammerless.blender.extract import extract_scene
from hammerless.core.build import build_vmf,Report
for o in list(bpy.data.objects):bpy.data.objects.remove(o,do_unlink=True)
bpy.ops.mesh.primitive_cube_add(size=2)
wall=bpy.context.object;wall.name='Receiver';wall.hammerless.role='BRUSH'
mat=bpy.data.materials.new('Audit material');mat.hammerless.source_material='brick/test';wall.data.materials.append(mat)
bpy.context.view_layer.update();a=ov.receiver_faces(bpy.context)
mat.hammerless.source_material='tools/toolsnodraw';bpy.context.view_layer.update();b=ov.receiver_faces(bpy.context)
assert a is not b
assert all(x[1].lower()=='tools/toolsnodraw' for x in b)
mat.hammerless.source_material='brick/test'
mapcoll=bpy.data.collections.new('Audit Map');bpy.context.scene.collection.children.link(mapcoll);bpy.context.scene.hammerless.map_collection=mapcoll
kit=bpy.data.collections.new('Kit');bpy.context.scene.collection.children.link(kit)
for coll in list(wall.users_collection):coll.objects.unlink(wall)
kit.objects.link(wall)
o=bpy.data.objects.new('Decal',None);kit.objects.link(o);o.hammerless.role='ENTITY';o.hammerless.classname='info_overlay';o.hl_overlay.enabled=True;o.location.z=1
o.hl_overlay.restrict_receivers=True;o.hl_overlay.receivers.add().object=wall
holder=bpy.data.objects.new('Kit Copy',None);mapcoll.objects.link(holder);holder.instance_type='COLLECTION';holder.instance_collection=kit
second=holder.copy();second.name='Other Copy';mapcoll.objects.link(second)
# Overlap both copies deliberately: name scoping must still keep receivers separate.
bpy.context.view_layer.update()
rep=Report();ir,_=extract_scene(bpy.context,rep,defer_textures=True)
print('INSTANCE_BRUSH_NAMES',[b.source for b in ir.brushes],flush=True)
print('INSTANCE_OVERLAY_FILTER',[e.keyvalues.get('_hl_overlay_receivers') for e in ir.entities if e.classname=='info_overlay'],flush=True)
text,rep=build_vmf(ir)
assert text,rep.errors
from hammerless.core.vmf import parse
entities=[e for e in parse(text) if e.get('classname')=='info_overlay']
assert len(entities)==2
ids=[set(e.get('sides').split()) for e in entities]
assert ids[0] and ids[1] and ids[0].isdisjoint(ids[1]),ids
print('OVERLAY_AUDIT_REGRESSIONS_PASS')
hammerless.unregister()
