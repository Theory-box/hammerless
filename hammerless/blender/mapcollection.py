"""The map collection: only what's inside the collection named after the map is built (exported, compiled,
baked, given nav and sound). Anything else in the scene (reference models, cameras, asset kits that collection
instances copy from) stays out of the map.

A scene made before this has no map collection: then the whole scene is the map, as it always was, and the build
log suggests Organize Scene. Organize Scene makes the collection and sorts what the map is built from into
collections by kind (the Add menu's categories); what isn't built stays outside.
"""
import bpy

KEY = "hl_map_collection"          # the scene's map collection (a pointer: renaming the map renames it)

# where each kind goes, under the map collection
WORLD, DETAIL, TERRAIN, LIGHTS, PROPS, MODELS = "World", "Detail", "Terrain", "Lights", "Props", "Custom Models"
ENTITIES, BRUSH_ENTITIES, VOLUMES, PREFABS, INSTANCES = "Entities", "Brush Entities", "Volumes", "Prefabs", "Instances"
VOLUME_CLASSES = {"hammerless_nav_region", "hammerless_nav_cut", "hammerless_no_bake", "hammerless_control"}


def map_collection(scene):
    """The scene's map collection, or None (a scene from before: the whole scene is the map)."""
    coll = scene.hammerless.map_collection
    if coll is not None and coll.name in bpy.data.collections and _linked(scene, coll):
        return coll
    return None


def _linked(scene, coll) -> bool:
    return coll in _all_children(scene.collection)


def _all_children(coll) -> set:
    out, stack = set(), list(coll.children)
    while stack:
        c = stack.pop()
        if c not in out:
            out.add(c)
            stack.extend(c.children)
    return out


def map_objects(scene) -> set:
    """Names of the objects the map is built from: the map collection's (with its sub-collections'), or the
    whole scene's when there's no map collection."""
    coll = map_collection(scene)
    objs = coll.all_objects if coll is not None else scene.objects
    return {o.name for o in objs}


def in_map(obj, names: set) -> bool:
    return obj.name in names


def outside_but_built(scene) -> list:
    """Objects outside the map collection that would be built if they were in it (for the build log)."""
    from .extract import effective_role
    coll = map_collection(scene)
    if coll is None:
        return []
    inside = map_objects(scene)
    sources = _instance_sources(scene)
    return sorted(o.name for o in scene.objects
                  if o.name not in inside and o.visible_get() and o.name not in sources
                  and effective_role(o) not in ("IGNORE", "NONE") and o.parent is None)


def _instance_sources(scene) -> set:
    """Objects in collections that collection instances copy (an asset kit): they're built only as copies."""
    out = set()
    for o in scene.objects:
        src = o.instance_collection if o.instance_type == "COLLECTION" else None
        if src is not None:
            out.update(x.name for x in src.all_objects)
    return out


def sync_name(scene) -> None:
    """The map collection follows the map's name."""
    coll = map_collection(scene)
    name = scene.hammerless.map_name
    if coll is not None and name and coll.name != name:
        coll.name = name


# ---------------------------------------------------------------- organizing

def _category(obj) -> str | None:
    """Where an object goes under the map collection ("A/B": a collection inside another), or None: it isn't
    built, so it stays where it is."""
    from ..core.entities import CATALOG
    from .extract import detail_choice, effective_role
    hs = obj.hammerless
    if hs.preset:
        return PREFABS
    if obj.instance_type == "COLLECTION" and obj.instance_collection is not None:
        return INSTANCES
    role = effective_role(obj)
    cls = hs.classname or ""
    if role == "BRUSH":
        return DETAIL if detail_choice(obj) == "DETAIL" else WORLD
    if role == "TERRAIN":
        return TERRAIN
    if role == "LIGHT":
        return LIGHTS
    if role == "MODEL":
        return MODELS
    if role == "BRUSH_ENTITY":
        return VOLUMES if cls in VOLUME_CLASSES else BRUSH_ENTITIES
    if role == "ENTITY":
        d = CATALOG.get(cls)
        if d is not None:
            if d.category == "Props":
                return PROPS
            if d.category == "Lights":
                return LIGHTS
            if d.category == "Brush Entities":
                return BRUSH_ENTITIES
            return f"{ENTITIES}/{d.category}"
        if cls.startswith("prop_"):
            return PROPS
        if cls.startswith("light"):
            return LIGHTS
        return f"{ENTITIES}/Other"
    return None


