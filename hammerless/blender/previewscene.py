"""Preview visibility policies; invisible solid faces can still cast shadows."""
from ..core.lightmap import NON_RENDERING_CLASSES, NON_RENDERING_MATERIALS

# Clip/trigger/portal volumes do not have VRAD's MASK_OPAQUE contents.
# NODRAW and BLOCKLIGHT are deliberately NOT in this set.
NON_SHADOW_TOOLS = {
    'tools/fogvolume', 'tools/toolsareaportal', 'tools/toolsclip',
    'tools/toolsnpcclip', 'tools/toolsplayerclip', 'tools/toolshint',
    'tools/toolsskip', 'tools/toolsorigin', 'tools/toolstrigger',
    'tools/toolsinvisibleladder', 'tools/toolsblock_los', 'tools/toolsoccluder',
}


def material_path(mat):
    if mat is None:
        return ''
    return (mat.hammerless.source_material or mat.name).lower().replace('\\','/').removeprefix('materials/').removesuffix('.vmt')


def draws_material(mat):
    return material_path(mat) not in NON_RENDERING_MATERIALS


def included_object(obj, view_layer):
    """Logical helper objects and their imported child brushes are not surfaces."""
    from .extract import effective_role
    role = effective_role(obj)
    if role == 'IGNORE':
        return False
    # Point-entity marker meshes are editing aids, not exported brush geometry.
    # Actual imported model meshes carry their model identity on the mesh data.
    if role == 'ENTITY' and not (obj.type == 'MESH' and obj.data.get('hl_model')):
        return False
    if not obj.visible_get(view_layer=view_layer):
        return False
    owner = obj
    while owner is not None:
        classname = owner.hammerless.classname.lower()
        if classname.startswith('trigger_') or classname in NON_RENDERING_CLASSES:
            return False
        owner = owner.parent
    return True
