"""Compile a volume fixture and assert non-rendering faces have no baked samples (CPU and GPU)."""
from pathlib import Path
import sys, subprocess,struct,json
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hammerless.core.ir import MapIR,Entity
from hammerless.core.geometry import box_brush
from hammerless.core.build import build_vmf
from hammerless.core.compile import find_game_root
from hammerless.core.bspdecompile import Bsp
from hammerless.core.lightmap import read_lightmaps
root=Path(__file__).resolve().parents[2];out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
game=Path(find_game_root())/'left4dead2'; core=root/'hammerless/core'
ir=MapIR();ir.settings.name='volume_fixture';ir.settings.auto_detail='OFF';ir.settings.auto_light_environment=False
mat='dev/dev_measuregeneric01b'
ir.brushes.append(box_brush((-256,-256,-16),(256,256,0),mat))
ir.brushes.append(box_brush((-4,-192,0),(4,192,160),'tools/toolsblocklight'))
ir.entities.append(Entity('light',(-128,0,128),keyvalues={'_light':'255 255 255 800'}))
for cls,x,material in [('fog_volume',-120,mat),('trigger_multiple',80,mat),('func_brush',160,mat),('fog_volume',-200,'tools/fogvolume')]:
 ir.entities.append(Entity(cls,brushes=[box_brush((x,-80,8),(x+32,-48,72),material)]))
text,rep=build_vmf(ir);assert rep.ok,rep.errors
vmf=out/'fixture.vmf';vmf.write_text(text)
subprocess.run([str(game.parent/'bin/vbsp.exe'),'-game',str(game),str(vmf)],check=True,stdout=(out/'vbsp.log').open('w'),stderr=subprocess.STDOUT)
base=vmf.with_suffix('.bsp').read_bytes();results={}
for mode in ('cpu','gpu'):
 path=out/(mode+'.bsp');path.write_bytes(base)
 cmd=[str(core/'hlvrad.exe'),'-noextra','-bounce','0','-game',str(game),str(path)]
 if mode=='gpu':cmd.insert(1,'-gpu')
 subprocess.run(cmd,check=True,stdout=(out/(mode+'.log')).open('w'),stderr=subprocess.STDOUT)
 b=Bsp(path.read_bytes());faces=b.lump(58) or b.lump(7);models=b.models();counts={}
 for pairs in b.entities():
  e=dict(pairs);model=e.get('model','')
  if not model.startswith('*'):continue
  # Model's final two ints are first face and number of faces.
  first,n=struct.unpack_from('<ii',b.lump(14),int(model[1:])*48+40)
  lit=sum(struct.unpack_from('<i',faces,i*56+20)[0]>=0 for i in range(first,first+n))
  cls=e['classname'];counts[cls]=counts.get(cls,0)+lit
  if cls.startswith('trigger_') or cls=='fog_volume':assert lit==0,(mode,cls,lit)
  if cls=='func_brush':assert lit>0,(mode,cls,lit)
 lm=read_lightmaps(path.read_bytes());assert lm.faces>0
 assert not any(lm.materials[i] in ('tools/fogvolume','tools/toolsblocklight') for i in lm.tri_material if i>=0)
 # Bake changes must not remove geometry or collision used for shadows/gameplay.
 for lump in (0,1,14,18,19):assert b.lump(lump)==Bsp(base).lump(lump),(mode,lump)
 results[mode]=counts
# toolsblocklight must still cast a shadow even though it receives no lightmap.
from hammerless.core.lightbake import read_faces
from hammerless.core.lightmap import decode_luxels
import numpy as np

def floor_sample(path):
 data=path.read_bytes(); fs,li=read_faces(data); b=Bsp(data)
 for f in fs:
  if f.normal[2]>.99 and np.max(np.abs(f.positions[:,2]))<.01:
   uv=f.to_luxel(np.array([[128.,0.,0.]]))[0]
   x,y=(int(round(float(v))) for v in uv)
   if 0<=x<f.w and 0<=y<f.h:
    at=f.light_ofs+4*(y*f.w+x)
    return float(decode_luxels(b.lump(li)[at:at+4]).mean())
 raise AssertionError('floor sample missing')

ir.brushes.pop(1)
text,rep=build_vmf(ir);assert rep.ok
clear=out/'no_blocker.vmf';clear.write_text(text)
subprocess.run([str(game.parent/'bin/vbsp.exe'),'-game',str(game),str(clear)],check=True,stdout=(out/'no_blocker_vbsp.log').open('w'),stderr=subprocess.STDOUT)
subprocess.run([str(core/'hlvrad.exe'),'-noextra','-bounce','0','-game',str(game),str(clear.with_suffix('.bsp'))],check=True,stdout=(out/'no_blocker_rad.log').open('w'),stderr=subprocess.STDOUT)
shadow,lit=floor_sample(out/'cpu.bsp'),floor_sample(clear.with_suffix('.bsp'))
assert lit>shadow*2+0.01,(shadow,lit)
results['blocker_shadow']={'blocked':shadow,'unblocked':lit}
(out/'result.json').write_text(json.dumps(results,indent=2));print(json.dumps(results))
