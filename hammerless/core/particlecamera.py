"""Fixed diagnostic perspective camera in Source units; no scene mutations."""
import math
import numpy as np


def camera(spec):
    eye=np.asarray(spec['eye'],dtype=float);target=np.asarray(spec['target'],dtype=float)
    up=np.asarray(spec.get('up',[0,0,1]),dtype=float)
    if any(v.shape!=(3,) or not np.isfinite(v).all() for v in (eye,target,up)):raise ValueError('camera vectors must be finite XYZ')
    forward=target-eye
    if np.linalg.norm(forward)<1e-9:raise ValueError('camera eye and target coincide')
    forward/=np.linalg.norm(forward);right=np.cross(forward,up)
    if np.linalg.norm(right)<1e-9:raise ValueError('camera up is parallel to view')
    right/=np.linalg.norm(right);up=np.cross(right,forward)
    width=int(spec.get('width',768));height=int(spec.get('height',576))
    fov=float(spec.get('horizontal_fov',90));near=float(spec.get('near',1));far=float(spec.get('far',10000))
    if not 16<=width<=4096 or not 16<=height<=4096:raise ValueError('camera image dimensions must be 16..4096')
    if not all(math.isfinite(x) for x in (fov,near,far)) or not 1<fov<179 or not 0<near<far:raise ValueError('invalid camera projection')
    view=np.eye(4);view[:3,:3]=np.stack((right,up,-forward));view[:3,3]=-view[:3,:3]@eye
    x=1/math.tan(math.radians(fov)/2)
    projection=np.zeros((4,4));projection[0,0]=x;projection[1,1]=x*width/height
    projection[2,2]=-(far+near)/(far-near);projection[2,3]=-2*far*near/(far-near);projection[3,2]=-1
    return dict(mvp=projection@view,right=right,up=up,eye=eye,width=width,height=height)
