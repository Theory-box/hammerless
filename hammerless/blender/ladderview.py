"""Selection-only ladder facing guides, derived from actual climbable faces."""
import bpy
import bmesh
import blf
import gpu
from mathutils import Vector
from gpu_extras.batch import batch_for_shader
from bpy_extras.view3d_utils import location_3d_to_region_2d

_handlers = []
COLOR = (0.3, 0.9, 1.0, 1.0)


def ladder_objects(context):
    from .presets import preset_root
    found = {}
    for selected in context.selected_objects:
        root = preset_root(selected)
        candidates = [selected]
        if root is not None:
            candidates += [root] + list(root.children_recursive)
        for obj in candidates:
            if (obj.type == 'MESH' and obj.hammerless.classname in ('func_ladder', 'func_simpleladder')
                    and obj.visible_get(view_layer=context.view_layer, viewport=context.space_data)):
                found[obj.as_pointer()] = obj
    return list(found.values())


def face_guides(obj, depsgraph):
    """World-space face centers and outward normals, including live Edit Mode changes."""
    indices = {i for i, slot in enumerate(obj.material_slots) if slot.material is not None
               and (slot.material.hammerless.source_material or slot.material.name).lower().replace('\\', '/')
               == 'tools/toolsinvisibleladder'}
    if not indices or abs(obj.matrix_world.determinant()) < 1e-12:
        return []
    matrix = obj.matrix_world
    normal_matrix = matrix.to_3x3().inverted().transposed()
    evaluated = None
    try:
        if obj.mode == 'EDIT':
            mesh = bmesh.from_edit_mesh(obj.data)
            mesh.normal_update()
            faces = [(f.calc_center_median(), f.normal.copy()) for f in mesh.faces
                     if f.material_index in indices and not f.hide]
        else:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            faces = [(f.center.copy(), f.normal.copy()) for f in mesh.polygons if f.material_index in indices]
        return [(matrix @ center, (normal_matrix @ normal).normalized()) for center, normal in faces
                if normal.length_squared > 1e-12]
    finally:
        if evaluated is not None:
            evaluated.to_mesh_clear()


def guides(context):
    space = context.space_data
    if (space is None or space.type != 'VIEW_3D' or not space.overlay.show_overlays
            or context.region_data is None):
        return []
    length = 32.0 / max(context.scene.hammerless.units_per_meter, 0.001)
    depsgraph = context.evaluated_depsgraph_get()
    return [(center, normal, length) for obj in ladder_objects(context)
            for center, normal in face_guides(obj, depsgraph)]


def _draw():
    lines = []
    for center, normal, length in guides(bpy.context):
        start = center + normal * (length * 0.025)
        tip = start + normal * length
        side = normal.cross(Vector((0, 0, 1)))
        if side.length_squared < 1e-6:
            side = normal.cross(Vector((0, 1, 0)))
        side.normalize()
        base = tip - normal * length * 0.25
        lines += [start, tip, tip, base + side * length * 0.15, tip, base - side * length * 0.15]
    if not lines:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {'pos': lines})
    depth, mask, width = gpu.state.depth_test_get(), gpu.state.depth_mask_get(), gpu.state.line_width_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(False)
        gpu.state.line_width_set(3.0)
        shader.bind()
        shader.uniform_float('color', COLOR)
        batch.draw(shader)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.depth_mask_set(mask)
        gpu.state.line_width_set(width)


def _labels():
    context = bpy.context
    for center, normal, length in guides(context):
        pos = location_3d_to_region_2d(context.region, context.region_data, center + normal * length * 1.1)
        if pos is not None:
            blf.size(0, 13 * context.preferences.system.ui_scale)
            blf.color(0, *COLOR)
            blf.position(0, pos.x + 5, pos.y + 4, 0)
            blf.draw(0, 'Climb Side')


def register():
    _handlers.append(bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW'))
    _handlers.append(bpy.types.SpaceView3D.draw_handler_add(_labels, (), 'WINDOW', 'POST_PIXEL'))


def unregister():
    for handler in _handlers:
        bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
    _handlers.clear()
