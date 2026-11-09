"""Build a sealed, navigable lighting reference without launching/installing it."""
from pathlib import Path
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hammerless.core.compile import find_game_root
from hammerless.core.geometry import box_brush
from hammerless.core.ir import Entity
from hammerless.core.vmf import VMFWriter,Block
from hammerless.core import navpredict,navanalyze
from hammerless.core.navfile import write_nav,read_nav
from hammerless.core.vpk import GameContent
from hammerless.core.bspdecompile import Bsp

out=ROOT.parents[1]/('outputs/l4d2-preview/playable-shadow-reference' if '--shadow' in sys.argv else 'outputs/l4d2-preview/playable-reference')
if '--grid-probe' in sys.argv:
    out=ROOT.parents[1]/'outputs/l4d2-preview/grid-probe-reference'
out.mkdir(parents=True,exist_ok=True)
root=Path(find_game_root());game=root/'left4dead2'
w=VMFWriter()
world=Block('world').kv('id',w.new_id()).kv('classname','worldspawn').kv('skyname','sky_day01_09_hdr')
for lo,hi in [((-256,-256,-16),(256,256,0)),((-256,-256,256),(256,256,272)),
              ((-272,-256,-16),(-256,256,272)),((256,-256,-16),(272,256,272)),
              ((-272,-272,-16),(272,-256,272)),((-272,256,-16),(272,272,272))]:
    brush=box_brush(lo,hi,'dev/dev_measuregeneric01b')
    for f in brush.faces:f.lightmap_scale=4
    world.add(w.solid(brush))
if '--shadow' in sys.argv:
    blocker=box_brush((0,-16,24),(16,16,40),'dev/dev_measuregeneric01b')
    for f in blocker.faces:f.lightmap_scale=4
    world.add(w.solid(blocker))
if '--grid-probe' in sys.argv:
    import math
    # Rotated faces and explicit texture axes probe compiler grid generation.
    for i,angle in enumerate((17,43)):
        brush=box_brush((-24,-24,-8),(24,24,8),'dev/dev_measuregeneric01b')
        a=math.radians(angle)
        for f in brush.faces:
            f.verts=[(x*math.cos(a)+z*math.sin(a)+(-80 if i==0 else 80),
                      y, -x*math.sin(a)+z*math.cos(a)+80) for x,y,z in f.verts]
            f.lightmap_scale=8 if i==0 else 16
            if i==1:
                f.tex_axes=(((.8,.6,0),13,.25),((-.6,.8,0),-7,.5))
        world.add(w.solid(brush))
entities=[Entity('info_player_start',(-128,-128,8)),Entity('info_director',(0,0,16)),
          Entity('light',(-64,0,128) if '--shadow' in sys.argv else (0,0,128),keyvalues={'_light':'255 180 90 200','_constant_attn':'0','_linear_attn':'0','_quadratic_attn':'1'})]
for i in range(4):
    entities.append(Entity('info_survivor_position',(-128+i*64,-128,8),keyvalues={'Order':str(i+1)}))
text=w.document(world,[w.entity(e) for e in entities])
vmf=out/'room.vmf';vmf.write_text(text)
def run(command,name):
    with (out/(name+'.log')).open('w') as log:
        subprocess.run(list(map(str,command)),stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120,
                       creationflags=subprocess.CREATE_NO_WINDOW)
run([root/'bin/vbsp.exe','-game',game,vmf],'vbsp')
bsp=vmf.with_suffix('.bsp')
run([root/'bin/vvis.exe','-game',game,bsp],'vvis')
run([ROOT/'hammerless/core/hlvrad.exe','-gpu','-noextra','-bounce','0','-game',game,bsp],'vrad')
mesh=navpredict.predict(text,[])
navanalyze.analyze(mesh,text,str(bsp),GameContent(str(root)))
nav=write_nav(mesh);vmf.with_suffix('.nav').write_bytes(nav)
decoded=read_nav(nav)
assert decoded.areas,'No navigation generated'
assert Bsp(bsp.read_bytes()).lump(0).count(b'info_survivor_position')==4
assert '**** leaked ****' not in (out/'vbsp.log').read_text().lower()
report={'compiled':True,'nav_areas':len(decoded.areas),'nav_analyzed':decoded.analyzed,
        'survivor_positions':4,'bsp':str(bsp),'runtime_loading_verified':False}
(out/'prepared.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
