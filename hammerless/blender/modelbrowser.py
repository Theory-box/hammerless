"""Packaged model thumbnails and cursor placement; no rendering in the editor."""
import json
import time
from collections import OrderedDict
from pathlib import Path

import bpy
import bpy.utils.previews
from bpy.props import EnumProperty, IntProperty, PointerProperty, StringProperty

PAGE = 16
DATA = Path(__file__).resolve().parents[1] / 'data' / 'model_thumbnails'
_index = None
_previews = None
_loaded = OrderedDict()
_cache = {}
_folders = [('ALL', 'All folders', '')]


def catalog():
    global _index
    if _index is None:
        try:
            _index = json.loads((DATA / 'index.json').read_text())
        except (OSError, ValueError):
            _index = {}
    return _index


def _reset(self, context):
    self.page = 0
    _cache.clear()


def _folder_items(self, context):
    global _folders
    if len(_folders) > 1:
        return _folders
    names = sorted({n.split('/')[1] for n in catalog() if n.count('/') >= 2})
    items = [('ALL', 'All folders', '')] + [(n, n, 'models/' + n) for n in names]
    if items != _folders:
        _folders = items
    return _folders


def listing(context):
    t = context.scene.hl_models
    from .ops import game_content, game_root
    content = game_content(game_root(context))
    key = (id(content), t.search, t.folder, t.filter, t.features, t.favorites, t.recent,
           int(time.monotonic()) if t.filter == 'USED' else 0)
    if _cache.get('key') == key:
        return _cache['names']
    metadata = catalog()
    names = sorted(n for n in content.files if n.startswith('models/') and n.endswith('.mdl')) if content else []
    if t.filter == 'FAV':
        allowed = set(t.favorites.splitlines()); names = [n for n in names if n in allowed]
    elif t.filter == 'RECENT':
        available = set(names); names = [n for n in t.recent.splitlines() if n in available]
    elif t.filter == 'USED':
        from .mapcollection import map_objects
        inside = map_objects(context.scene)
        used = {kv.value.lower().replace('\\', '/') for o in context.scene.objects if o.name in inside
                for kv in o.hammerless.keyvalues if kv.key.lower() == 'model'}
        names = sorted(used)
    if t.folder != 'ALL':
        names = [n for n in names if n.startswith('models/' + t.folder + '/')]
    words = t.search.lower().split()
    names = [n for n in names if all(w in n for w in words)]
    if t.features == 'STATIC':
        names = [n for n in names if metadata.get(n, {}).get('static')]
    elif t.features == 'COLLISION':
        names = [n for n in names if metadata.get(n, {}).get('collision')]
    elif t.features == 'PREVIEW':
        names = [n for n in names if metadata.get(n, {}).get('status') == 'ok']
    _cache.update(key=key, names=names)
    return names


def thumbnail(path):
    global _previews
    if _previews is None:
        _previews = bpy.utils.previews.new()
    if path in _previews:
        _loaded.move_to_end(path)
        return _previews[path].icon_id
    record = catalog().get(path, {})
    filename = record.get('image', '')
    if record.get('status') != 'ok' or Path(filename).name != filename:
        return 0
    try:
        icon = _previews.load(path, str(DATA / filename), 'IMAGE')
    except (OSError, RuntimeError):
        return 0
    _loaded[path] = True
    while len(_loaded) > 128:
        old, _ = _loaded.popitem(last=False)
        del _previews[old]
    return icon.icon_id


def _remember(t):
    t.recent = '\n'.join([t.active] + [n for n in t.recent.splitlines() if n != t.active][:99])
    _cache.clear()


class HL_ModelBrowserSettings(bpy.types.PropertyGroup):
    active: StringProperty()
    search: StringProperty(name='Search', options={'TEXTEDIT_UPDATE'}, update=_reset,
                           description='Find models containing all these words in their path')
    folder: EnumProperty(name='Folder', items=_folder_items, update=_reset)
    filter: EnumProperty(name='Show', default='ALL', update=_reset, items=[
        ('ALL', 'All', 'All installed game models'), ('USED', 'In Map', 'Models used in this map'),
        ('FAV', 'Favourites', 'Starred models'), ('RECENT', 'Recent', 'Recently placed or applied models')])
    features: EnumProperty(name='Filter', default='PREVIEW', update=_reset, items=[
        ('PREVIEW', 'With Thumbnails', 'Hide animation-only files and models without a packaged thumbnail'),
        ('ALL', 'Any Model', 'Include models without a packaged thumbnail'),
        ('STATIC', 'Static Compatible', 'Models compiled with the static-prop flag'),
        ('COLLISION', 'Has Collision', 'Models with a physics collision file; not a guarantee of physics-prop support')])
    prop_type: EnumProperty(name='Place As', default='AUTO', items=[
        ('AUTO', 'Auto', 'Static when compiled for static props, otherwise Dynamic'),
        ('prop_static', 'Static Prop', 'Fixed scenery; the model must support static placement'),
        ('prop_physics', 'Physics Prop', 'Movable physics object; requires suitable model physics data'),
        ('prop_dynamic', 'Dynamic Prop', 'Can animate or respond to entity outputs')])
    page: IntProperty(default=0, min=0)
    favorites: StringProperty(options={'HIDDEN'})
    recent: StringProperty(options={'HIDDEN'})


class HL_OT_model_choose(bpy.types.Operator):
    bl_idname = 'hammerless.model_choose'
    bl_label = 'Select Model'
    path: StringProperty()

    @classmethod
    def description(cls, context, properties):
        return properties.path

    def execute(self, context):
        context.scene.hl_models.active = self.path
        return {'FINISHED'}


