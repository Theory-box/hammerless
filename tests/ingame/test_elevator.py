"""Integration test: regenerate hotel nav and verify bots board, ride and exit its elevator.
This controlled bot test bypasses buttons and teleports the host to the lower hall.
It does not verify human exit collision or the normal campaign event sequence.
Run from the repository root with L4D2 installed. Uses only hl_test_elevator_generated copies.
"""
import sys,json,shutil
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from tests.ingame.bench import Bench,BenchError
from hammerless.core import compile as cc, vmf
from hammerless.core.bspdecompile import decompile
from hammerless.core.navpredict import predict
from hammerless.core.nav import collect_regions
from hammerless.core.vmfimport import semantic_entities
from hammerless.core.ir import MapIR
from hammerless.core.navfile import write_nav
root=Path(cc.find_game_root())/'left4dead2'/'maps'
raw=(root/'c1m1_hotel.bsp').read_bytes()
text,_=decompile(raw)
regions,_=collect_regions(MapIR(imported_entities=semantic_entities([b for b in vmf.parse(text) if b.name=='entity'])))
mesh=predict(text,regions)
assert mesh.elevator_links, mesh.problems
mesh.bsp_size=len(raw)
(root/'hl_test_elevator_generated.bsp').write_bytes(raw)
(root/'hl_test_elevator_generated.nav').write_bytes(write_nav(mesh))
output=Path(sys.argv[1]) if len(sys.argv)>1 else Path('elevator-test.json')
with Bench() as b:
 b.load('hl_test_elevator_generated',timeout=90,settle=10)
 b.wait_until('GetListenServerHost().GetMoveParent()==null && GetListenServerHost().GetOrigin().z<3000',timeout=75)
 b.console('sv_cheats 1','god 1','director_stop')
 b.run('EntFire("elevator_1_door1","Open","",0);EntFire("elevator_1_door2","Open","",0);local p=null;local n=0;while((p=Entities.FindByClassname(p,"player"))!=null){if(p.IsSurvivor()){p.SetOrigin(Vector(2120+32*n,5635,2470));n++;if(IsPlayerABot(p))CommandABot({bot=p,cmd=1,pos=Vector(2168,5820,2464)});else p.SetOrigin(Vector(2168,5840,2470));}}return n;')
 def positions():
  return b.run('local out=[];local p=null;while((p=Entities.FindByClassname(p,"player"))!=null)if(p.IsSurvivor())out.append({bot=IsPlayerABot(p),pos=p.GetOrigin()});return out;')
 result={'initial_positions':positions()}
 try:
  b.wait_until('((function(){local p=null;while((p=Entities.FindByClassname(p,"player"))!=null)if(p.IsSurvivor() && IsPlayerABot(p) && p.GetOrigin().y<5730)return false;return true;})())',timeout=20)
  result['boarded']=True
 except BenchError as e:result['boarded']=False
 result['boarding_positions']=positions()
 if result['boarded']:
  b.run('EntFire("elevator_1","MoveToFloor","stop1",0);return true;')
  b.wait_until('Entities.FindByName(null,"elevator_1").GetOrigin().z<1185',timeout=55)
  result['after_ride']=positions()
  output.write_text(json.dumps(result,indent=2))
  print(json.dumps(result),flush=True)
  b.run('local p=null;while((p=Entities.FindByClassname(p,"player"))!=null){if(p.IsSurvivor()){if(IsPlayerABot(p)){CommandABot({bot=p,cmd=1,pos=Vector(2168,5600,1184)});}else{p.SetOrigin(Vector(2168,5550,1190));}}}return true;')
  try:
   b.wait_until('((function(){local p=null;while((p=Entities.FindByClassname(p,"player"))!=null)if(p.IsSurvivor() && IsPlayerABot(p) && (p.GetOrigin().y>5710 || p.GetOrigin().z>1250))return false;return true;})())',timeout=20)
   result['exited']=True
  except BenchError:result['exited']=False
  result['exit_positions']=positions()
 output.write_text(json.dumps(result,indent=2))
 print(json.dumps(result),flush=True)





assert result.get('boarded') and result.get('exited'), result
assert len([p for p in result['after_ride'] if p['bot']]) == 3, result
assert all(p['pos'][2] < 1250 for p in result['after_ride']), result

