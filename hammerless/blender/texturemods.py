"""Blend-owned material replacements; game files are never edited in place."""
import os, re, uuid, shutil
from pathlib import Path
import bpy
import numpy as np
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from ..core import texturemods


def target_path(material):
    return str(material.get('hl_source_material', '')) if material else ''


def _get_target(self): return target_path(self)
def _set_target(self,value): self['hl_source_material']=value.strip()


class HL_MaterialMod(bpy.types.PropertyGroup):
    enabled: BoolProperty(name='Enabled',default=True)
    material: PointerProperty(type=bpy.types.Material)
    target: StringProperty(name='Replaces Game Material',description='Exact game material path, relative to materials, without .vmt. Every use of this material is replaced')
    infected_gloss: FloatProperty(name='Infected Normal Gloss',default=32/255,min=0,max=1,description='Fallback normal alpha mask for Infected materials without an original normal map. Approximate shine control, not a calibrated roughness conversion. Existing game normal alpha is preserved when available')


class HL_MaterialMods(bpy.types.PropertyGroup):
    entries: CollectionProperty(type=HL_MaterialMod)
    active: IntProperty(default=0)
    uid: StringProperty()
    status: StringProperty()


class HL_InstalledMaterialMod(bpy.types.PropertyGroup):
    uid: StringProperty()
    filename: StringProperty()
    source_blend: StringProperty()
    title: StringProperty()
    count: IntProperty()


def refresh_installed(context):
    from .ops import game_root
    from ..core.modregistry import scan
    rows=context.window_manager.hl_installed_material_mods
    rows.clear()
    root=game_root(context)
    if root:
        for mod in scan(root):
            row=rows.add()
            for key in ('uid','filename','source_blend','title'):setattr(row,key,mod[key])
            row.count=len(mod['targets'])
    return len(rows)


def identity(settings):
    if not settings.uid: settings.uid=uuid.uuid4().hex
    if not re.fullmatch('[0-9a-f]{32}',settings.uid): raise ValueError('Invalid material-mod identity')
    return settings.uid


def package_name(uid):
    return 'hlm_'+uid+'.vpk'


def install_package(output,root,uid):
    from ..core.vpk import VPK
    dest=Path(root)/'left4dead2'/'addons'/package_name(uid)
    # L4D2 truncates addon metadata paths at 127 bytes; such packages can mount
    # below stock content even though addonlist says they are enabled.
    if len(str(dest.resolve()).encode('utf-8'))>=128:
        raise ValueError('L4D2 addon path is too long (128-byte engine limit). Use a shorter game installation path.')
    legacy=dest.parent/('hammerless_materials_'+uid+'.vpk')
    if legacy.exists():
        archive=VPK(str(legacy));info=archive.read('addoninfo.txt').decode('utf-8')
        from ..core.vmf import parse
        blocks=parse(info)
        if not blocks or blocks[0].get('addonAuthor')!='Hammerless':
            raise ValueError('Old package is not a recognized Hammerless mod: '+str(legacy))
    dest.parent.mkdir(exist_ok=True)
    tmp=dest.with_suffix('.vpk.tmp')
    try:
        shutil.copyfile(output,tmp)
        try:os.replace(tmp,dest)
        except PermissionError as e:raise PermissionError('Close L4D2 before installing: the game is holding this mod package open.') from e
        if legacy.exists():legacy.unlink()
    finally:
        if tmp.exists():tmp.unlink()


def image_input(material,socket_name):
    if not material or not material.use_nodes: raise ValueError('Replacement must use a Principled material with image textures')
    outputs=[n for n in material.node_tree.nodes if n.type=='OUTPUT_MATERIAL' and n.is_active_output]
    if not outputs or not outputs[0].inputs['Surface'].is_linked: raise ValueError('Material has no connected surface')
    shader=outputs[0].inputs['Surface'].links[0].from_node
    if shader.type!='BSDF_PRINCIPLED': raise ValueError('Use a Principled BSDF for this first version')
    sock=shader.inputs[socket_name]
    if not sock.is_linked:
        if socket_name=='Base Color': raise ValueError('Base Color needs a connected Image Texture')
        return None
    node=sock.links[0].from_node
    if socket_name=='Normal':
        if node.type!='NORMAL_MAP' or node.space!='TANGENT' or node.inputs['Strength'].is_linked or abs(node.inputs['Strength'].default_value-1)>1e-6:
            raise ValueError('Normal needs a tangent-space Normal Map node with Strength 1')
        if not node.inputs['Color'].is_linked: raise ValueError('Normal Map needs an image')
        node=node.inputs['Color'].links[0].from_node
    if node.type!='TEX_IMAGE' or not node.image: raise ValueError(socket_name+' must come directly from an Image Texture; bake procedural or mixed materials first')
    if node.inputs['Vector'].is_linked and node.inputs['Vector'].links[0].from_node.type!='UVMAP':
        raise ValueError('Bake transformed/generated texture coordinates before creating a mod')
    return node.image


