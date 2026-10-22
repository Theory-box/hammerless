"""Resolve an imported brush's entity independently of hierarchy and display names."""
from . import vmfimport as vi

def entity_object(context, obj=None):
    obj = context.object if obj is None else obj
    if obj is None or obj.type != 'MESH' or obj.get(vi.KIND) not in ('solid', 'disp'):
        return obj
    scene = context.scene
    if not vi.imported(scene):
        return obj
    from .mapcollection import map_objects
    inside = map_objects(scene)
    if obj.name not in inside:
        return obj
    objects = [o for o in scene.objects if o.name in inside]
    # Match exactly the original objects the exporter writes back. Copies remain independent.
    originals = vi._originals(objects, obj.get(vi.KIND))
    if originals.get(obj.get(vi.ID)) != obj:
        return obj
    index = obj.get('hl_vmf_owner_index')
    if index is None:
        return obj
    return vi._originals(objects, 'entity', vi.INDEX).get(index, obj)


def selected_entities(context):
    return list(dict.fromkeys(entity_object(context, o) for o in context.selected_objects))
