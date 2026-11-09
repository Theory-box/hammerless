"""Imported preview ID metadata reuses work without retaining stale source IDs."""
from pathlib import Path
from types import SimpleNamespace
import sys,json,traceback
import bpy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,vmfimport
from hammerless.blender.previewsurfacecharts import ChartSnapshot
from hammerless.blender.previewlivecache import LiveSurfaceCache
from hammerless.core import vmfimport as vi
from hammerless.core.geometry import box_brush
from hammerless.core.vmf import VMFWriter,Block
OUT=ROOT.parents[1]/'outputs/l4d2-preview/preview-document-ids.json'

def run():
 props.register();scene=bpy.context.scene
 writer=VMFWriter();world=Block('world');world.kv('id',writer.new_id());world.kv('classname','worldspawn')
 world.add(writer.solid(box_brush((0,0,0),(128,128,128),'dev/dev_measuregeneric01b')))
 raw=[];world.write(raw);raw=''.join(raw)
 text=bpy.data.texts.new('preview-document-ids.vmf');text.write(raw);scene[vmfimport.SOURCE_KEY]=text.name
 cache={};calls=[];checks=[];original_max=vi.Document.max_id
 def counted(document):
  calls.append(document);return original_max(document)
 vi.Document.max_id=counted
 try:
  first=ChartSnapshot(bpy.context,cache);maximum=original_max(first.document)
  assert len(calls)==1 and first.writer.new_id()==maximum+1
  second=ChartSnapshot(bpy.context,cache)
  assert len(calls)==1 and second.document is first.document
  assert second.writer.new_id()==maximum+1,'Each snapshot gets a fresh writer'
  checks.append('same document scans IDs once and resets fresh writer')
  # The exporter may change scales in shared parsed blocks, but not IDs.
  for solid in first.document.solids.values():vi.set_lightmap_scale(solid,lambda path:64)
  third=ChartSnapshot(bpy.context,cache)
  assert len(calls)==1 and third.writer.new_id()==maximum+1
  checks.append('scale-only parsed mutations preserve cached maximum')
  # Same Text datablock, different source content: existing parser must replace
  # the document and the preview must not keep the former smaller maximum.
  text.write('\nentity\n{\n"id" "987654"\n"classname" "info_target"\n}\n')
  edited=ChartSnapshot(bpy.context,cache)
  assert len(calls)==2 and edited.document is not first.document
  assert edited.writer.new_id()==987655 and cache['document'] is edited.document
  assert len(cache)==2,'Only the current document and maximum should be retained'
  checks.append('source edit updates higher IDs and replaces retained document')
  scene.hammerless.lightmap_scale_imported=not scene.hammerless.lightmap_scale_imported
  changed=ChartSnapshot(bpy.context,cache)
  assert len(calls)==3 and changed.document is not edited.document
  assert changed.writer.new_id()==987655
  checks.append('imported-scale mode replaces parser document')
  plain=ChartSnapshot(bpy.context)
  assert len(calls)==4 and plain.writer.new_id()==987655
  checks.append('standalone snapshot remains compatible')
  del scene[vmfimport.SOURCE_KEY]
  ChartSnapshot(bpy.context,cache);assert not cache
  checks.append('snapshot import removal clears retained document')
  # Live update must clear even when no chart snapshot is requested afterward.
  live=LiveSurfaceCache();live.document_ids.update(document=changed.document,maximum=987654)
  bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
  live.update(bpy.context,deps,SimpleNamespace(geometry={},instances=[],surface_materials=None),0)
  assert not live.document_ids
  checks.append('live empty-scene import removal clears retained document')
  return dict(ok=True,checks=checks,max_id_scans=len(calls))
 finally:vi.Document.max_id=original_max
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
