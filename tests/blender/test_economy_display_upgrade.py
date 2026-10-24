"""Only untouched shipped rules upgrade; owner settings and custom graphs survive."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.logic_examples import all_examples,build_example,_group_state
from hammerless.blender.economy_migration import upgrade_displays
for title in ('Personal Kill Points','Purchase - Any Weapon or Item','Purchase - Remove Barrier'):
    problems=[]
    build_example(bpy.context,next(i for i,e in enumerate(all_examples()) if e['title']==title),problems)
    assert not problems,problems
obj=next(o for o in bpy.context.scene.objects if o.name.endswith('purchase_display'))
slot=obj.hammerless.behaviours[0]
setting=slot.graph.nodes['price_setting'].setting_id
slot.values[setting].integer=1234
old=slot.graph.nodes['buy'].group
old['hl_example_group']='previous version'
old['hl_example_state']=_group_state(old)
custom=old.copy()
custom.name='My Edited Purchase Rules'
custom['hl_example_group']='previous version'
custom['hl_example_state']=_group_state(custom)
custom.nodes['poor_text'].template='My custom message'
custom.use_fake_user=True
assert upgrade_displays()==1
assert slot.graph.nodes['buy'].group!=old
assert slot.values[setting].integer==1234
assert custom.nodes['poor_text'].template=='My custom message'
assert custom['hl_example_group']=='previous version'
assert upgrade_displays()==0
print('PASS display upgrade preserves owner settings and custom rules; repeat is no-op')
hammerless.unregister()
