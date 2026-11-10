"""Conservative light-only optimization, guarded against silent addon edits.

Dependency graph notifications are necessary, not sufficient. Custom properties
and imported text are compared explicitly on every callback. Unknown updates
always use the full path. No global handler or persisted revision is required.
"""
import bpy
from .extract import game_path,texture_size
from .previewscene import collection_role_snapshot
from .previewsource import SourceRevision
from . import vmfimport


def _pointer(value):
    return value.as_pointer() if value is not None else 0


def _light_only(depsgraph):
    updates=list(depsgraph.updates)
    return bool(updates) and all(isinstance(u.id,bpy.types.Light) or
        (isinstance(u.id,bpy.types.Object) and u.id.type=='LIGHT') for u in updates)


class LightUpdateGuard:
    def __init__(self):
        self.previous=None
        self.source=SourceRevision()
        self.unsupported_light_dependencies=False
        self.fast_updates=0
        self.full_updates=0

    def clear(self):
        self.previous=None

    def fingerprint(self,context,depsgraph,live,refresh_revision):
        scene=context.scene;s=scene.hammerless
        source_text=bpy.data.texts.get(scene.get(vmfimport.SOURCE_KEY,''))
        if live is not None:self.source.update(None)
        source=(live.source_revision if live is not None else self.source).update(
            source_text.as_string() if source_text else None)
        roles=collection_role_snapshot()
        # Include evaluated instance sources outside scene.objects (collection
        # instances), plus all parents whose classname changes inclusion policy.
        objects={o.as_pointer():o for o in scene.objects}
        for instance in depsgraph.object_instances:
            obj=instance.object.original;objects[obj.as_pointer()]=obj
            if instance.parent is not None:
                parent=instance.parent.original;objects[parent.as_pointer()]=parent
        for obj in list(objects.values()):
            parent=obj.parent
            while parent is not None:
                objects[parent.as_pointer()]=parent;parent=parent.parent
        # First version deliberately excludes lights acting as geometry parents
        # or instancers, rather than assuming their dependent update tags.
        self.unsupported_light_dependencies=any(
            (o.parent is not None and o.parent.type=='LIGHT') or
            (o.type=='LIGHT' and o.instance_type!='NONE') for o in objects.values())
        materials={};rows=[]
        for key,obj in objects.items():
            h=obj.hammerless;data=obj.data
            slots=tuple(slot.material for slot in obj.material_slots) if obj.type=='MESH' else ()
            for mat in slots:
                if mat is not None:materials[mat.as_pointer()]=mat
            rows.append((key,obj.name,obj.type,_pointer(obj.parent),_pointer(data),
                h.role,h.classname,h.use_convex_hull,roles.get(key,'NONE'),
                obj.get(vmfimport.ID),obj.get(vmfimport.KIND),obj.get(vmfimport.NAME),obj.get(vmfimport.INDEX),
                bool(data.get('hl_model')) if obj.type=='MESH' else False,
                tuple(_pointer(mat) for mat in slots)))
        material_rows=tuple((key,mat.name,mat.hammerless.source_material,
            mat.hammerless.lightmap_scale,mat.hammerless.texture_scale,game_path(mat),texture_size(mat))
            for key,mat in materials.items())
        refs=scene.get(vmfimport.REGISTRY)
        registry=tuple(sorted((key,_pointer(value)) for key,value in refs.items())) if refs is not None else None
        settings=(s.game_root,s.default_material,s.units_per_meter,s.lightmap_scale,s.lightmap_scale_imported,
            scene.hl_preview_surface_cache,scene.hl_preview_bounce_samples,scene.hl_preview_max_bounces,
            scene.hl_preview_refine_frames,refresh_revision)
        return (_pointer(scene),_pointer(context.view_layer),_pointer(depsgraph),scene.frame_current,scene.frame_subframe,
                settings,source,registry,tuple(rows),material_rows)

    def check(self,context,depsgraph,live,refresh_revision):
        if not _light_only(depsgraph):
            self.previous=None;self.full_updates+=1
            return False
        current=self.fingerprint(context,depsgraph,live,refresh_revision)
        fast=self.previous is not None and not self.unsupported_light_dependencies and current==self.previous
        self.previous=current
        if fast:self.fast_updates+=1
        else:self.full_updates+=1
        return fast
