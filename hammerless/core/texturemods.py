"""Build self-contained, reversible material override addons from explicit mappings.

Inputs are already encoded uint8 RGBA, top row first. This module never edits
stock game archives or loose game materials. Caller owns install destination.
"""
from __future__ import annotations
import copy
import hashlib
import os
from pathlib import Path
import re
import tempfile
import numpy as np
from .vmf import Block, parse
from .textures import write_vtf, is_pow2
from .vtf_read import read_vtf
from .vpk import write_vpk

SUPPORTED_SHADERS = {'infected', 'vertexlitgeneric', 'lightmappedgeneric', 'unlitgeneric'}


def material_path(value: str) -> str:
    """Canonical material identifier, without materials/ or .vmt."""
    if not isinstance(value,str):raise ValueError('Material path must be text')
    value=value.strip().replace('\\','/').lower()
    if value.startswith('materials/'):value=value[10:]
    if value.endswith('.vmt'):value=value[:-4]
    if not value or len(value)>240 or any(p in ('','.','..') for p in value.split('/')) or not re.fullmatch(r'[a-z0-9_./ -]+',value):
        raise ValueError('Invalid game material path: '+repr(value))
    return value


def _texture_path(value):
    value=value.strip().replace('\\','/').lower()
    if value.startswith('materials/'):value=value[10:]
    if value.endswith('.vtf'):value=value[:-4]
    return material_path(value)


def _set(block,key,value):
    key=key.lower()
    block.items=[it for it in block.items if not isinstance(it,tuple) or it[0].lower()!=key]
    block.items.append((key,value))


def _merge(destination,source,replace):
    for item in source.items:
        key=item.name if isinstance(item,Block) else item[0]
        indexes=[i for i,it in enumerate(destination.items) if (it.name if isinstance(it,Block) else it[0]).lower()==key.lower()]
        if isinstance(item,Block) and indexes and isinstance(destination.items[indexes[0]],Block):
            _merge(destination.items[indexes[0]],item,replace)
        elif not indexes:
            if not replace:destination.items.append(copy.deepcopy(item))
        elif replace:
            destination.items[indexes[0]]=copy.deepcopy(item)


def resolved_material(content,target,seen=()):
    target=material_path(target)
    if target in seen or len(seen)>=16:raise ValueError('Cyclic or excessive material Patch includes: '+target)
    raw=content.read('materials/'+target+'.vmt')
    if not raw:raise ValueError('Original game material not found: '+target)
    roots=parse(raw.decode('utf-8-sig',errors='replace'))
    if len(roots)!=1:raise ValueError('Expected one material shader: '+target)
    root=roots[0]
    if root.name.lower()!='patch':return root
    include=root.get('include')
    if not include:raise ValueError('Patch has no include: '+target)
    root_out=resolved_material(content,include,seen+(target,))
    for kind in ('insert','replace'):
        for section in root.blocks(kind):_merge(root_out,section,kind=='replace')
    return root_out


def _rgba(value,label):
    if not isinstance(value,np.ndarray) or value.dtype!=np.uint8 or value.ndim!=3 or value.shape[2]!=4:
        raise ValueError(label+' must be an HxWx4 uint8 array')
    h,w=value.shape[:2]
    if not is_pow2(w) or not is_pow2(h) or max(w,h)>8192:
        raise ValueError(label+' dimensions must be powers of two up to 8192')
    return value.copy()


def _original_alpha(content,root,rgba,target,key='$basetexture'):
    tex=root.get(key)
    if not tex:raise ValueError('Original material has no '+key+': '+target)
    raw=content.read('materials/'+_texture_path(tex)+'.vtf')
    if not raw:raise ValueError('Cannot preserve original texture alpha: '+target)
    _,_,old=read_vtf(raw,max_size=8192)
    h,w=rgba.shape[:2];oh,ow=old.shape[:2]
    # Pixel-center nearest sampling avoids inventing intermediate mask classes.
    yy=np.minimum(((np.arange(h)+.5)*oh/h).astype(int),oh-1)
    xx=np.minimum(((np.arange(w)+.5)*ow/w).astype(int),ow-1)
    rgba[:,:,3]=old[yy[:,None],xx[None,:],3]


