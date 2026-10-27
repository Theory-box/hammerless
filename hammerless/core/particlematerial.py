"""Conservative material settings for the independent particle preview shader."""
import math
from .gamematerials import read_vmt


def profile(params):
    def number(key,default):
        try:value=float(params.get(key,default))
        except (TypeError,ValueError):return default
        return value if math.isfinite(value) else default
    dual=number('$dualsequence',0)!=0
    mode=int(number('$sequence_blend_mode',0)) if dual else -1
    warnings=[]
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
    return dict(addself=addself,blend_frames=number('$blendframes',0)!=0,
                maxlum1=number('$maxlumframeblend1',0)!=0,maxlum2=number('$maxlumframeblend2',0)!=0,
                zoom2=max(1,number('$zoomanimateseq2',1)),dual_mode=mode,brightness=max(0,number('$overbrightfactor',1)),
                alpha=max(0,min(1,number('$alpha',1))),
                cutoff=max(0,min(1,number('$alphatestreference',.5))) if number('$alphatest',0) else -1,
                warnings=warnings)


def load_profile(content,material):
    return profile(read_vmt(content,material.replace('\\','/').removesuffix('.vmt')))


def blend_mode(additive,material):
    if additive:return 'ADDITIVE'
    return 'ALPHA_PREMULT' if material.get('addself',0)>0 else 'ALPHA'
