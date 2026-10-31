"""Typed list UI sockets rebuild without losing their declared data types."""
import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import listtools
if not hasattr(bpy.types, 'HL_MT_list_tools'):
    listtools.register()
tree=bpy.data.node_groups.new('List tools test','HL_LogicTree')
for cls in listtools.NODES:
    node=tree.nodes.new(cls.bl_idname)
    assert node.list_type=='bots'
    assert next(s for s in node.inputs if s.identifier=='items').bl_idname=='HL_BotListSocket'
    node.list_type='navareas'
    assert next(s for s in node.inputs if s.identifier=='items').bl_idname=='HL_NavAreasSocket'
    if cls.kind not in ('LIST_COUNT','LIST_FILTER'):
        assert next(s for s in node.outputs if s.identifier=='item').bl_idname=='HL_NavAreaSocket'
print('LIST_TOOLS_BLENDER_OK')
