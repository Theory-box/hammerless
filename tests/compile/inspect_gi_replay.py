"""Offline persistent-GI replay inspection: exact face lightmaps, not viewport parity.

Reads a normal GPU-VRAD reference BSP and HGI1 replay artifacts. Does not launch
Blender/game/compiler or modify either input. Contact sheets show style-0 flat
lightmaps; metrics cover every style and bump page of renderable lit faces.
"""
from pathlib import Path
import argparse
import hashlib
import json
import struct
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hammerless.core.lightbake import read_faces
from hammerless.core.lightmap import decode_luxels, _lumps, FACE_SIZE

MAGIC=0x48474931

def replay(data):
    if len(data)<16:raise ValueError('Truncated replay header')
    magic,count,stride,size=struct.unpack_from('<4I',data)
    if magic!=MAGIC or stride!=FACE_SIZE:raise ValueError('Unsupported replay magic/face record size')
    stop=16+count*stride
    if len(data)!=stop+size or size%4:raise ValueError('Replay payload length mismatch')
    return data[16:stop],data[stop:]

def override_lump(bsp,index,payload):
    # An in-memory BSP view lets the existing parser validate real face geometry.
    out=bytearray(bsp);offset=len(out);out.extend(payload)
    version,_,_,_=struct.unpack_from('<4i',out,8+index*16)
    struct.pack_into('<4i',out,8+index*16,version,offset,len(payload),0)
    return bytes(out)

def pages(face,lighting):
    count=face.w*face.h*(4 if face.bump else 1)*face.nstyles
    start=face.light_ofs;stop=start+count*4
    if start<0 or stop>len(lighting):raise ValueError(f'Face {face.index} lightmap range outside payload')
    return decode_luxels(lighting[start:stop]).reshape(face.nstyles,4 if face.bump else 1,face.h,face.w,3)

