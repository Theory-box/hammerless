"""Actual Blender socket registration, defaults and typed rebuilding."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import diagnostics
if not hasattr(bpy.types,'HL_MT_diagnostics'): diagnostics.register()
tree=bpy.data.node_groups.new('Diagnostics test','HL_LogicTree')
for cls in diagnostics.NODES:
    n=tree.nodes.new(cls.bl_idname)
    if cls.kind!='BA_INSPECT':
        assert not next(s for s in n.inputs if s.identifier=='enabled').value
        assert next(s for s in n.inputs if s.identifier=='interval').value==1
watch=tree.nodes.new('HL_DebugWatch')
for typ in ('num','bool','text','vec','thing','bot','navarea'):
    watch.value_type=typ
    assert watch.settings()['type']==typ
    assert any(s.identifier=='value' for s in watch.inputs)
print('DIAGNOSTICS_BLENDER_OK')
