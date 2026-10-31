"""Bake surface samples on CPU/GPU and verify forward emission and zero-emission control."""
from pathlib import Path
import sys,subprocess,math,json
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hammerless.core.ir import MapIR
from hammerless.core.geometry import box_brush
from hammerless.core.build import build_vmf
from hammerless.core.compile import find_game_root
from hammerless.core.surfacelights import light_sample,rectangle_samples
from hammerless.core.lightbake import read_faces
from hammerless.core.lightmap import decode_luxels
from hammerless.core.bspdecompile import Bsp
root=Path(__file__).resolve().parents[2]
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
game=Path(find_game_root())/'left4dead2'
core=root/'hammerless/core'
results={}
for direction in (-1,1):
 ir=MapIR();ir.settings.name='surface_fixture';ir.settings.auto_light_environment=False;ir.settings.ambient_brightness=0;ir.settings.auto_detail='OFF'
 ir.brushes.append(box_brush((-128,-128,-16),(128,128,0),'dev/dev_measuregeneric01b'))
 # Fixed total power over an emitting rectangle, facing down/up.
 for x,y,w in rectangle_samples(64,32):
  ir.entities.append(light_sample((x,y,96),(0,0,direction),(200*w,20*w,10*w),'Panel'))
 text,rep=build_vmf(ir);assert rep.ok,rep.errors
 vmf=out/f'panel_{direction}.vmf';vmf.write_text(text)
 subprocess.run([str(game.parent/'bin/vbsp.exe'),'-game',str(game),str(vmf)],check=True,stdout=(out/f'vbsp_{direction}.log').open('w'),stderr=subprocess.STDOUT)
 subprocess.run([str(game.parent/'bin/vvis.exe'),'-fast','-game',str(game),str(vmf.with_suffix('.bsp'))],check=True,stdout=(out/f'vvis_{direction}.log').open('w'),stderr=subprocess.STDOUT)
 base=vmf.with_suffix('.bsp').read_bytes()
 for mode in ('cpu','gpu','gi'):
  path=out/f'panel_{direction}_{mode}.bsp';path.write_bytes(base)
  cmd=[str(core/'hlvrad.exe'),'-noextra','-bounce','0','-game',str(game),str(path)]
  if mode=='gpu':cmd.insert(1,'-gpu')
  if mode=='gi':
   cmd[cmd.index('-bounce')+1]='2'
   cmd[1:1]=['-gpu','-gi','2','-girays','64']
  subprocess.run(cmd,check=True,stdout=(out/f'vrad_{direction}_{mode}.log').open('w'),stderr=subprocess.STDOUT)
  data=path.read_bytes();b=Bsp(data);faces,li=read_faces(data)
  values=[]
  for f in faces:
   if f.normal[2]>.99 and np.max(np.abs(f.positions[:,2]))<.01 and f.light_ofs>=0:
    raw=b.lump(li)[f.light_ofs:f.light_ofs+4*f.w*f.h]
    values.extend(decode_luxels(raw).reshape(-1,3))
  rgb=np.mean(values,axis=0)
  results[f'{direction}_{mode}']=rgb.tolist()
for mode in ('cpu','gpu','gi'):
 down=np.array(results[f'-1_{mode}']);up=np.array(results[f'1_{mode}'])
 assert down[0]>up[0]*10+.01,(mode,down,up)
 assert down[0]>down[1]*5,(mode,down)
(out/'results.json').write_text(json.dumps(results,indent=2));print(results)