def metrics(a,b):
    difference=np.abs(a.astype(np.float64)-b.astype(np.float64))
    return {'samples':int(a.size//3),'max_abs_linear':float(difference.max(initial=0)),
            'mean_abs_linear':float(difference.mean()) if difference.size else 0.,
            'rms_linear':float(np.sqrt(np.mean(difference*difference))) if difference.size else 0.,
            'equal_channels':int(np.count_nonzero(a==b)), 'channels':int(a.size)}

def display(data,exposure):
    from PIL import Image
    data=np.maximum(data*(2.**exposure),0)
    srgb=np.where(data<=.0031308,data*12.92,1.055*np.power(data,1/2.4)-.055)
    return Image.fromarray(np.uint8(np.clip(srgb,0,1)*255+.5),'RGB')

def sheet(rows,path,exposure,error_scale,limit):
    from PIL import Image, ImageDraw
    rows=sorted(rows,key=lambda x:(-x[3]['max_abs_linear'],-x[1].size))[:limit]
    tile_w,tile_h=300,220
    image=Image.new('RGB',(tile_w*3,max(1,len(rows))*tile_h+55),(28,28,28));draw=ImageDraw.Draw(image)
    for col,label in enumerate(('GPU VRAD reference','Persistent replay','Absolute linear error')):
        draw.text((col*tile_w+10,8),label,fill='white')
    draw.text((10,30),f'Exposure {exposure:+g} stops; error white = {error_scale:g}. Flat style-0 luxels, nearest filtering.',fill='white')
    for row,(index,reference,candidate,stats) in enumerate(rows):
        error=np.max(np.abs(reference-candidate),axis=2)/error_scale
        error_image=Image.fromarray(np.uint8(np.clip(error,0,1)*255+.5),'L').convert('RGB')
        for col,thumb in enumerate((display(reference,exposure),display(candidate,exposure),error_image)):
            ratio=min((tile_w-20)/thumb.width,(tile_h-45)/thumb.height)
            thumb=thumb.resize((max(1,int(thumb.width*ratio)),max(1,int(thumb.height*ratio))),Image.Resampling.NEAREST)
            x=col*tile_w+(tile_w-thumb.width)//2;y=55+row*tile_h+25
            image.paste(thumb,(x,y))
            draw.text((col*tile_w+8,55+row*tile_h),f'Face {index} | {reference.shape[1]}x{reference.shape[0]} | max {stats["max_abs_linear"]:.6g}',fill='white')
    image.save(path)

def inspect(reference_path,artifact_path,out,exposure=0,error_scale=.1,limit=12):
    reference=reference_path.read_bytes();raw_faces,raw_light=replay(artifact_path.read_bytes())
    ref_lump=_lumps(reference)
    if not ref_lump(53):raise ValueError('Reference must contain HDR lighting')
    reference_faces,lump=read_faces(reference)
    if lump!=53:raise ValueError('Reference face reader did not select HDR')
    ref_facebytes=ref_lump(58) or ref_lump(7)
    if len(raw_faces)!=len(ref_facebytes):raise ValueError('Replay/reference face counts differ')
    candidate=override_lump(override_lump(reference,58,raw_faces),53,raw_light)
    candidate_faces,_=read_faces(candidate)
    if [f.index for f in candidate_faces]!=[f.index for f in reference_faces]:raise ValueError('Renderable lit face sets differ')
    all_ref=[];all_candidate=[];rows=[];per_face=[]
    for a,b in zip(reference_faces,candidate_faces):
        if (a.w,a.h,a.bump,a.nstyles,a.style_slot)!=(b.w,b.h,b.bump,b.nstyles,b.style_slot):
            raise ValueError(f'Face {a.index} layout/style mismatch')
        if ref_facebytes[a.index*56+16:a.index*56+20]!=raw_faces[b.index*56+16:b.index*56+20]:
            raise ValueError(f'Face {a.index} style IDs differ')
        if not np.array_equal(a.positions,b.positions):raise ValueError(f'Face {a.index} geometry differs')
        if not np.array_equal(a.lux,b.lux) or not np.array_equal(a.normal,b.normal):
            raise ValueError(f'Face {a.index} lightmap mapping/normal differs')
        x,y=pages(a,ref_lump(53)),pages(b,raw_light)
        stats=metrics(x,y);per_face.append({'face':a.index,**stats})
        all_ref.append(x.reshape(-1,3));all_candidate.append(y.reshape(-1,3))
        rows.append((a.index,x[a.style_slot,0],y[b.style_slot,0],stats))
    if not rows:raise ValueError('No renderable lit faces to compare')
    summary=metrics(np.concatenate(all_ref),np.concatenate(all_candidate))
    name=artifact_path.stem
    image_path=out/(name+'-lightmaps.png')
    sheet(rows,image_path,exposure,error_scale,limit)
    result={'reference':str(reference_path.resolve()),'artifact':str(artifact_path.resolve()),
            'reference_sha256':hashlib.sha256(reference).hexdigest(),
            'artifact_sha256':hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            'scope':'Every style/bump page of renderable faces with static style 0; no props/ambient or in-game visual parity claim',
            'faces':len(rows),'metrics':summary,'per_face':per_face,'contact_sheet':str(image_path.resolve()),
            'exposure_stops':exposure,'error_white_linear':error_scale}
    (out/(name+'-comparison.json')).write_text(json.dumps(result,indent=2))
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--blob',type=Path,nargs='+',required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--exposure',type=float,default=0)
    parser.add_argument('--error-scale',type=float,default=.1)
    parser.add_argument('--faces',type=int,default=12)
    args=parser.parse_args()
    if not np.isfinite([args.exposure,args.error_scale]).all() or args.error_scale<=0 or args.faces<1:parser.error('Invalid display parameters')
    args.out.mkdir(parents=True,exist_ok=True)
    for artifact in args.blob:
        result=inspect(args.reference,artifact,args.out,args.exposure,args.error_scale,args.faces)
        print(json.dumps({'artifact':str(artifact),'faces':result['faces'],**result['metrics']}))

if __name__=='__main__':main()
