"""Live diagnostic regression; requires a prepared map with valid flow through an elevator."""
from pathlib import Path
import sys,json
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if len(sys.argv) != 3:
 raise SystemExit('Usage: python tests/ingame/test_flow_diagnostics.py <prepared-elevator-map> <report.json>')
map_name, report_path = sys.argv[1:]
from tests.ingame.bench import Bench
from hammerless.core.gamefiles import READY_SCRIPT
script=READY_SCRIPT.replace('HLR_', 'HLTest_').replace('printl(', 'HL_TestPrint(')
with Bench() as b:
 b.load(map_name,timeout=90,settle=3)
 prefix='::HL_TestLines <- []; ::HL_TestPrint <- function(s) { ::HL_TestLines.append(s); };\n'
 code=prefix+script+'\nHLTest_FlowReport(); local actual=clone HL_TestLines; HL_TestLines.clear(); '
 # Force only the diagnostic fallback; leave the real Director and nav untouched.
 fallback=script.replace('GetMaxFlowDistance()', '0.0')
 code+=fallback+'\nHLTest_FlowReport();return {actual=actual,fallback=HL_TestLines,flow=GetMaxFlowDistance()};'
 data=b.run(code,timeout=30)
 print(json.dumps(data),flush=True)
 Path(report_path).write_text(json.dumps(data,indent=2))
 assert data['flow']>0
 assert data['fallback']==['HAMMERLESS_FLOW broken connected'],data
 testscript=script.replace('GetMaxFlowDistance()', '0.0').replace('Entities.FindByClassname(', 'HL_TestFind(').replace('NavMesh.GetAllAreas(', 'HL_TestAreas(')
 cases={}
 for state,flags,hasexit in [('nonav',[],True),('noexit',[128,2048],False),('nostart',[2048],True),('noend',[128],True)]:
  setup='::HL_TestLines <- []; ::HL_TestFind <- function(a,b){return '+('1' if hasexit else 'null')+';}; ::HL_TestAreas <- function(out){'
  for i,flag in enumerate(flags):setup+='out['+str(i)+'] <- {GetSpawnAttributes=function(){return '+str(flag)+';}};'
  setup+='};'
  result=b.run(setup+testscript+';HLTest_FlowReport();return HL_TestLines;')
  assert result==['HAMMERLESS_FLOW '+state],result
  cases[state]=result
 data['cases']=cases
 Path(report_path).write_text(json.dumps(data,indent=2))
 print('All flow diagnostic states passed',flush=True)