class HL_OT_model_page(bpy.types.Operator):
    bl_idname = 'hammerless.model_page'
    bl_label = 'Model Page'
    step: IntProperty()

    def execute(self, context):
        t = context.scene.hl_models
        t.page = max(0, min(t.page + self.step, (max(1, len(listing(context))) - 1) // PAGE))
        return {'FINISHED'}


class HL_OT_model_star(bpy.types.Operator):
    bl_idname = 'hammerless.model_star'
    bl_label = 'Star Model'

    def execute(self, context):
        t = context.scene.hl_models
        names = set(t.favorites.splitlines())
        if t.active in names: names.remove(t.active)
        elif t.active: names.add(t.active)
        t.favorites = '\n'.join(sorted(names))
        _cache.clear()
        return {'FINISHED'}


class HL_OT_model_place(bpy.types.Operator):
    bl_idname = 'hammerless.model_place'
    bl_label = 'Add at Cursor'
    bl_description = 'Place the selected model with its authored origin at the 3D cursor'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.scene.hl_models.active)

    def execute(self, context):
        from .ops import game_content, game_root, make_entity_object
        t = context.scene.hl_models
        content = game_content(game_root(context))
        if content is None or not content.has_model(t.active):
            self.report({'ERROR'}, 'This model is not available in the configured game')
            return {'CANCELLED'}
        record = catalog().get(t.active, {})
        kind = t.prop_type
        if kind == 'AUTO': kind = 'prop_static' if record.get('static') else 'prop_dynamic'
        if kind == 'prop_static' and record.get('static') is False:
            self.report({'WARNING'}, 'This model is not compiled for static props. Choose Dynamic or Auto.')
            return {'CANCELLED'}
        obj = make_entity_object(context, kind, context.scene.cursor.location.copy(),
                                 keyvalues={'model': t.active}, name=Path(t.active).stem)
        for old in context.selected_objects: old.select_set(False)
        obj.select_set(True); context.view_layer.objects.active = obj
        _remember(t)
        return {'FINISHED'}


class HL_OT_model_replace(bpy.types.Operator):
    bl_idname = 'hammerless.model_replace'
    bl_label = 'Replace Selected'
    bl_description = 'Replace the model on selected props of the active prop type, keeping their settings and transforms'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'OBJECT' and bool(context.scene.hl_models.active) and context.object is not None
                and context.object.hammerless.classname in {'prop_static', 'prop_physics', 'prop_dynamic'})

    def execute(self, context):
        t = context.scene.hl_models
        from .ops import game_content, game_root
        content = game_content(game_root(context))
        if not content or not content.has_model(t.active):
            self.report({'ERROR'}, 'Model unavailable in the configured game'); return {'CANCELLED'}
        if context.object.hammerless.classname == 'prop_static' and catalog().get(t.active, {}).get('static') is False:
            self.report({'WARNING'}, 'This model needs a Dynamic prop'); return {'CANCELLED'}
        result = bpy.ops.hammerless.pick_model(model=t.active)
        if result == {'FINISHED'}: _remember(t)
        return result


class HL_PT_model_browser(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Hammerless'
    bl_label = 'Model Browser'
    bl_order = 4
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout; t = context.scene.hl_models
        if t.active:
            box = layout.box(); row = box.row()
            icon = thumbnail(t.active)
            if icon: row.template_icon(icon_value=icon, scale=4.0)
            col = row.column(align=True)
            col.label(text=Path(t.active).stem)
            col.label(text=str(Path(t.active).parent).replace('\\', '/'))
            fav = t.active in t.favorites.splitlines()
            col.operator('hammerless.model_star', text='Unstar' if fav else 'Star', icon='SOLO_ON' if fav else 'SOLO_OFF')
            box.prop(t, 'prop_type')
            row = box.row(align=True)
            row.operator('hammerless.model_place', icon='ADD')
            row.operator('hammerless.model_replace', icon='FILE_REFRESH')
        layout.prop(t, 'search', text='', icon='VIEWZOOM')
        layout.row().prop(t, 'filter', expand=True)
        layout.prop(t, 'folder', text='')
        layout.prop(t, 'features', text='')
        names = listing(context)
        pages = max(1, (len(names) + PAGE - 1) // PAGE); page = min(t.page, pages - 1)
        grid = layout.grid_flow(row_major=True, columns=4, even_columns=True, even_rows=True)
        for path in names[page*PAGE:(page+1)*PAGE]:
            col = grid.column(align=True); icon = thumbnail(path)
            if icon: col.template_icon(icon_value=icon, scale=3.2)
            else: col.label(text='No thumbnail', icon='MESH_DATA')
            name = Path(path).stem
            col.operator('hammerless.model_choose', text=name if len(name)<=12 else name[:11]+'…',
                         depress=t.active==path).path = path
        if not names: layout.label(text='No matching models', icon='INFO')
        row = layout.row(align=True)
        row.operator('hammerless.model_page', text='', icon='TRIA_LEFT').step = -1
        row.label(text=f'{page+1} / {pages}  ({len(names):,} models)')
        row.operator('hammerless.model_page', text='', icon='TRIA_RIGHT').step = 1


CLASSES = (HL_ModelBrowserSettings, HL_OT_model_choose, HL_OT_model_page, HL_OT_model_star,
           HL_OT_model_place, HL_OT_model_replace, HL_PT_model_browser)


def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
    bpy.types.Scene.hl_models = PointerProperty(type=HL_ModelBrowserSettings)


def unregister():
    global _previews, _index, _folders
    del bpy.types.Scene.hl_models
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
    if _previews is not None: bpy.utils.previews.remove(_previews)
    _previews = None; _index = None
    _loaded.clear(); _cache.clear()
    _folders = [('ALL', 'All folders', '')]
