"""Graph-defined controls with overrides stored on each object's behaviour slot.

No draw-time mutation. Attachment/schema edits initialize controls; missing overrides
always compile to the graph default. Orphan values survive node deletion for undo.
"""
import uuid
import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty, EnumProperty

FIELDS = {'FLOAT':'number', 'INT':'integer', 'BOOL':'boolean', 'TEXT':'text', 'ENUM':'choice', 'VECTOR':'vector', 'COLOR':'color', 'OBJECT':'object'}
_syncing = False
_enum_cache = {}


def definitions(tree, seen=None):
    seen = set() if seen is None else seen
    if tree is None or tree.as_pointer() in seen:
        return []
    seen.add(tree.as_pointer())
    out = []
    for n in tree.nodes:
        if n.mute:
            continue
        if n.bl_idname == 'HL_NodeExposedSetting' and not n.mute:
            out.append(n)
        elif n.bl_idname == 'HL_NodeGroup':
            out.extend(definitions(n.group, seen))
    return out


def choices(node):
    rows=[]; keys=set()
    for line in node.choices.replace('\n',';').split(';'):
        if not line.strip():continue
        key, sep, label=line.strip().partition('=')
        key=key.strip();label=label.strip() if sep else key
        if not key or key in keys:
            raise ValueError('Dropdown choices need unique nonempty IDs: id=Label; other=Other Label')
        keys.add(key);rows.append((key,label or key))
    if not rows:raise ValueError('Dropdown needs at least one choice')
    return rows


def default(node):
    if node.value_type=='ENUM':
        options=choices(node)
        if node.default_text not in dict(options):
            raise ValueError('Dropdown default must match one of its choice IDs')
        return node.default_text
    return getattr(node, 'default_'+FIELDS[node.value_type])


def node_for(value):
    return next((n for n in definitions(value.schema) if n.setting_id==value.name),None)


def _items(self,context):
    n=node_for(self)
    try:rows=choices(n) if n else []
    except ValueError:rows=[]
    # Keep callback strings alive. Persist choice IDs, never transient enum positions.
    key=tuple(rows)
    if key not in _enum_cache:_enum_cache[key]=[(k,label,'',i) for i,(k,label) in enumerate(rows)]
    return _enum_cache[key]


def _enum_get(self):
    return next((i for i,row in enumerate(_items(self,None)) if row[0]==self.text),0)


def _enum_set(self,index):
    rows=_items(self,None)
    if 0<=index<len(rows):self.text=rows[index][0]


def _changed(self,context):
    if not _syncing:
        self.overridden=True
        owner=self.id_data
        if isinstance(owner,bpy.types.Object):
            for slot in owner.hammerless.behaviours:
                value=slot.values.get(self.name)
                if value is not None and value.as_pointer()==self.as_pointer():
                    update_preview(slot,self.name)
                    break


class HL_ExposedValue(bpy.types.PropertyGroup):
    object: PointerProperty(type=bpy.types.Object, update=_changed)
    schema: PointerProperty(type=bpy.types.NodeTree)
    value_type: StringProperty()
    overridden: BoolProperty(default=False)
    number: FloatProperty(update=_changed)
    integer: IntProperty(update=_changed)
    boolean: BoolProperty(update=_changed)
    text: StringProperty(update=_changed)
    vector: FloatVectorProperty(size=3,update=_changed)
    color: FloatVectorProperty(size=3,subtype='COLOR',min=0,max=1,update=_changed)
    choice: EnumProperty(items=_items,get=_enum_get,set=_enum_set)


def sync_slot(slot):
    global _syncing
    if slot.graph is None:return
    previous = _syncing
    _syncing=True
    try:
        for n in definitions(slot.graph):
            if not n.setting_id:continue
            v=slot.values.get(n.setting_id)
            if v is None:
                v=slot.values.add();v.name=n.setting_id
            v.schema=slot.graph
            if v.value_type!=n.value_type:
                v.value_type=n.value_type;v.overridden=False
            if not v.overridden:
                try:d=default(n)
                except ValueError:continue
                setattr(v,'text' if n.value_type=='ENUM' else FIELDS[n.value_type],d)
    finally:_syncing=previous


