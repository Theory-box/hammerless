"""Focused export check with Blender's actual Smooth by Angle asset; no game or bake required."""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
from hammerless.blender.extract import mesh_to_brushes
from hammerless.blender.vmfimport import _faces
from hammerless.core.vmf import VMFWriter
from hammerless.core import vmfimport as vi
from hammerless.core.ir import Polygon

hammerless.register()
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
bevel = obj.modifiers.new('Bevel', 'BEVEL')
bevel.width = 0.2
bevel.segments = 1
asset = Path(bpy.utils.resource_path('LOCAL')) / 'datafiles/assets/geometry_nodes/smooth_by_angle.blend'
with bpy.data.libraries.load(str(asset)) as (src, dst):
    dst.node_groups = ['Smooth by Angle']
mod = obj.modifiers.new('Smooth by Angle', 'NODES')
mod.node_group = dst.node_groups[0]
angle = next(i.identifier for i in mod.node_group.interface.items_tree
             if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == 'Angle')
ignore = next(i.identifier for i in mod.node_group.interface.items_tree
              if i.item_type == 'SOCKET' and i.in_out == 'INPUT' and i.name == 'Ignore Sharpness')
mod[ignore] = True
materials = SimpleNamespace(resolve=lambda mat: ('dev/dev_measuregeneric01', 0.25, 16))

def exported(degrees):
    mod[angle] = math.radians(degrees)
    mod.node_group.interface_update(bpy.context)
    obj.update_tag()
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    brushes = mesh_to_brushes(obj, dg, 32, materials)
    writer = VMFWriter()
    solid = writer.solid(brushes[0])
    flags = [int(s.get('smoothing_groups')) for s in solid.blocks('side')]
    # Imported brush writeback takes a separate path; it must export the same shading.
    imported = vi.solid_block(None, _faces(obj, 32, materials, dg), writer.new_id,
                             lambda v, m: writer.side(Polygon(v, m)))
    assert sorted(flags) == sorted(int(s.get('smoothing_groups')) for s in imported.blocks('side'))
    return flags

low, high = exported(30), exported(60)
assert low != high, (low, high)
assert set(low) == {-2147483648} and any(f != -2147483648 for f in high), (low, high)
assert all(low) and all(high)  # never silently fall back to the compiler's 45-degree default
obj.modifiers.remove(mod)
obj.modifiers.remove(bevel)
assert all(f.smoothing_groups == 0x80000000 for f in
           mesh_to_brushes(obj, bpy.context.evaluated_depsgraph_get(), 32, materials)[0].faces)
hammerless.unregister()
print('PASS actual Smooth by Angle 30/60 degrees, new/imported export, and flat faces')
