"""Discover and uninstall managed material addons independently of any open blend."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import struct
import uuid

from .vpk import VPK
from .vmf import parse
from .texturemods import material_path

MANIFEST = 'hammerless_material_mod.json'
_NAME = re.compile(r'^(hlm_|hammerless_materials_)([0-9a-f]{32})\.vpk$')


def _addons(root):
    if not isinstance(root,(str,os.PathLike)) or not str(root).strip():
        raise ValueError('Set the L4D2 folder in Hammerless Settings first')
    return Path(root).resolve() / 'left4dead2' / 'addons'


def _fingerprint(path):
    s=path.stat()
    return [s.st_size,s.st_mtime_ns,s.st_ino]


def _inspect(addons,filename):
    match=_NAME.fullmatch(filename)
    if not match:raise ValueError('Not a managed Hammerless material-mod filename')
    path=addons/filename
    if path.is_symlink() or path.resolve().parent!=addons.resolve():
        raise ValueError('Material-mod package must be a regular file in the game addons folder')
    before=_fingerprint(path)
    archive=VPK(str(path))
    info=parse(archive.read('addoninfo.txt').decode('utf-8'))
    if len(info)!=1 or info[0].name.lower()!='addoninfo' or info[0].get('addonAuthor')!='Hammerless':
        raise ValueError('Package is not a recognized Hammerless material mod')
    uid=match.group(2)
    targets=sorted(material_path(name[len('materials/'):]) for name in archive.entries
                   if name.startswith('materials/') and name.endswith('.vmt'))
    source='';title=info[0].get('addonTitle','Hammerless Material Mods');legacy=True
    if MANIFEST in archive.entries:
        raw=archive.read(MANIFEST)
        if len(raw)>1024*1024:raise ValueError('Material-mod manifest is too large')
        data=json.loads(raw)
        if not isinstance(data,dict) or data.get('uid')!=uid:
            raise ValueError('Material-mod manifest identity does not match its filename')
        source=data.get('source_blend','');title=data.get('title',title)
        if not isinstance(source,str) or not isinstance(title,str) or len(source)>4096 or len(title)>512:
            raise ValueError('Invalid material-mod source information')
        # Targets come from actual VPK entries, not potentially stale manifest metadata.
        legacy=False
    if before!=_fingerprint(path):raise ValueError('Material-mod package changed while it was being read; refresh and retry')
    return dict(uid=uid,filename=filename,source_blend=source,title=title,
                targets=targets,legacy=legacy,path=str(path),fingerprint=before)


def scan(root):
    """List valid managed packages. Older packages remain visible with source unknown.

    This reads installed VPKs rather than a blend-owned list, so a different blend
    or a manually removed package is reflected on the next refresh. Unrelated,
    malformed, or identity-mismatched packages are never offered for uninstall.
    """
    addons=_addons(root)
    if not addons.is_dir():return []
    result=[]
    for path in sorted(addons.iterdir(),key=lambda p:p.name):
        if not _NAME.fullmatch(path.name) or not path.is_file():continue
        try:result.append(_inspect(addons,path.name))
        except (OSError,ValueError,KeyError,UnicodeError,IndexError,struct.error):continue
    return result


def uninstall(root,filename):
    """Revalidate and move one managed addon out of the mounted directory.

    Returns the backup path. Rebuilding the owning blend reinstalls it; users can
    also restore this backup manually. No stock file or other addon is removed.
    """
    addons=_addons(root)
    item=_inspect(addons,filename)
    source=addons/filename
    backup=addons.parent/'hammerless_uninstalled'
    if backup.is_symlink() or backup.resolve().parent!=addons.parent.resolve():
        raise ValueError('Uninstall backup folder must be inside the game folder')
    backup.mkdir(exist_ok=True)
    destination=backup/(filename+'.'+uuid.uuid4().hex+'.backup')
    if item['fingerprint']!=_fingerprint(source):
        raise ValueError('Material-mod package changed; refresh and retry')
    os.replace(source,destination)
    return str(destination)
