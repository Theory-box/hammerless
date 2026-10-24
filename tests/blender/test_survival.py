import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import navview
from hammerless.core.navfile import NavMesh,NavArea
from hammerless.blender.extract import scene_settings_to_ir
s=bpy.context.scene.hammerless
s.game_mode='SURVIVAL'
assert scene_settings_to_ir(s).game_mode=='SURVIVAL'
for key in ('SURVIVAL_START','SURVIVAL_ARENA'):
    assert bpy.ops.hammerless.add_preset(preset=key)=={'FINISHED'}
assert any(o.hammerless.classname=='func_button' and any(v.input=='PanicEvent' for v in o.hammerless.outputs) for o in bpy.context.scene.objects)
a=NavArea(1,0,(0,0,0),(100,100,0),0,0,spawn_attributes=128|320)
b=NavArea(2,0,(1000,0,0),(1100,100,0),0,0,connections=[[1],[],[],[]],spawn_attributes=320)
c=NavArea(3,0,(2000,0,0),(2100,100,0),0,0,spawn_attributes=320)
d=NavArea(4,0,(1000,100,0),(1100,200,0),0,0,connections=[[1],[],[],[]],spawn_attributes=8192)
m=NavMesh(areas=[a,b,c,d]);navview.set_predicted(m)
r=navview.spawn_reasons(m,a)
assert r[1]=='too_close',r
assert r[2]=='can_spawn',r
assert r[3]=='no_spawn',r
assert r[4]=='no_spawn',r
s.game_mode='CAMPAIGN'
assert scene_settings_to_ir(s).game_mode=='CAMPAIGN'
print('PASS registration, mode switching, native presets, directed spawn connectivity and NO_MOBS')
hammerless.unregister()
