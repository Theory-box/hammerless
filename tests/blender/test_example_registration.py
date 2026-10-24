import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import logic,logic_examples as ex
index=next(i for i,e in enumerate(ex.all_examples()) if e['title']=='Purchase - Any Weapon or Item')
counts=lambda:(len(bpy.data.objects),len(bpy.data.node_groups),len(bpy.data.collections))
before=counts()
bpy.utils.unregister_class(logic.HL_NodeExposedSetting)
try:
    try:ex.build_example(bpy.context,index)
    except ex.ExampleRegistrationError as error:assert 'restart' in str(error)
    else:raise AssertionError('Missing registration was not caught')
    assert counts()==before,(counts(),before)
finally:bpy.utils.register_class(logic.HL_NodeExposedSetting)
problems=[];ex.build_example(bpy.context,index,problems)
assert not problems,problems
print('PASS missing registration rejected without scene changes; registered example builds')
hammerless.unregister()