def _child(parent, name: str):
    """parent's sub-collection called name (made if missing; Blender may add .001 to keep names unique)."""
    for c in parent.children:
        if c.get("hl_category") == name:
            return c
    c = bpy.data.collections.new(name)
    c["hl_category"] = name
    parent.children.link(c)
    return c


def _freeze_settings(obj) -> None:
    """Roles and Detail settings that come from the object's collection go onto the object itself, so moving it
    out of that collection doesn't change how it's built."""
    from .extract import collection_role, detail_choice, effective_role
    hs = obj.hammerless
    if hs.role == "AUTO" and collection_role(obj) != "NONE":
        hs.role = effective_role(obj)
    if hs.brush_detail == "AUTO":
        choice = detail_choice(obj)
        if choice != "AUTO":
            hs.brush_detail = choice


def organize(context) -> dict:
    """Make the map collection and sort the scene into it. Returns counts for the report."""
    from .vmfimport import KIND
    scene = context.scene
    name = scene.hammerless.map_name or "my_map"
    coll = map_collection(scene)
    if coll is None:
        coll = bpy.data.collections.new(name)
        scene.collection.children.link(coll)
        scene.hammerless.map_collection = coll
    sync_name(scene)
    sources = _instance_sources(scene)
    moved = left = 0
    imported_tops = set()
    for obj in list(scene.objects):
        if obj.parent is not None:
            continue                  # (children go with their parent)
        if obj.get(KIND) is not None:
            # an imported map's objects: its own collection goes in whole (its brushes and entities are written
            # back from it)
            for c in obj.users_collection:
                top = _top_collection(scene, c)
                if top is not None and top is not coll and top not in _all_children(coll):
                    imported_tops.add(top)
            continue
        if obj.name in sources or not obj.visible_get():
            left += 1
            continue                  # (hidden or an asset kit's: not built, stays outside)
        cat = _category(obj)
        if cat is None:
            left += 1
            continue
        target = coll
        for part in cat.split("/"):
            target = _child(target, part)
        for o in [obj] + list(obj.children_recursive):
            _freeze_settings(o)
            if target not in o.users_collection:
                target.objects.link(o)
            for c in list(o.users_collection):
                if c is not target:
                    c.objects.unlink(o)
            moved += 1
    for top in imported_tops:
        for parent in [scene.collection] + list(_all_children(scene.collection)):
            if top.name in parent.children:
                parent.children.unlink(top)
        coll.children.link(top)
    removed = _drop_empty(scene, coll)
    _known[scene.name] = {o.session_uid for o in scene.objects}
    # new objects (Shift+A, the Add panel) land in the map
    lc = _layer_collection(context.view_layer.layer_collection, coll)
    if lc is not None:
        context.view_layer.active_layer_collection = lc
    return {"moved": moved, "left": left, "imported": len(imported_tops), "removed": removed}


def _top_collection(scene, coll):
    """The collection directly under the scene's that holds coll (or coll itself)."""
    for top in scene.collection.children:
        if top is coll or coll in _all_children(top):
            return top
    return None


def _drop_empty(scene, keep) -> int:
    """Remove collections left empty by organizing (no objects, nothing in them, no Hammerless settings)."""
    removed = 0
    changed = True
    while changed:
        changed = False
        for c in list(_all_children(scene.collection)):
            if c is keep or c.objects or c.children or c.users > 1:
                continue
            hs = getattr(c, "hammerless", None)
            if hs is not None and (hs.role != "NONE" or hs.brush_detail != "NONE"):
                continue
            if any(o.instance_collection == c for o in bpy.data.objects):
                continue
            bpy.data.collections.remove(c)
            removed += 1
            changed = True
    return removed


def _layer_collection(lc, coll):
    if lc.collection == coll:
        return lc
    for child in lc.children:
        found = _layer_collection(child, coll)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------- keeping it sorted

_known: dict = {}             # scene name -> its objects already seen (session ids: renaming isn't new)
_pending: dict = {}           # object session id -> True when new (else: its kind may have changed)
_timer = [False]


def _sorted_place(coll, c) -> bool:
    """A collection objects are sorted into: the map collection itself or one of its kind collections."""
    return c is coll or (c.get("hl_category") is not None and c in _all_children(coll))


