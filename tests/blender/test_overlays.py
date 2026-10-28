import sys, pathlib
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
import bpy, hammerless
hammerless.register()
from hammerless.blender import overlays
from hammerless.blender.extract import extract_scene
from hammerless.core.build import build_vmf,Report
# Match real organized maps: moving a child emits a Collection update too.
coll=bpy.data.collections.new('Overlay test map')
bpy.context.scene.collection.children.link(coll)
bpy.context.scene.hammerless.map_collection=coll
bpy.context.view_layer.active_layer_collection=bpy.context.view_layer.layer_collection.children[coll.name]
bpy.ops.mesh.primitive_cube_add(size=2)
wall=bpy.context.object;wall.name='Receiver';wall.hammerless.role='BRUSH'
wall.scale=(2,2,.1)
bpy.context.scene.cursor.location=(0,0,.1)
bpy.ops.hammerless.add_overlay()
o=bpy.context.object;o.hl_overlay.material='decals/asphalt/asphalt1'
assert o.hl_overlay.stop_at_surface
s=bpy.context.scene.hammerless;s.auto_seal=True;s.auto_light_environment=False
kv=overlays.entity(o,s.units_per_meter).keyvalues
faces=overlays.receiver_faces(bpy.context)
from hammerless.core.overlays import project
hits=[project(kv,*f) for f in faces]
assert sum(bool(x) for x in hits)==1,hits
rep=Report();ir,_=extract_scene(bpy.context,rep,defer_textures=True)
text,rep=build_vmf(ir)
assert text,rep.errors
from hammerless.core.vmf import parse
es=[b for b in parse(text) if b.get('classname')=='info_overlay']
assert len(es)==1 and es[0].get('sides'),es
assert '_hl_overlay_' not in text
import tempfile
pathlib.Path(tempfile.gettempdir(),'hl_overlay_smoke.vmf').write_text(text)
# Mirrored footprint keeps the outward projection normal.
o.scale.x=-1
mirror=overlays.entity(o,s.units_per_meter).keyvalues
assert mirror['BasisNormal']==kv['BasisNormal']
assert sum(bool(project(mirror,*f)) for f in faces)==1
o.scale.x=1
# UI property preservation and independently transformed duplicate.
copy=o.copy();bpy.context.collection.objects.link(copy);copy.location.x+=1
assert copy.hl_overlay.material==o.hl_overlay.material
bpy.data.objects.remove(copy,do_unlink=True)
# Align at nearest surface and repeat export.
bpy.ops.hammerless.overlay_align()
assert overlays.entity(o,s.units_per_meter).keyvalues
# Transforming only the overlay must reuse receiver geometry and broad phase
# must retain exactly the same projected hits as exhaustive clipping.
bpy.context.view_layer.update()
faces=overlays.receiver_faces(bpy.context)
o.location.x+=.01
bpy.context.view_layer.update()
assert overlays.receiver_faces(bpy.context) is faces, 'Overlay move invalidated map cache'
kv=overlays.entity(o,s.units_per_meter).keyvalues
assert [project(kv,*f) for f in overlays.nearby_faces(kv,faces) if project(kv,*f)] == [project(kv,*f) for f in faces if project(kv,*f)]
wall.location.x+=.01
bpy.context.view_layer.update()
assert overlays.receiver_faces(bpy.context) is not faces, 'Brush move retained stale cache'
# Triangulation must not discard half of a receiving surface.
tri=wall.modifiers.new('Triangulated receiver','TRIANGULATE')
bpy.context.view_layer.update()
faces=overlays.receiver_faces(bpy.context)
top=[f for f in faces if f[2]==wall.name and overlays.g.polygon_normal(f[0])[2]>.99]
assert len(top)==1 and len(top[0][0])==4, top
# Adding with a rotated, elevated cursor aligns automatically.
bpy.context.scene.cursor.location=(0,0,.25)
bpy.context.scene.cursor.rotation_euler=(1,0,0)
bpy.ops.hammerless.add_overlay()
new=bpy.context.object
kv=overlays.entity(new,s.units_per_meter).keyvalues
assert any(project(kv,*f) for f in overlays.receiver_faces(bpy.context))
print('OVERLAY_SMOKE_PASS',es[0].get('sides'))

hammerless.unregister()

