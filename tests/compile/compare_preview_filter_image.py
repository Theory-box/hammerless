"""Offline planar-filter image test. Does not modify the renderer or launch games."""
from pathlib import Path
import json, math, sys, hashlib
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.core.previewlighting import PointLight
OUT=ROOT.parents[1]/'outputs/l4d2-preview/game-shadow-still'
c=json.loads((OUT/'capture.json').read_text())
assert hashlib.sha256(Path(c['source']).read_bytes()).hexdigest()==c['sha256']
assert c['camera']['angles'][1:]==[0,0], 'Fixture supports yaw/roll zero only'
pitch=math.radians(c['camera']['angles'][0]);eye=np.array(c['camera']['eye'])
y,x=np.mgrid[:480,:640]
u=(x+.5-320)/320;v=(240-y-.5)/320
forward=np.array([math.cos(pitch),0,-math.sin(pitch)])
up=np.array([math.sin(pitch),0,math.cos(pitch)])
rays=forward+u[...,None]*np.array([0,-1,0])+v[...,None]*up
p=eye+rays*(-eye[2]/rays[...,2])[...,None]
light=PointLight.from_keys({'_light':'255 180 90 200','_quadratic_attn':'1','_linear_attn':'0','_constant_attn':'0'})
def blocked(start,delta):
    enter=np.zeros(delta.shape[:-1]);leave=np.ones_like(enter)
    for a,lo,hi in ((0,0,16),(1,-16,16),(2,24,40)):
        d=delta[...,a];s=np.broadcast_to(start,delta.shape)[...,a]
        parallel=abs(d)<1e-10;safe=np.where(parallel,1,d)
        near=(lo-s)/safe;far=(hi-s)/safe
        enter=np.maximum(enter,np.where(parallel,-np.inf,np.minimum(near,far)))
        leave=np.minimum(leave,np.where(parallel,np.inf,np.maximum(near,far)))
        leave=np.where(parallel&((s<lo)|(s>hi)),-np.inf,leave)
    return enter<=leave

def direct(q):
    receiver=q+np.array([0,0,1]);delta=np.array([-64,0,128])-receiver
    dist=np.linalg.norm(delta,axis=-1)
    value=delta[...,2]/dist**3/255
    return np.where(blocked(receiver,delta),0,value)
base=np.floor(p[...,:2]/4)*4;fraction=p[...,:2]/4-np.floor(p[...,:2]/4)
filtered=np.zeros((480,640))
for ix in (0,1):
    for iy in (0,1):
        corner=np.concatenate((base+np.array([ix,iy])*4,np.zeros((480,640,1))),axis=-1)
        val=sum(direct(corner+np.array([dx,dy,0])) for dx in (-2,2) for dy in (-2,2))/4
        weight=(fraction[...,0] if ix else 1-fraction[...,0])*(fraction[...,1] if iy else 1-fraction[...,1])
        filtered+=weight*val

def decode(a):return np.where(a<=.04045,a/12.92,((a+.055)/1.055)**2.4)
def encode(a):return np.where(a<=.0031308,12.92*a,1.055*np.maximum(a,0)**(1/2.4)-.055)
albedo=decode(np.asarray(Image.open(OUT/'preview-fullbright1.png').convert('RGB'))/255)
ref=np.asarray(Image.open(next(OUT.glob('hlref_*_fullbright0.jpg'))).convert('RGB'))/255
mask=(p[...,0]>=20)&(p[...,0]<=80)&(abs(p[...,1])<=48)&~blocked(eye,p-eye)
assert mask.sum()>5000
report={'pixels':int(mask.sum()),'scope':'Visible planar-floor ROI only, source texture from prior unlit preview, analytic fixture occluder. No fitted correction; PNG/JPEG quantization and game postprocessing remain.'}
for name,irr in [('hard',direct(p)),('filtered',filtered)]:
    image=encode(albedo*irr[...,None]*np.array(light.intensity))
    report[name]={'mean_absolute_error':float(np.mean(abs(image-ref)[mask])), 'rms':float(np.sqrt(np.mean((image-ref)[mask]**2)))}
    Image.fromarray(np.uint8(np.clip(image,0,1)*255)).save(OUT/f'offline-{name}.png')
(OUT/'offline-filter-comparison.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
