"""Conservative material settings for the independent particle preview shader."""
import math
import re
from .gamematerials import read_vmt


def profile(params):
    def number(key,default):
        try:value=float(params.get(key,default))
        except (TypeError,ValueError):return default
        return value if math.isfinite(value) else default
    raw=re.findall(r'[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?',str(params.get('$cropfactor','[1 1]')))
    crop=tuple(float(v) for v in raw[:2]) if len(raw)>=2 else (1.,1.)
    if not all(math.isfinite(v) and 0<v<=1 for v in crop):crop=(1.,1.)
    dual=number('$dualsequence',0)!=0
    mode=int(number('$sequence_blend_mode',0)) if dual else -1
    warnings=[]
    handled={'shader','$basetexture','$addself','$blendframes','$cropfactor','$overbrightfactor','$alpha','$alphatest','$alphatestreference','$dualsequence','$sequence_blend_mode','$maxlumframeblend1','$maxlumframeblend2','$zoomanimateseq2','$additive','$vertexalpha','$vertexcolor','$translucent'}
    for key in sorted(params):
        if key not in handled and key not in ('$depthblend','$addoverblend','$distancealpha','$extractgreenalpha'):
            warnings.append('Unimplemented material setting '+key)
    if params.get('shader','').lower() not in ('spritecard','unlitgeneric','refract',''):
        warnings.append('Unsupported material shader '+params['shader'])
    if mode not in (-1,0,1,2):
        warnings.append('Unsupported dual-sequence blend mode '+str(mode));mode=-1
    shader=params.get('shader','').lower()
    if shader=='refract':warnings.append('Refraction needs scene colour/depth; preview displays base texture only')
    if dual:warnings.append('Dual-sequence blending is approximate; Source material parity is unverified')
    for key in ('$depthblend',
                '$addoverblend','$distancealpha','$extractgreenalpha'):
        if number(key,0)!=0:warnings.append('Unsupported material parameter '+key)
    addself=max(0,number('$addself',0))
    if addself and number('$additive',0):
        warnings.append('Combined additive and addself material: addself ignored');addself=0
    if addself or number('$zoomanimateseq2',0) or number('$maxlumframeblend1',0) or number('$maxlumframeblend2',0):
        warnings.append('Self-add, zoom and maximum-luminance behavior are independent approximations')
    return dict(crop=crop,addself=addself,blend_frames=number('$blendframes',0)!=0,
                maxlum1=number('$maxlumframeblend1',0)!=0,maxlum2=number('$maxlumframeblend2',0)!=0,
                zoom2=max(1,number('$zoomanimateseq2',1)),dual_mode=mode,brightness=max(0,number('$overbrightfactor',1)),
                alpha=max(0,min(1,number('$alpha',1))),
                cutoff=max(0,min(1,number('$alphatestreference',.5))) if number('$alphatest',0) else -1,
                warnings=warnings)


def load_profile(content,material):
    result=profile(read_vmt(content,material.replace('\\','/').removesuffix('.vmt')))
    from .particlesheet import material_texture
    import struct
    data,_=material_texture(content,material)
    if data is not None and len(data)>=20 and data[:4]==b'VTF\0':
        result['mapping_height']=struct.unpack_from('<H',data,18)[0]
    return result


def blend_mode(additive,material):
    if additive:return 'ADDITIVE'
    return 'ALPHA_PREMULT' if material.get('addself',0)>0 else 'ALPHA'