def pixels(image,color=False,normal=False):
    w,h=image.size
    if not w or not h: raise ValueError('Image is missing: '+image.name)
    data=np.empty(w*h*4,np.float32);image.pixels.foreach_get(data);data=data.reshape(h,w,4)[::-1].copy()
    if not np.isfinite(data).all(): raise ValueError('Image contains invalid pixels: '+image.name)
    space=image.colorspace_settings.name
    if color:
        if image.is_float and space in {'Linear Rec.709','Linear'}:
            rgb=data[:,:,:3];data[:,:,:3]=np.where(rgb<=.0031308,rgb*12.92,1.055*np.maximum(rgb,0)**(1/2.4)-.055)
        elif not image.is_float and space in {'sRGB','Non-Color'}:
            pass  # Blender byte-image pixels already contain encoded values.
        else: raise ValueError('Use an sRGB PNG or Linear Rec.709 float image for Base Color: '+image.name)
    if normal and space!='Non-Color':raise ValueError('Set the normal image Color Space to Non-Color: '+image.name)
    if normal: data[:,:,1]=1-data[:,:,1]
    return np.clip(data*255+.5,0,255).astype(np.uint8)


def build(context,install=False):
    from .ops import game_root,game_content
    root=game_root(context)
    if not root: raise ValueError('Set the L4D2 folder in Hammerless Settings')
    if not bpy.data.filepath: raise ValueError('Save this blend before building its material mod')
    settings=context.scene.hl_material_mods;uid=identity(settings);replacements={}
    for entry in settings.entries:
        if not entry.enabled: continue
        path=texturemods.material_path(entry.target)
        if path in replacements: raise ValueError('Two enabled entries replace '+path)
        material=entry.material
        if not material: raise ValueError('Missing replacement material for '+path)
        payload={'basecolor':pixels(image_input(material,'Base Color'),color=True)}
        normal=image_input(material,'Normal')
        if normal:
            payload['normal']=pixels(normal,normal=True)
            from ..core.gamematerials import read_vmt
            if read_vmt(game_content(root),path).get('shader')=='infected':
                payload['normal'][:,:,3]=round(entry.infected_gloss*255)
        shader=next(n for n in material.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
        for key,sock in [('roughness','Roughness'),('metallic','Metallic')]:
            if shader.inputs[sock].is_linked: payload[key]=True
        replacements[path]=payload
    folder=Path(bpy.data.filepath).parent/'hammerless_mods';folder.mkdir(exist_ok=True)
    output=folder/package_name(uid)
    report=texturemods.build_package(game_content(root),replacements,str(output),uid,
        title=Path(bpy.data.filepath).stem+' - Material Mods',source_blend=str(Path(bpy.data.filepath).resolve()))
    if install:
        from ..core.modregistry import scan
        for mod in scan(root):
            if mod['uid']!=uid and set(mod['targets']).intersection(replacements):
                raise ValueError('Materials already replaced by '+mod['title']+'. Uninstall that package in Installed Mods first.')
        install_package(output,root,uid)
        refresh_installed(context)
    settings.status=('Installed' if install else 'Built')+': '+str(len(replacements))+' replacements. Restart the game to reload.'
    warnings=report.get('warnings',[])
    text=bpy.data.texts.get('Hammerless Material Mod Report') or bpy.data.texts.new('Hammerless Material Mod Report')
    text.clear();text.write(settings.status+'\n'+str(output)+'\n'+'\n'.join(warnings))
    return report


class HL_OT_material_mod_add(bpy.types.Operator):
    bl_idname='hammerless.material_mod_add';bl_label='Create Texture Mod from Selected';bl_options={'REGISTER','UNDO'}
    bl_description='Add the selected mesh materials using stored game paths. Global replacements affect all models sharing those materials'
    @classmethod
    def poll(cls,context): return context.object is not None and context.object.type=='MESH'
    def execute(self,context):
        obj=context.object;settings=context.scene.hl_material_mods
        provenance=obj.data.get('hl_source_materials',[])
        pending=[]
        try:
            for i,slot in enumerate(obj.material_slots):
                mat=slot.material
                if not mat: continue
                target=target_path(mat) or (provenance[i] if i<len(provenance) else '')
                if not target: raise ValueError('Set Original Game Material for '+mat.name+' below, then try again')
                target=texturemods.material_path(target);pending.append((mat,target))
            if not pending: raise ValueError('Selected mesh has no materials')
            if len({p for _,p in pending})!=len(pending):raise ValueError('Multiple selected slots replace the same game material; resolve their mappings first')
            for mat,target in pending:
                entry=next((e for e in settings.entries if e.target==target),None)
                if entry is None: entry=settings.entries.add()
                entry.target=target;entry.material=mat;entry.enabled=True;mat['hl_source_material']=target
            identity(settings);settings.status='Added replacements. Shared game materials affect other models too.'
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
        return {'FINISHED'}


class HL_OT_material_mod_remove(bpy.types.Operator):
    bl_idname='hammerless.material_mod_remove';bl_label='Remove Replacement';bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        s=context.scene.hl_material_mods
        if 0<=s.active<len(s.entries):s.entries.remove(s.active);s.active=max(0,s.active-1)
        s.status='Rebuild / Install to remove this replacement from the game.'
        return {'FINISHED'}


class HL_OT_material_mod_build(bpy.types.Operator):
    bl_idname='hammerless.material_mod_build';bl_label='Build / Install Material Mod'
    bl_description='Rebuild this blend’s managed addon from enabled entries only. Disabled or removed entries stop overriding game materials after restarting the game'
    install: BoolProperty(default=True)
    def execute(self,context):
        try:
            report=build(context,self.install)
            self.report({'WARNING'} if report.get('warnings') else {'INFO'},context.scene.hl_material_mods.status+(' See Material Mod Report for shader limitations.' if report.get('warnings') else ''))
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
        return {'FINISHED'}


class HL_UL_material_mods(bpy.types.UIList):
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        layout.prop(item,'enabled',text='');layout.label(text=item.material.name if item.material else 'Missing material')


class HL_OT_material_mod_refresh(bpy.types.Operator):
    bl_idname='hammerless.material_mod_refresh';bl_label='Refresh Installed Mods'
    def execute(self,context):
        try:refresh_installed(context)
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
        return {'FINISHED'}


class HL_OT_material_mod_uninstall(bpy.types.Operator):
    bl_idname='hammerless.material_mod_uninstall';bl_label='Uninstall Mod'
    bl_description='Remove this installed Hammerless package, even if another blend created it. Keeps a backup outside addons. Restart the game afterward'
    filename: StringProperty()
    def execute(self,context):
        from .ops import game_root
        from ..core.modregistry import uninstall
        try:
            backup=uninstall(game_root(context),self.filename)
            refresh_installed(context)
            context.scene.hl_material_mods.status='Uninstalled. Restart game. Building its source blend installs it again.'
            self.report({'INFO'},'Uninstalled; backup: '+str(backup))
        except PermissionError:self.report({'ERROR'},'Close L4D2 before uninstalling this mod.');return {'CANCELLED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
        return {'FINISHED'}


class HL_PT_material_mods(bpy.types.Panel):
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Hammerless';bl_label='Material Mods';bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        l=self.layout;s=context.scene.hl_material_mods
        l.label(text='Global: affects all uses of these materials',icon='ERROR')
        l.operator('hammerless.material_mod_add',icon='ADD')
        obj=context.object
        if obj and obj.active_material:
            box=l.box();box.label(text='Selected material mapping')
            box.prop(obj.active_material,'hl_mod_target',text='Original Game Material')
        row=l.row();row.template_list('HL_UL_material_mods','',s,'entries',s,'active',rows=3);row.operator('hammerless.material_mod_remove',text='',icon='REMOVE')
        if 0<=s.active<len(s.entries):
            e=s.entries[s.active];l.prop(e,'target');l.prop(e,'material')
            l.prop(e,'infected_gloss')
        l.operator('hammerless.material_mod_build',icon='EXPORT')
        l.label(text='Remove/disable → rebuild → restart game')
        l.label(text='Shader limits: see Material Mod Report')
        l.label(text='Roughness / metallic maps: not converted yet')
        if s.status:l.label(text=s.status)
        box=l.box();box.label(text='Installed Mods (all blends)')
        box.operator('hammerless.material_mod_refresh',icon='FILE_REFRESH')
        for mod in context.window_manager.hl_installed_material_mods:
            item=box.box();item.label(text=mod.title)
            item.label(text=str(mod.count)+' material replacements'+(' - this blend' if mod.uid==s.uid else ''))
            if mod.source_blend:
                item.label(text='Source: '+Path(mod.source_blend).name)
                row=item.row();row.enabled=False;row.prop(mod,'source_blend',text='Full Path')
            else:item.label(text='Source: Unknown (older package)')
            item.label(text=mod.filename)
            op=item.operator('hammerless.material_mod_uninstall',icon='TRASH');op.filename=mod.filename
        box.label(text='Uninstall here; rebuild source blend to reinstall.')


CLASSES=(HL_MaterialMod,HL_MaterialMods,HL_InstalledMaterialMod,HL_OT_material_mod_add,HL_OT_material_mod_remove,HL_OT_material_mod_build,HL_UL_material_mods,HL_OT_material_mod_refresh,HL_OT_material_mod_uninstall,HL_PT_material_mods)
def register():
    for c in CLASSES:bpy.utils.register_class(c)
    bpy.types.Scene.hl_material_mods=PointerProperty(type=HL_MaterialMods)
    bpy.types.WindowManager.hl_installed_material_mods=CollectionProperty(type=HL_InstalledMaterialMod)
    bpy.types.Material.hl_mod_target=StringProperty(name='Original Game Material',get=_get_target,set=_set_target,description='Stored game path, independent of this material’s Blender name. Example: models/infected/common/common_male_lower_01')
def unregister():
    del bpy.types.WindowManager.hl_installed_material_mods
    del bpy.types.Material.hl_mod_target;del bpy.types.Scene.hl_material_mods
    for c in reversed(CLASSES):bpy.utils.unregister_class(c)
