"""The Texturing panel: Hammer's Texture Browser and Face Edit, in the sidebar.

Pick a texture in the browser, then Paint Faces: click a face to give it the texture (Shift+click: the whole object,
Alt+click: take the face's texture and alignment), right-click or Esc to stop. Apply to Selected does the faces
selected in Edit Mode (or every face of the selected objects). Alignment (scale, shift, rotation, World or Face,
justify) changes the selected faces in Edit Mode, else the last face painted, as you edit it. Replace swaps one
texture for another across the map.

A painted face keeps its alignment in its UVs and is marked (face attribute hl_tex_uv): the exporter writes Hammer's
texture axes from them (core/texalign.py), so the viewport (Material Preview) shows what the game shows. Faces never
painted stay world-aligned as before.
"""
import os

import bmesh
import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from ..core import texalign
from .extract import PAINTED, texture_size

PAGE = 16                         # thumbnails a page (4 x 4)
_previews = {"coll": None}
_lists = {"key": None, "all": [], "folders": []}
_guard = [False]                  # (reading a face back into the settings: don't re-apply)


# ---------------------------------------------------------------- the game's materials

def _content(context):
    from .ops import game_content, game_root
    root = game_root(context)
    return game_content(root), (os.path.join(root, "left4dead2") if root else None)


def all_materials(context) -> list[str]:
    content, _gd = _content(context)
    key = id(content)
    if _lists["key"] != key:
        names = sorted(content.materials()) if content else []
        names = [n for n in names if not n.startswith(("skybox/", "debug/", "editor/", "engine/", "vgui/", "hud/",
                                                        "console/", "particle/", "effects/", "sprites/", "models/"))]
        _lists.update(key=key, all=names, folders=sorted({n.split("/")[0] for n in names if "/" in n}))
    return _lists["all"]


def used_materials(context) -> list[str]:
    from .mapcollection import map_objects
    inside = map_objects(context.scene)
    out = set()
    for o in context.scene.objects:
        if o.name in inside and o.type == "MESH":
            for slot in o.material_slots:
                m = slot.material
                if m is not None:
                    path = (m.hammerless.source_material or "").strip().lower()
                    if path:
                        out.add(path)
    return sorted(out)


def _split(text: str) -> list[str]:
    return [t for t in (text or "").split(",") if t]


_listing_cache = {"key": None, "names": []}