def build_package(content,replacements,output_path,uid,title='Hammerless Material Mods',source_blend=''):
    """Rebuild an entire VPK atomically from enabled replacements only.

    replacements: material path -> {basecolor: RGBA, normal: optional RGBA}.
    Normal input must already use Source orientation. Base alpha is retained
    from stock material because infected shading/other shaders use it as data.
    Empty replacements builds a valid addon with no material overrides.
    Returns material/file counts plus explicit conversion warnings.
    """
    if not isinstance(uid,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',uid):raise ValueError('Invalid mod identity')
    uid=uid.lower();files={};warnings=[];seen=set()
    with tempfile.TemporaryDirectory(prefix='hammerless-materialmod-') as temp:
        for raw_target,maps in replacements.items():
            target=material_path(raw_target)
            if target in seen:raise ValueError('Duplicate target material: '+target)
            seen.add(target)
            root=resolved_material(content,target)
            shader=root.name.lower()
            if shader not in SUPPORTED_SHADERS:raise ValueError('Unsupported material shader '+root.name+' for '+target)
            if shader=='infected' and root.get('$disablevariation')!='1':
                _set(root,'$disablevariation','1')
                warnings.append(target+': disabled infected palette variation because the replacement is authored RGB color')
            rgba=_rgba(maps.get('basecolor'),'Base color for '+target)
            _original_alpha(content,root,rgba,target)
            prefix='hammerless/mods/'+uid+'/'+hashlib.sha256(target.encode()).hexdigest()[:24]
            path=Path(temp)/'image.vtf'
            write_vtf(str(path),rgba)
            files['materials/'+prefix+'_color.vtf']=path.read_bytes()
            _set(root,'$basetexture',prefix+'_color')
            if maps.get('normal') is not None:
                normal=_rgba(maps['normal'],'Normal for '+target)
                if shader in {'infected','vertexlitgeneric','lightmappedgeneric'}:
                    if root.get('$bumpmap'):
                        _original_alpha(content,root,normal,target,key='$bumpmap')
                    elif shader=='infected':
                        warnings.append(target+': Infected normal alpha controls shininess; the supplied mask is an approximation, not a calibrated roughness conversion')
                    write_vtf(str(path),normal,normal_map=True)
                    files['materials/'+prefix+'_normal.vtf']=path.read_bytes()
                    _set(root,'$bumpmap',prefix+'_normal')
                    if shader=='infected':warnings.append(target+': Infected normal maps require the game Shader Detail setting on High')
                else:warnings.append(target+': '+root.name+' normal-map replacement is not supported; original shader retained')
            for key in ('roughness','metallic'):
                if maps.get(key) is not None:warnings.append(target+': '+key+' is not a direct Source input and was not exported')
            lines=[];root.write(lines)
            files['materials/'+target+'.vmt']=''.join(lines).encode('utf-8')
        import json
        files['hammerless_material_mod.json']=json.dumps({'version':1,'uid':uid,'title':title,'source_blend':source_blend,'targets':sorted(seen)},ensure_ascii=False).encode('utf-8')
        info=Block('AddonInfo').kv('addonSteamAppID','550').kv('addonTitle',title).kv('addonVersion','1').kv('addonAuthor','Hammerless').kv('addonDescription','Global material replacements. Disable or remove entries and rebuild to restore stock materials.').kv('addonContent_Skin','1')
        lines=[];info.write(lines);files['addoninfo.txt']=''.join(lines).encode('utf-8')
        destination=Path(output_path);destination.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=destination.name+'.',suffix='.tmp',dir=destination.parent);os.close(fd)
        try:
            write_vpk(tmp,files);os.replace(tmp,destination)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return {'materials':len(seen),'files':len(files),'warnings':warnings}
