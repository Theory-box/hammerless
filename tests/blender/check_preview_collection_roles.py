"""Compare batched preview roles with exporter after collection edits."""
from pathlib import Path
import sys,json,traceback
import bpy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props
from hammerless.blender.extract import effective_role,collection_role
from hammerless.blender.previewscene import collection_role_snapshot

def run():
 props.register();scene=bpy.context.scene
 parent=bpy.data.collections.new('Role parent');scene.collection.children.link(parent)
 first=bpy.data.collections.new('A role');second=bpy.data.collections.new('B role')
 parent.children.link(first);scene.collection.children.link(second)
 obj=bpy.data.objects.new('Role mesh',bpy.data.meshes.new('Role mesh'));first.objects.link(obj);second.objects.link(obj)
 checks=0
 def check():
  nonlocal checks
  lookup=collection_role_snapshot()
  for item in bpy.data.objects:
   assert collection_role(item)==lookup.get(item.as_pointer(),'NONE'),item.name
   assert effective_role(item)==effective_role(item,lookup),item.name
  checks+=1
 scene.collection.objects.link(obj)
 scene.collection.hammerless.role='IGNORE';check()
 scene.collection.hammerless.role='BRUSH';check()
 scene.collection.hammerless.role='NONE'
 check();parent.hammerless.role='IGNORE';check();second.hammerless.role='TERRAIN';check()
 parent.hammerless.role='NONE';check();first.objects.unlink(obj);check()
 first.objects.link(obj);first.hammerless.role='BRUSH';check()
 obj.hammerless.role='IGNORE';check();bpy.data.collections.remove(first);check()
 return dict(ok=True,checks=checks)
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
(ROOT.parents[1]/'outputs/l4d2-preview/collection-role-check.json').write_text(json.dumps(result,indent=2))
print(result,flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
