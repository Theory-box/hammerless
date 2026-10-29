import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
from hammerless.blender import modelbrowser as m
hammerless.register()
s=bpy.context.scene.hammerless
s.game_root=r'C:/Program Files (x86)/Steam/steamapps/common/Left 4 Dead 2'
t=bpy.context.scene.hl_models
assert len(m.catalog())==5547
assert len(m.listing(bpy.context))>5000
t.search='oildrum001'; names=m.listing(bpy.context); assert names and all('oildrum001' in n for n in names)
t.active='models/props_c17/oildrum001.mdl'
m.thumbnail(t.active); assert t.active in m._loaded
bpy.context.scene.cursor.location=(1,2,3)
assert bpy.ops.hammerless.model_place()=={'FINISHED'}
o=bpy.context.object
assert tuple(o.location)==(1,2,3)
assert o.hammerless.classname=='prop_static'
assert any(k.key=='model' and k.value==t.active for k in o.hammerless.keyvalues)
assert len(o.data.polygons)>0
bpy.ops.hammerless.model_star()
t.filter='FAV'; assert m.listing(bpy.context)==[t.active]
t.filter='RECENT'; assert m.listing(bpy.context)==[t.active]
t.prop_type='prop_physics'; bpy.ops.hammerless.model_place()
assert bpy.context.object.hammerless.classname=='prop_physics'
t.active='models/props_junk/dumpster.mdl'
assert bpy.ops.hammerless.model_replace()=={'FINISHED'}
assert bpy.context.object.hammerless.classname=='prop_physics'
assert tuple(bpy.context.object.location)==(1,2,3)
# Page bounds, filters, and preview cache stay bounded.
t.search=''; t.filter='ALL'; t.features='STATIC'
assert all(m.catalog()[p]['static'] for p in m.listing(bpy.context))
bpy.ops.hammerless.model_page(step=99999)
assert t.page==(len(m.listing(bpy.context))-1)//m.PAGE
for path in m.listing(bpy.context)[:140]: m.thumbnail(path)
assert len(m._loaded)<=128
hammerless.unregister()
print('MODEL_BROWSER_PASS')