def listing(context) -> list[str]:
    import time
    t = context.scene.hl_tex
    key = (t.filter, t.folder, t.search, t.favorites if t.filter == "FAV" else "", t.recent if t.filter == "RECENT" else "",
           int(time.monotonic() // 2) if t.filter == "USED" else 0)
    if _listing_cache["key"] == key:
        return _listing_cache["names"]
    names = _listing(context)
    _listing_cache.update(key=key, names=names)
    return names


def _listing(context) -> list[str]:
    t = context.scene.hl_tex
    if t.filter == "USED":
        names = used_materials(context)
    elif t.filter == "FAV":
        names = _split(t.favorites)
    elif t.filter == "RECENT":
        names = _split(t.recent)
    else:
        names = all_materials(context)
        if t.folder != "ALL":
            names = [n for n in names if n.startswith(t.folder + "/")]
    words = (t.search or "").lower().split()
    if words:
        names = [n for n in names if all(w in n for w in words)]
    return names


def thumbnail(context, path: str) -> int:
    """A small picture of a material's texture (icon id), made once."""
    if _previews["coll"] is None:
        _previews["coll"] = bpy.utils.previews.new()
    coll = _previews["coll"]
    if path in coll:
        return coll[path].icon_id
    prev = coll.new(path)
    try:
        import numpy as np
        from ..core.gamematerials import base_texture, read_texture_bytes
        from ..core.vtf_read import read_vtf
        content, gd = _content(context)
        tex = base_texture(content, path, gd) if content else None
        data = read_texture_bytes(content, tex, gd) if tex else None
        if data:
            _w, _h, rgba = read_vtf(data, 64)
            h, w = rgba.shape[:2]
            px = (rgba[::-1].astype(np.float32) / 255.0)
            px[..., 3] = 1.0
            prev.image_size = (w, h)
            prev.image_pixels_float = px.ravel()
            ys = (np.arange(32) * h // 32).clip(0, h - 1)       # (the small icon buttons use)
            xs = (np.arange(32) * w // 32).clip(0, w - 1)
            prev.icon_size = (32, 32)
            prev.icon_pixels_float = px[ys][:, xs].ravel()
    except Exception:
        pass
    return prev.icon_id


# ---------------------------------------------------------------- painting faces

def material_for(path: str):
    """The Blender material for a game material, its preview showing painted faces through their UVs."""
    from .ops import game_material, refresh_material_preview
    mat = game_material(path)
    if not mat.get("hl_uvmix") and mat.use_nodes and mat.node_tree:
        from .preview import NODE_TAG
        nodes = mat.node_tree.nodes
        if (any(n.type == "TEX_IMAGE" for n in nodes) and not any(n.get(NODE_TAG) for n in nodes)):
            mat["hl_uvmix"] = 1            # (an imported map's: its faces show through their UVs already)
    if not mat.get("hl_uvmix") and not mat.get("hl_uvmix_tried") and not path.startswith("tools/"):
        mat["hl_uvmix_tried"] = 1          # (once: a texture the game doesn't have would be read every click)
        refresh_material_preview(mat)
    return mat


def _slot(obj, mat) -> int:
    for i, s in enumerate(obj.material_slots):
        if s.material == mat:
            return i
    if not obj.material_slots:          # (the other faces keep what they had: the map's default material)
        from .ops import game_material
        default = bpy.context.scene.hammerless.default_material or "dev/dev_measuregeneric01b"
        obj.data.materials.append(game_material(default))
        if game_material(default) == mat:
            return 0
    obj.data.materials.append(mat)
    return len(obj.material_slots) - 1


def _paintable(obj) -> bool:
    hs = obj.hammerless
    return (obj.type == "MESH" and not obj.data.name.startswith("HL_") and hs.role not in ("ENTITY", "MODEL")
            and obj.get("hl_vmf_kind") != "disp")


def current_alignment(context) -> texalign.Alignment:
    t = context.scene.hl_tex
    return texalign.Alignment(t.scale_u, t.scale_v if not t.lock_scale else t.scale_u, t.shift_u, t.shift_v,
                              t.rotation, t.mode)


def _upm(context) -> float:
    return context.scene.hammerless.units_per_meter


def _outside(pts, m):
    """The corners counter-clockwise from outside (a mirrored object's world winding is reversed)."""
    return pts[::-1] if m.determinant() < 0 else pts


def _align_bm_face(f, uvl, pl, m, upm, al, mat, set_flag=True):
    pts = [tuple(c * upm for c in (m @ v.co)) for v in f.verts]
    ua, va = texalign.axes(_outside(pts, m), al)
    w, h = texture_size(mat, remember=True)
    for loop, uv in zip(f.loops, texalign.uvs(pts, ua, va, w, h)):
        loop[uvl].uv = uv
    if set_flag:
        f[pl] = 1


def paint_faces(context, obj, faces=None, material=None, al=None, fresh=True) -> int:
    """Give faces of an object (indices; None: all) the material (None: keep theirs) and alignment. Object or
    Edit Mode. Returns how many faces. fresh: update the scene first (a just-moved object's matrix may lag; the
    paint tool's clicks don't need it)."""
    al = al or current_alignment(context)
    if fresh and obj.mode != "EDIT":
        context.view_layer.update()
    edit = obj.mode == "EDIT"
    bm = bmesh.from_edit_mesh(obj.data) if edit else bmesh.new()
    if not edit:
        bm.from_mesh(obj.data)
    try:
        uvl = bm.loops.layers.uv.active or bm.loops.layers.uv.new("UVMap")
        pl = bm.faces.layers.int.get(PAINTED) or bm.faces.layers.int.new(PAINTED)
        bm.faces.ensure_lookup_table()
        idx = None
        if material is not None:
            idx = _slot(obj, material)
        targets = [bm.faces[i] for i in faces if i < len(bm.faces)] if faces is not None else list(bm.faces)
        m = obj.matrix_world
        upm = _upm(context)
        for f in targets:
            if idx is not None:
                f.material_index = idx
            mat = obj.material_slots[f.material_index].material if f.material_index < len(obj.material_slots) else None
            _align_bm_face(f, uvl, pl, m, upm, al, mat)
        if edit:
            bmesh.update_edit_mesh(obj.data)
        else:
            bm.to_mesh(obj.data)
            obj.data.update()
        return len(targets)
    finally:
        if not edit:
            bm.free()


def selected_targets(context) -> list[tuple]:
    """(object, face indices or None) the panel's buttons work on: selected faces in Edit Mode, else every face of
    the selected objects."""
    out = []
    if context.mode == "EDIT_MESH":
        for obj in context.objects_in_mode_unique_data:
            if not _paintable(obj):
                continue
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.index_update()
            sel = [f.index for f in bm.faces if f.select]
            if sel:
                out.append((obj, sel))
    else:
        out = [(o, None) for o in context.selected_objects if _paintable(o)]
    return out


def read_face(context, obj, index) -> None:
    """The face's texture and alignment into the settings (Alt+click, Pick from Face)."""
    mesh = obj.data
    if obj.mode == "EDIT":
        obj.update_from_editmode()
    poly = mesh.polygons[index]
    mat = obj.material_slots[poly.material_index].material if poly.material_index < len(obj.material_slots) else None
    t = context.scene.hl_tex
    path = (mat.hammerless.source_material or "").strip().lower() if mat else ""
    if path:
        set_active(context, path)
    painted = mesh.attributes.get(PAINTED)
    flag = painted.data[index].value if painted is not None and painted.domain == "FACE" else 0
    upm = _upm(context)
    pts = [tuple(c * upm for c in (obj.matrix_world @ mesh.vertices[v].co)) for v in poly.vertices]
    uv = mesh.uv_layers.active
    al = None
    if flag and uv is not None:
        w, h = texture_size(mat)
        ax = texalign.axes_from_uv(pts, [tuple(uv.data[li].uv) for li in poly.loop_indices], w, h)
        if ax is not None:
            al = texalign.alignment_from_axes(_outside(pts, obj.matrix_world), *ax, mode=t.mode)
    if al is None:
        s = mat.hammerless.texture_scale if mat else 0.25
        al = texalign.Alignment(s, s, 0.0, 0.0, 0.0, "WORLD")
    _guard[0] = True
    try:
        t.mode = al.mode
        t.lock_scale = abs(al.scale_u - al.scale_v) < 1e-6
        t.scale_u, t.scale_v = round(al.scale_u, 4), round(al.scale_v, 4)
        t.shift_u, t.shift_v = round(al.shift_u, 3), round(al.shift_v, 3)
        t.rotation = round(al.rotation, 3)
    finally:
        _guard[0] = False


def set_active(context, path: str) -> None:
    t = context.scene.hl_tex
    t.active = path
    recent = [path] + [r for r in _split(t.recent) if r != path]
    t.recent = ",".join(recent[:24])


def _remember_last(context, obj, index):
    context.scene["hl_tex_last_obj"] = obj.name
    context.scene["hl_tex_last_face"] = int(index)


def last_painted(context):
    """(object, face index) last painted, or (None, None)."""
    obj = bpy.data.objects.get(context.scene.get("hl_tex_last_obj", ""))
    i = context.scene.get("hl_tex_last_face", -1)
    if obj is None or obj.type != "MESH" or not 0 <= i < len(obj.data.polygons):
        return None, None
    return obj, i


def _on_align(self, context):
    """Alignment changed in the panel: re-align the selected faces (Edit Mode), else the last face painted."""
    if _guard[0]:
        return
    if self.lock_scale and abs(self.scale_v - self.scale_u) > 1e-9:
        _guard[0] = True
        self.scale_v = self.scale_u
        _guard[0] = False
    al = current_alignment(context)
    targets = []
    if context.mode == "EDIT_MESH":
        targets = selected_targets(context)
    else:
        obj, i = last_painted(context)
        if obj is not None:
            targets = [(obj, [i])]
    for obj, faces in targets:
        if faces is not None:
            paint_faces(context, obj, faces, None, al)


# ---------------------------------------------------------------- operators

def _view_under_mouse(context, event):
    """The 3D view's main region under the mouse (the tool starts from the sidebar), or None."""
    for area in context.window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if (region.type == "WINDOW" and region.x <= event.mouse_x < region.x + region.width
                    and region.y <= event.mouse_y < region.y + region.height):
                return region
    return None


def _ray(context, event):
    from bpy_extras import view3d_utils
    region = _view_under_mouse(context, event)
    if region is None or region.data is None:
        return None, None
    rv3d = region.data
    co = (event.mouse_x - region.x, event.mouse_y - region.y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, co)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, co)
    depsgraph = context.evaluated_depsgraph_get()
    hit, _loc, _n, index, obj, _m = context.scene.ray_cast(depsgraph, origin, direction)
    if not hit or obj is None:
        return None, None
    obj = obj.original
    if obj.type != "MESH":
        return None, None
    if any(md.show_viewport for md in obj.modifiers):
        # (the hit's index is the modified mesh's: find the face of the object's own mesh under the mouse)
        from mathutils.bvhtree import BVHTree
        me = obj.data
        m = obj.matrix_world
        tree = BVHTree.FromPolygons([m @ v.co for v in me.vertices], [tuple(p.vertices) for p in me.polygons])
        _loc, _n, index, _d = tree.ray_cast(origin, direction)
        if index is None:
            return None, None
    if index >= len(obj.data.polygons):
        return None, None
    return obj, index


def _snapshot(obj, faces):
    """What painting these faces changes (materials, UVs, the painted mark), to put back."""
    me = obj.data
    idx = list(range(len(me.polygons))) if faces is None else list(faces)
    uvl = me.uv_layers.active
    pa = me.attributes.get(PAINTED)
    return {
        "obj": obj.name, "slots": len(obj.material_slots), "faces": idx,
        "mats": [me.polygons[i].material_index for i in idx],
        "uvs": ({li: tuple(uvl.data[li].uv) for i in idx for li in me.polygons[i].loop_indices}
                if uvl is not None else None),
        "flags": [pa.data[i].value for i in idx] if pa is not None and pa.domain == "FACE" else None,
    }


def _restore(snap) -> bool:
    obj = bpy.data.objects.get(snap["obj"])
    if obj is None or obj.type != "MESH":
        return False
    me = obj.data
    for i, m in zip(snap["faces"], snap["mats"]):
        if i < len(me.polygons):
            me.polygons[i].material_index = m
    uvl = me.uv_layers.active
    if uvl is not None and snap["uvs"] is not None:
        for li, uv in snap["uvs"].items():
            if li < len(uvl.data):
                uvl.data[li].uv = uv
    pa = me.attributes.get(PAINTED)
    if pa is not None:
        for k, i in enumerate(snap["faces"]):
            if i < len(pa.data):
                pa.data[i].value = snap["flags"][k] if snap["flags"] is not None else 0
    while len(obj.material_slots) > snap["slots"]:          # (a slot the click added, now unused)
        used = {p.material_index for p in me.polygons}
        if len(obj.material_slots) - 1 in used:
            break
        me.materials.pop()
    me.update()
    return True


class HL_OT_tex_paint(bpy.types.Operator):
    bl_idname = "hammerless.tex_paint"
    bl_label = "Paint Faces"
    bl_description = ("Click faces to give them the active texture with the alignment below. Shift+click: the whole "
                      "object. Alt+click: take a face's texture and alignment. Right-click or Esc: stop")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        if not context.scene.hl_tex.active:
            self.report({"WARNING"}, "Pick a texture in the browser first")
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        context.window.cursor_modal_set("PAINT_BRUSH")
        context.area.header_text_set("Paint Faces: click a face   Shift+click: whole object   Alt+click: pick   "
                                     "Ctrl+Z: undo a click   Right-click / Esc: stop")
        self._undo = []
        self._mats = {}                     # (each texture's material, looked up once a session)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _stop(self, context):
        context.window.cursor_modal_restore()
        context.area.header_text_set(None)
        return {"FINISHED"}

    def modal(self, context, event):
        if event.type in ("RIGHTMOUSE", "ESC") and event.value == "PRESS":
            return self._stop(context)
        if event.type == "Z" and event.value == "PRESS" and (event.ctrl or event.oskey) and not event.shift:
            if self._undo and _restore(self._undo.pop()):
                context.area.tag_redraw()
                self.report({"INFO"}, f"Undid a click ({len(self._undo)} left)")
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if _view_under_mouse(context, event) is None:
                return {"PASS_THROUGH"}         # (a click on the panel, another editor...)
            obj, index = _ray(context, event)
            if obj is None or not _paintable(obj):
                return {"RUNNING_MODAL"}
            if event.alt:
                read_face(context, obj, index)
                self.report({"INFO"}, f"Picked {context.scene.hl_tex.active}")
                return {"RUNNING_MODAL"}
            path = context.scene.hl_tex.active
            mat = self._mats.get(path) or self._mats.setdefault(path, material_for(path))
            faces = None if event.shift else [index]
            # (no undo step a click: on a big scene Blender's takes half a second. The tool keeps its own,
            # Ctrl+Z; the whole session is one undo step when it ends)
            self._undo.append(_snapshot(obj, faces))
            paint_faces(context, obj, faces, mat, fresh=False)
            _remember_last(context, obj, index)
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}         # (navigating the view, the panel)


class HL_OT_tex_apply(bpy.types.Operator):
    bl_idname = "hammerless.tex_apply"
    bl_label = "Apply to Selected"
    bl_description = ("Give the active texture, with the alignment below, to the faces selected in Edit Mode, or to "
                      "every face of the selected objects")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if not context.scene.hl_tex.active:
            self.report({"WARNING"}, "Pick a texture in the browser first")
            return {"CANCELLED"}
        mat = material_for(context.scene.hl_tex.active)
        n = sum(paint_faces(context, obj, faces, mat) for obj, faces in selected_targets(context))
        if not n:
            self.report({"WARNING"}, "Select faces (Edit Mode) or objects first")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{n} face(s): {context.scene.hl_tex.active}")
        return {"FINISHED"}


class HL_OT_tex_pick(bpy.types.Operator):
    bl_idname = "hammerless.tex_pick"
    bl_label = "Pick from Face"
    bl_description = "Take the active face's texture and alignment (Edit Mode); or Alt+click a face while painting"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.object
        if obj is None or obj.type != "MESH" or obj.mode != "EDIT":
            self.report({"WARNING"}, "In Edit Mode, make a face active first (or Alt+click one while painting)")
            return {"CANCELLED"}
        bm = bmesh.from_edit_mesh(obj.data)
        f = bm.faces.active
        if f is None:
            self.report({"WARNING"}, "No active face")
            return {"CANCELLED"}
        read_face(context, obj, f.index)
        return {"FINISHED"}


class HL_OT_tex_set(bpy.types.Operator):
    bl_idname = "hammerless.tex_set"
    bl_label = "Use Texture"
    bl_description = "Make this the active texture"
    path: StringProperty()

    def execute(self, context):
        set_active(context, self.path)
        return {"FINISHED"}


class HL_OT_tex_favorite(bpy.types.Operator):
    bl_idname = "hammerless.tex_favorite"
    bl_label = "Favourite"
    bl_description = "Star the active texture (the browser's Favourites)"

    def execute(self, context):
        t = context.scene.hl_tex
        favs = _split(t.favorites)
        if t.active in favs:
            favs.remove(t.active)
        elif t.active:
            favs.append(t.active)
        t.favorites = ",".join(favs)
        return {"FINISHED"}


class HL_OT_tex_page(bpy.types.Operator):
    bl_idname = "hammerless.tex_page"
    bl_label = "Page"
    bl_description = "Next or previous page of textures"
    step: IntProperty(default=1)

    def execute(self, context):
        t = context.scene.hl_tex
        pages = max(1, (len(listing(context)) + PAGE - 1) // PAGE)
        t.page = max(0, min(pages - 1, t.page + self.step))
        return {"FINISHED"}


class HL_OT_tex_justify(bpy.types.Operator):
    bl_idname = "hammerless.tex_justify"
    bl_label = "Justify"
    bl_description = "Line the texture up with the face's edges (or Fit: one copy exactly covering the face)"
    bl_options = {"REGISTER", "UNDO"}
    how: EnumProperty(items=[(k, k.title(), "") for k in ("LEFT", "RIGHT", "TOP", "BOTTOM", "CENTER", "FIT")])

    def execute(self, context):
        targets = selected_targets(context) if context.mode == "EDIT_MESH" else []
        if not targets:
            obj, i = last_painted(context)
            if obj is not None:
                targets = [(obj, [i])]
        if not targets:
            self.report({"WARNING"}, "Select faces in Edit Mode, or paint one first")
            return {"CANCELLED"}
        upm = _upm(context)
        last_al = None
        for obj, faces in targets:
            if faces is None:
                continue
            if obj.mode == "EDIT":
                obj.update_from_editmode()
            for i in faces:
                poly = obj.data.polygons[i]
                mat = obj.material_slots[poly.material_index].material if poly.material_index < len(obj.material_slots) else None
                pts = [tuple(c * upm for c in (obj.matrix_world @ obj.data.vertices[v].co)) for v in poly.vertices]
                w, h = texture_size(mat, remember=True)
                al = texalign.justify(_outside(pts, obj.matrix_world), current_alignment(context), self.how, w, h)
                paint_faces(context, obj, [i], None, al)
                last_al = al
        if last_al is not None:                 # (the settings show the result)
            t = context.scene.hl_tex
            _guard[0] = True
            try:
                t.lock_scale = abs(last_al.scale_u - last_al.scale_v) < 1e-6
                t.scale_u, t.scale_v = round(last_al.scale_u, 4), round(last_al.scale_v, 4)
                t.shift_u, t.shift_v = round(last_al.shift_u, 3), round(last_al.shift_v, 3)
            finally:
                _guard[0] = False
        return {"FINISHED"}


class HL_OT_tex_turn(bpy.types.Operator):
    bl_idname = "hammerless.tex_turn"
    bl_label = "Turn"
    bl_description = "Rotate the texture 90 degrees, or flip it"
    bl_options = {"REGISTER", "UNDO"}
    how: EnumProperty(items=[("ROT", "Rotate 90", ""), ("FLIPU", "Flip U", ""), ("FLIPV", "Flip V", "")])

    def execute(self, context):
        t = context.scene.hl_tex
        if self.how == "ROT":
            t.rotation = (t.rotation + 90.0) % 360.0
        elif self.how == "FLIPU":
            t.lock_scale = False
            t.scale_u = -t.scale_u
        else:
            t.lock_scale = False
            t.scale_v = -t.scale_v
        return {"FINISHED"}


def _used_items(self, context):
    items = [(p, p, "") for p in used_materials(context)] or [("", "(no textures in the map)", "")]
    _used_items.cache = items
    return items


class HL_OT_tex_replace(bpy.types.Operator):
    bl_idname = "hammerless.tex_replace"
    bl_label = "Replace in Map"
    bl_description = "Every face in the map using the Find texture gets the active texture (alignment kept)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .mapcollection import map_objects
        t = context.scene.hl_tex
        if not t.replace_from or not t.active:
            self.report({"WARNING"}, "Choose the texture to find, and an active texture to put instead")
            return {"CANCELLED"}
        new = material_for(t.active)
        inside = map_objects(context.scene)
        n = 0
        done = set()
        for o in context.scene.objects:
            if o.name not in inside or o.type != "MESH" or not _paintable(o) or o.data in done:
                continue
            done.add(o.data)
            for si, slot in enumerate(o.material_slots):
                m = slot.material
                if m is None or (m.hammerless.source_material or "").strip().lower() != t.replace_from:
                    continue
                (ow, oh), (nw, nh) = texture_size(m), texture_size(new, remember=True)
                if (ow, oh) != (nw, nh):     # (painted faces' UVs are in texture repeats: keep their texels)
                    me = o.data
                    pa = me.attributes.get(PAINTED)
                    uvl = me.uv_layers.active
                    if pa is not None and uvl is not None:
                        for p in me.polygons:
                            if p.material_index == si and pa.data[p.index].value:
                                for li in p.loop_indices:
                                    u, v = uvl.data[li].uv
                                    uvl.data[li].uv = (u * ow / nw, v * oh / nh)
                slot.material = new
                n += 1
        self.report({"INFO"}, f"Replaced {t.replace_from} with {t.active} on {n} object(s)")
        return {"FINISHED"}


class HL_OT_tex_select_using(bpy.types.Operator):
    bl_idname = "hammerless.tex_select_using"
    bl_label = "Select Objects Using It"
    bl_description = "Select every object in the map with the Find texture on any face"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from .mapcollection import map_objects
        t = context.scene.hl_tex
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        inside = map_objects(context.scene)
        n = 0
        for o in context.scene.objects:
            use = (o.name in inside and o.type == "MESH" and o.visible_get()
                   and any(s.material is not None and (s.material.hammerless.source_material or "").strip().lower()
                           == t.replace_from for s in o.material_slots))
            o.select_set(use)
            n += use
        self.report({"INFO"}, f"{n} object(s) use {t.replace_from}")
        return {"FINISHED"}


# ---------------------------------------------------------------- settings and panel

def _folder_items(self, context):
    all_materials(context)
    items = [("ALL", "All Folders", "")] + [(f, f, "") for f in _lists["folders"]]
    _folder_items.cache = items
    return items


def _reset_page(self, context):
    self.page = 0


class HL_TexSettings(bpy.types.PropertyGroup):
    active: StringProperty(name="Texture", description="The texture Paint Faces and Apply to Selected give faces")
    scale_u: FloatProperty(name="Scale U", default=0.25, step=1, precision=3, update=_on_align,
                           description="Hammer units per texel across (0.25: Hammer's default)")
    scale_v: FloatProperty(name="Scale V", default=0.25, step=1, precision=3, update=_on_align,
                           description="Hammer units per texel down")
    lock_scale: BoolProperty(name="Same Scale", default=True, update=_on_align,
                             description="Scale V follows Scale U")
    shift_u: FloatProperty(name="Shift U", default=0.0, step=100, precision=1, update=_on_align,
                           description="Slide the texture across, in texels")
    shift_v: FloatProperty(name="Shift V", default=0.0, step=100, precision=1, update=_on_align,
                           description="Slide the texture down, in texels")
    rotation: FloatProperty(name="Rotation", default=0.0, step=1500, precision=1, update=_on_align,
                            description="Turn the texture on the face, in degrees")
    mode: EnumProperty(name="Align", default="WORLD", update=_on_align, items=[
        ("WORLD", "World", "Projected from the world's axes (like Hammer's World alignment): textures line up "
                           "across neighbouring faces"),
        ("FACE", "Face", "In the face's own plane (Hammer's Face alignment): no stretching on slopes")])
    search: StringProperty(name="Search", options={"TEXTEDIT_UPDATE"}, update=_reset_page,
                           description="Words in the texture's path (all of them)")
    filter: EnumProperty(name="Show", default="ALL", update=_reset_page, items=[
        ("ALL", "All", "Every texture in the game"), ("USED", "In Map", "Textures the map uses"),
        ("FAV", "Favourites", "Starred textures"), ("RECENT", "Recent", "Textures used lately")])
    folder: EnumProperty(name="Folder", items=_folder_items, update=_reset_page)
    page: IntProperty(default=0, min=0)
    favorites: StringProperty(options={"HIDDEN"})
    recent: StringProperty(options={"HIDDEN"})
    replace_from: EnumProperty(name="Find", items=_used_items, description="The texture to replace")


def _status(layout, text):
    row = layout.row()
    row.label(text=text)


class HL_PT_texturing(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Texturing"
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header_preset(self, context):
        t = context.scene.hl_tex
        if t.active:
            _status(self.layout, t.active.split("/")[-1])

    def draw(self, context):
        t = context.scene.hl_tex
        layout = self.layout
        box = layout.box()
        row = box.row()
        if t.active:
            row.template_icon(icon_value=thumbnail(context, t.active), scale=3.0)
            col = row.column(align=True)
            col.label(text=t.active.split("/")[-1])
            col.label(text=os.path.dirname(t.active) + "/")
            fav = t.active in _split(t.favorites)
            col.operator("hammerless.tex_favorite", text="Unstar" if fav else "Star",
                         icon="SOLO_ON" if fav else "SOLO_OFF")
        else:
            row.label(text="Pick a texture in the browser below", icon="TEXTURE")
        big = layout.row()
        big.scale_y = 1.4
        big.operator("hammerless.tex_paint", icon="BRUSH_DATA")
        row = layout.row(align=True)
        row.operator("hammerless.tex_apply", icon="CHECKMARK")
        row.operator("hammerless.tex_pick", icon="EYEDROPPER")


class HL_PT_tex_browser(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Browser"
    bl_parent_id = "HL_PT_texturing"

    def draw_header_preset(self, context):
        _status(self.layout, f"{len(listing(context)):,}")

    def draw(self, context):
        t = context.scene.hl_tex
        layout = self.layout
        layout.prop(t, "search", text="", icon="VIEWZOOM")
        layout.row().prop(t, "filter", expand=True)
        if t.filter == "ALL":
            layout.prop(t, "folder", text="")
        names = listing(context)
        pages = max(1, (len(names) + PAGE - 1) // PAGE)
        page = min(t.page, pages - 1)
        grid = layout.grid_flow(row_major=True, columns=4, even_columns=True, even_rows=True, align=False)
        for path in names[page * PAGE:(page + 1) * PAGE]:
            col = grid.column(align=True)
            col.template_icon(icon_value=thumbnail(context, path), scale=3.2)
            name = path.split("/")[-1]
            op = col.operator("hammerless.tex_set", text=name if len(name) <= 11 else "…" + name[-10:],
                              depress=path == t.active)
            op.path = path
        if not names:
            layout.label(text="No textures match", icon="INFO")
        row = layout.row(align=True)
        row.operator("hammerless.tex_page", text="", icon="TRIA_LEFT").step = -1
        row.label(text=f"{page + 1} / {pages}")
        row.operator("hammerless.tex_page", text="", icon="TRIA_RIGHT").step = 1


class HL_PT_tex_align(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Alignment"
    bl_parent_id = "HL_PT_texturing"

    def draw_header_preset(self, context):
        if context.mode == "EDIT_MESH":
            n = sum(len(f) for _o, f in selected_targets(context) if f)
            _status(self.layout, f"{n} face(s)")
        elif last_painted(context)[0] is not None:
            _status(self.layout, "last painted")

    def draw(self, context):
        t = context.scene.hl_tex
        layout = self.layout
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        row = col.row(align=True)
        row.prop(t, "scale_u", text="Scale")
        sub = row.row(align=True)
        sub.enabled = not t.lock_scale
        sub.prop(t, "scale_v", text="")
        row.prop(t, "lock_scale", text="", icon="LINKED" if t.lock_scale else "UNLINKED")
        row = col.row(align=True)
        row.prop(t, "shift_u", text="Shift")
        row.prop(t, "shift_v", text="")
        col.prop(t, "rotation")
        col.row().prop(t, "mode", expand=True)
        grid = layout.grid_flow(columns=3, align=True)
        for how, label in (("LEFT", "Left"), ("RIGHT", "Right"), ("CENTER", "Center"), ("TOP", "Top"),
                           ("BOTTOM", "Bottom"), ("FIT", "Fit")):
            grid.operator("hammerless.tex_justify", text=label).how = how
        row = layout.row(align=True)
        row.operator("hammerless.tex_turn", text="Rotate 90", icon="FILE_REFRESH").how = "ROT"
        row.operator("hammerless.tex_turn", text="Flip U").how = "FLIPU"
        row.operator("hammerless.tex_turn", text="Flip V").how = "FLIPV"


class HL_PT_tex_replace(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hammerless"
    bl_label = "Replace"
    bl_parent_id = "HL_PT_texturing"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        t = context.scene.hl_tex
        layout = self.layout
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(t, "replace_from")
        col.label(text=f"With: {t.active or '(pick a texture)'}")
        row = layout.row(align=True)
        row.operator("hammerless.tex_select_using", icon="RESTRICT_SELECT_OFF")
        row.operator("hammerless.tex_replace", icon="FILE_REFRESH")


CLASSES = (HL_TexSettings, HL_OT_tex_paint, HL_OT_tex_apply, HL_OT_tex_pick, HL_OT_tex_set, HL_OT_tex_favorite,
           HL_OT_tex_page, HL_OT_tex_justify, HL_OT_tex_turn, HL_OT_tex_replace, HL_OT_tex_select_using,
           HL_PT_texturing, HL_PT_tex_browser, HL_PT_tex_align, HL_PT_tex_replace)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.hl_tex = bpy.props.PointerProperty(type=HL_TexSettings)


def unregister():
    del bpy.types.Scene.hl_tex
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    if _previews["coll"] is not None:
        bpy.utils.previews.remove(_previews["coll"])
        _previews["coll"] = None