def sync_all(*_):
    for obj in bpy.data.objects:
        if hasattr(obj,'hammerless') and (obj.library is None or obj.override_library is not None):
            for slot in obj.hammerless.behaviours:sync_slot(slot)


def graph_changed(self,context):
    sync_slot(self)


def schema_changed(self,context):
    if self.setting_id:
        sync_all()
        for obj in bpy.data.objects:
            if obj.library is not None and obj.override_library is None:continue
            for slot in obj.hammerless.behaviours:
                if self in definitions(slot.graph):update_preview(slot,self.setting_id)


def type_changed(self,context):
    from .logic import _rebuild
    _rebuild(self)
    schema_changed(self,context)


def resolve(node,slot):
    result=default(node)
    if slot is not None:
        v=slot.values.get(node.setting_id)
        if v is not None and v.overridden and v.value_type==node.value_type:
            result=getattr(v,'text' if node.value_type=='ENUM' else FIELDS[node.value_type])
    if node.value_type=='ENUM' and result not in dict(choices(node)):
        raise ValueError('Saved dropdown choice was removed; choose a replacement or reset this setting')
    return tuple(result) if node.value_type in ('VECTOR','COLOR') else result


@bpy.app.handlers.persistent
def on_load(*_):
    sync_all()


def draw(layout,slot,index):
    defs=definitions(slot.graph)
    previews=preview_nodes(slot.graph)
    if not defs and not previews:return
    box=layout.box();box.label(text=slot.graph.name)
    if previews:
        box.prop(slot,'preview_enabled',text=getattr(previews[0],'preview_label','Update Display from Settings'))
        if slot.preview_error:box.label(text=slot.preview_error,icon='ERROR')
    for n in defs:
        v=slot.values.get(n.setting_id)
        if v is None:
            box.label(text=n.ui_name+' (refresh settings)')
            box.operator('hammerless.behaviour_refresh_settings',text='Refresh settings')
            continue
        try:resolve(n,slot)
        except ValueError as error:box.label(text=str(error),icon='ERROR')
        row=box.row(align=True)
        row.prop(v,FIELDS[n.value_type],text=n.ui_name or n.name)
        sub=row.row(align=True);sub.enabled=v.overridden
        op=sub.operator('hammerless.behaviour_reset_setting',text='',icon='LOOP_BACK')
        op.index=index;op.key=n.setting_id


def copy_values(source,target):
    global _syncing
    previous = _syncing
    _syncing = True
    try:
        sync_slot(target)
        target.preview_enabled = source.preview_enabled
        for value in source.values:
            other=target.values.get(value.name)
            if other is not None and value.overridden:
                field='text' if value.value_type=='ENUM' else FIELDS[value.value_type]
                setattr(other,field,getattr(value,field))
                other.overridden = True
    finally:
        _syncing = previous
    update_preview(target)


def preview_nodes(tree,seen=None):
    seen=set() if seen is None else seen
    if tree is None or tree.as_pointer() in seen:return []
    seen.add(tree.as_pointer())
    out=[]
    for n in tree.nodes:
        if n.mute:continue
        if hasattr(n,'update_owner_preview'):out.append(n)
        if n.bl_idname=='HL_NodeGroup':out.extend(preview_nodes(n.group,seen))
    return out


def update_preview(slot,setting_id=None):
    if not slot.preview_enabled:return
    owner=slot.id_data
    if not isinstance(owner,bpy.types.Object):return
    slot.preview_error=''
    for n in preview_nodes(slot.graph):
        try:n.update_owner_preview(owner,slot,setting_id)
        except (ValueError,RuntimeError,OSError) as error:
            slot.preview_error='Model preview: '+str(error)


def preview_toggle(self,context):
    if not _syncing:
        update_preview(self)


def preview_node_changed(self,context):
    for obj in bpy.data.objects:
        if obj.library is not None and obj.override_library is None:continue
        for slot in obj.hammerless.behaviours:
            if self in preview_nodes(slot.graph):update_preview(slot)
