"""VTF sheet-resource animation for particle preview, including nonuniform UV rectangles."""
import math
import struct
from dataclasses import dataclass
import numpy as np


@dataclass
class Sequence:
    clamp: bool
    durations: np.ndarray
    rectangles: np.ndarray  # frames x coordinate sets x four UV bounds

    def frame(self, age, lifetime, rate=1, fit=False, fps=False):
        if not math.isfinite(age) or not math.isfinite(rate):
            return 0
        total=float(self.durations.sum())
        if fit:
            t=min(max(age/max(lifetime,1e-9),0),np.nextafter(1.,0))*total
        elif fps:
            index=max(0,int(age*rate))
            return min(index,len(self.durations)-1) if self.clamp else index%len(self.durations)
        else:
            t=max(0,age*rate)
            t=min(t,np.nextafter(total,0)) if self.clamp else t%total
        return min(int(np.searchsorted(np.cumsum(self.durations),t,side='right')),len(self.durations)-1)


def read_sequences(vtf):
    """Return sequence IDs -> frames; reject malformed resources rather than crop guesses."""
    if len(vtf)<80 or vtf[:4]!=b'VTF\0':return {}
    minor=struct.unpack_from('<I',vtf,8)[0]
    if minor<3:return {}
    count=struct.unpack_from('<I',vtf,68)[0]
    if count>(len(vtf)-80)//8:raise ValueError('truncated VTF resource directory')
    for i in range(count):
        tag,flags,offset=struct.unpack_from('<3sBI',vtf,80+i*8)
        if tag!=b'\x10\0\0':continue
        if flags & 2:raise ValueError('inline sheet resource')
        if offset+4>len(vtf):raise ValueError('truncated sheet length')
        size=struct.unpack_from('<I',vtf,offset)[0];end=offset+4+size;p=offset+4
        if end>len(vtf):raise ValueError('truncated sheet payload')
        def take(fmt):
            nonlocal p
            n=struct.calcsize(fmt)
            if p+n>end:raise ValueError('truncated sheet field')
            result=struct.unpack_from(fmt,vtf,p);p+=n;return result
        version,number=take('<II')
        if version not in (0,1):raise ValueError('unknown sheet version')
        coordinates=1 if version==0 else 4
        result={}
        for _ in range(number):
            ident,clamp,frames,total=take('<IIIf')
            if not frames or frames>(end-p)//(4+16*coordinates):raise ValueError('invalid sheet frame count')
            durations=[];rects=[]
            for frame in range(frames):
                duration,=take('<f')
                if not math.isfinite(duration) or duration<=0:raise ValueError('invalid frame duration')
                durations.append(duration)
                rect=[take('<4f') for _ in range(coordinates)]
                if not np.isfinite(rect).all():raise ValueError('invalid UV coordinates')
                rects.append(rect)
            result[ident]=Sequence(bool(clamp),np.array(durations),np.array(rects))
        return result
    return {}


def material_texture(content,material):
    from .gamematerials import read_vmt,read_texture_bytes,texture_path
    params=read_vmt(content,material.removesuffix('.vmt'))
    name=params.get('$basetexture')
    if not name:return None,params
    data=read_texture_bytes(content,texture_path(name))
    return data,params


def load_sequences(content,material):
    """Read sheet timing without decoding/uploading texture pixels."""
    data,_=material_texture(content,material)
    return read_sequences(data) if data is not None else {}


def load_atlas(content,material):
    from .vtf_read import read_vtf
    data,params=material_texture(content,material)
    if data is None:return None
    _,_,image=read_vtf(data,max_size=1024)
    sequences=read_sequences(data)
    additive=params.get('$additive','0').strip() in ('1','1.0')
    return image.astype(np.float32)/255,sequences,additive