def place(scene, obj, new: bool) -> bool:
    """Put one object (and the objects parented under it) where its kind goes. A new object is moved from
    anywhere; an existing one only from the map collection or its kind collections (not from collections you
    made inside the map). Returns whether it moved."""
    from .vmfimport import KIND
    coll = map_collection(scene)
    if coll is None or obj is None or obj.name not in scene.objects or obj.parent is not None:
        return False
    if obj.get(KIND) is not None or not obj.visible_get() or obj.name in _instance_sources(scene):
        return False
    cat = _category(obj)
    if cat is None:
        return False
    if not new and not all(_sorted_place(coll, c) for c in obj.users_collection):
        return False
    target = coll
    for part in cat.split("/"):
        target = _child(target, part)
    if list(obj.users_collection) == [target]:
        return False
    for o in [obj] + list(obj.children_recursive):
        if o is not obj and not new and not all(_sorted_place(coll, c) for c in o.users_collection):
            continue
        _freeze_settings(o)
        if target not in o.users_collection:
            target.objects.link(o)
        for c in list(o.users_collection):
            if c is not target:
                c.objects.unlink(o)
    return True


def _apply_pending():
    _timer[0] = False
    scene = bpy.context.scene
    by_uid = {o.session_uid: o for o in scene.objects}
    moved = 0
    for uid, new in list(_pending.items()):
        try:
            moved += place(scene, by_uid.get(uid), new)
        except (ReferenceError, RuntimeError):
            pass
    _pending.clear()
    if moved:
        try:
            bpy.ops.ed.undo_push(message="Hammerless: sort into the map collection")
        except RuntimeError:
            pass
    return None


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if map_collection(scene) is None:
        return
    ids = {o.session_uid: o for o in scene.objects}
    known = _known.get(scene.name)
    _known[scene.name] = set(ids)
    if known is None:
        return                        # (first look at this scene: what's there stays where it is)
    for uid in ids.keys() - known:
        _pending[uid] = True
    for u in depsgraph.updates:
        if isinstance(u.id, bpy.types.Object) and not u.is_updated_transform:
            _pending.setdefault(u.id.original.session_uid, False)
    if _pending and not _timer[0]:
        _timer[0] = True
        bpy.app.timers.register(_apply_pending, first_interval=0.0)


@bpy.app.handlers.persistent
def _on_load(*_args):
    _known.clear()
    _pending.clear()
    convert_old_volumes()


def convert_old_volumes() -> int:
    """No Nav Volumes and No Bake Volumes become Control Volumes doing the same (nav, Not inside; light, its
    mode). Returns how many."""
    from ..core.entities import CONTROL, NAV_CUT, NO_BAKE
    n = 0
    for o in bpy.data.objects:
        cls = o.hammerless.classname
        if cls not in (NAV_CUT, NO_BAKE):
            continue
        kv = {k.key: k.value for k in o.hammerless.keyvalues}
        only = cls == NO_BAKE and kv.get("invert", "0").strip() in ("1", "true", "True")
        values = {"mode": "ONLY" if only else "EXCLUDE", "nav": "1" if cls == NAV_CUT else "0",
                  "light": "1" if cls == NO_BAKE else "0", "vis": "0", "sound": "0", "spawns": "0"}
        o.hammerless.keyvalues.clear()
        for k, v in values.items():
            item = o.hammerless.keyvalues.add()
            item.key, item.value = k, v
        o.hammerless.classname = CONTROL
        n += 1
    return n


class HL_OT_organize_scene(bpy.types.Operator):
    bl_idname = "hammerless.organize_scene"
    bl_label = "Organize Scene"
    bl_description = ("Put the map in a collection named after it, sorted by kind (world, detail, terrain, lights, "
                      "props, entities, volumes, prefabs, instances; an imported map whole). Only what's in it is "
                      "built: things that aren't built (cameras, reference objects, hidden ones, asset kits) stay "
                      "outside. Ctrl+Z undoes it")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        r = organize(context)
        coll = map_collection(context.scene)
        self.report({"INFO"}, f"Organized into '{coll.name}': {r['moved']} object(s) sorted"
                              + (f", {r['imported']} imported map(s) moved in" if r["imported"] else "")
                              + (f"; {r['left']} not built, left outside" if r["left"] else ""))
        return {"FINISHED"}


CLASSES = (HL_OT_organize_scene,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_post.append(_on_load)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
