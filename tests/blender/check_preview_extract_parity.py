"""Background regression: evaluated mesh extraction matches temporary meshes."""
from pathlib import Path
from types import SimpleNamespace
import sys

import bpy
import bmesh
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hammerless.blender.l4d2preview import extract_geometry

ARRAYS = ('positions', 'normals', 'uvs', 'face_normals', 'painted', 'material_ids')


def temporary_reference(evaluated):
    # Run the old to_mesh/to_mesh_clear path against the identical evaluated ID.
    calls = []
    def make():
        calls.append('make')
        return evaluated.to_mesh()
    def clear():
        calls.append('clear')
        evaluated.to_mesh_clear()
    proxy = SimpleNamespace(original=SimpleNamespace(data=SimpleNamespace(is_editmode=True)),
                            data=evaluated.data, to_mesh=make, to_mesh_clear=clear)
    result = extract_geometry(proxy)
    assert calls == ['make', 'clear'], calls
    return result


def compare(obj, label):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    result = extract_geometry(evaluated)
    reference = temporary_reference(evaluated)
    for name in ARRAYS:
        actual, expected = getattr(result, name), getattr(reference, name)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-7,
                                   err_msg=f'{label}: {name}')
        assert actual.flags.owndata, (label, name, 'snapshot aliases another array')
    print('PASS extraction parity:', label, len(result.positions) // 3, 'triangles', flush=True)
    return result


bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
mesh = obj.data
for name in ('Extraction A', 'Extraction B'):
    mesh.materials.append(bpy.data.materials.new(name))
painted = mesh.attributes.new('hl_tex_uv', 'INT', 'FACE')
for i, polygon in enumerate(mesh.polygons):
    polygon.material_index = i % 2
    polygon.use_smooth = i % 3 == 0
    painted.data[i].value = i % 2
uv = mesh.uv_layers.active or mesh.uv_layers.new()
for i, corner in enumerate(uv.data):
    corner.uv = ((i % 5) / 4, (i % 7) / 6)
mesh.update()
snapshot = compare(obj, 'cube with UV, normals, material and painted faces')
saved = {name: getattr(snapshot, name).copy() for name in ARRAYS}
assert set(snapshot.material_ids) == {0, 1}
assert set(snapshot.painted) == {0, 1}

bevel = obj.modifiers.new('Extraction bevel', 'BEVEL')
bevel.width = .15
bevel.segments = 2
compare(obj, 'bevel modifier')
subdivision = obj.modifiers.new('Extraction subdivision', 'SUBSURF')
subdivision.levels = 1
compare(obj, 'bevel and subdivision modifiers')
obj.modifiers.clear()

bpy.ops.object.mode_set(mode='EDIT')
try:
    bm = bmesh.from_edit_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.x -= .75
    bmesh.update_edit_mesh(mesh, loop_triangles=True, destructive=False)
    assert obj.data.is_editmode
    edited = compare(obj, 'live edited BMesh')
    assert not np.allclose(edited.positions, snapshot.positions), 'Edit Mode change was lost'
finally:
    bpy.ops.object.mode_set(mode='OBJECT')
compare(obj, 'committed edit')
for name in ARRAYS:
    np.testing.assert_array_equal(getattr(snapshot, name), saved[name],
                                  err_msg=f'saved {name} changed with source mesh')
print('PASS: extraction arrays survive source edits and evaluated mesh disposal', flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
