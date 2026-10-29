"""Read-only generated-node smoke test on an already running disposable stock map.

No build, map load, teleport, spawn, or bot commands. Uses an existing Bench.
The tiny EMS request includes a temporary .nut file, avoiding StringToFile limits.
Generated script globals are temporary test state: use a stock test map, not an
active user map with Hammerless logic. Reload the test map after the session.

    python tests/ingame/test_worldqueries.py --bsp path/to/current_map.bsp

Root test driver may call run(bench, bsp_path) directly; this module never launches
or changes maps. It requires survivor bots already present for the loop check.
"""
from pathlib import Path
import argparse
import json
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests.ingame.bench import Bench
from hammerless.core.logic import LNode as N,LLink as L,compile_graph
from hammerless.core.ir import MapIR
from hammerless.core.ambientquery import augment_scripts


def graph(position):
    definition={
        "nodes":[N("entry","BA_ENTRY"),N("probe","WQ_NEAREST",consts={"position":position,"radius":256}),
                 N("read","WQ_INFO"),N("remember","BA_STATE_SET",{"name":"nav_found","type":"bool"})],
        "links":[L("entry","start","probe","run"),L("probe","next","read","run"),
                 L("probe","area","read","area",True),L("read","next","remember","run"),
                 L("read","found","remember","value",True)],
        "inputs":[],"supports":"SURVIVORS","channels":[]}
    nodes=[N("near","WQ_NEAREST",consts={"position":position,"radius":256}),
           N("nearby","WQ_NEARBY",consts={"position":position,"radius":512,"limit":8}),
           N("info","WQ_INFO"),N("path","WQ_PATH",consts={"length":8192}),
           N("ambient","WQ_AMBIENT",consts={"position":position}),
           N("find","BA_FIND",{"kind":"SURVIVORS"}),N("loop","BA_LOOP"),
           N("loop_near","WQ_NEAREST",consts={"position":position,"radius":256}),
           N("apply","BA_APPLY"),N("action","BA_ACTION",{"definition":definition})]
    links=[L("near","next","nearby","run"),L("nearby","next","info","run"),
           L("near","area","info","area",True),L("info","next","path","run"),
           L("near","area","path","start",True),L("near","area","path","goal",True),
           L("path","next","ambient","run"),L("find","bots","loop","bots",True),
           L("loop","each","loop_near","run"),L("loop_near","next","apply","run"),
           L("loop","current","apply","bot",True),L("action","action","apply","action",True),
           L("loop_near","found","apply","enabled",True)]
    ir=MapIR()
    errors=compile_graph(nodes,links,ir,"wqsmoke")
    if errors:
        raise AssertionError(errors)
    return ir.extra_scripts


def run(bench,bsp_path):
    if bench.value('"HLBA" in getroottable() && HLBA.loops.len()>0'):
        raise RuntimeError("Active Hammerless bot loops found; use a disposable stock map")
    position=bench.value("GetListenServerHost().GetOrigin() + Vector(0,0,32)")
    mapname=bench.value("Director.GetMapName()")
    files,warnings=augment_scripts(graph(position),bsp_path,mapname)
    script=next(s for p,s in files.items() if "/logic_" in p)
    name="hl_worldquery_smoke_"+uuid.uuid4().hex
    path=Path(bench.game)/"scripts"/"vscripts"/(name+".nut")
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(script,encoding="utf-8")
    try:
        loaded=bench.run('return IncludeScript('+json.dumps(name)+',getroottable());')
        if loaded is False:
            raise AssertionError("Generated node script failed to load")
        normal=bench.run('HL_S_wqsmoke_near(); local n=HL_R.wqsmoke_near; local a=HL_R.wqsmoke_ambient; '
                         'return {nearest=n.available && n.found, info=HL_R.wqsmoke_info.found, '
                         'nearby=HL_R.wqsmoke_nearby.count, path=HL_R.wqsmoke_path.exists, '
                         'ambient=a.available, brightness=a.brightness};')
        loop=bench.run('HL_BA_Start_wqsmoke_loop(); HLBA.TickAll(Time()); '
                       'local loop=HLBA.loops.wqsmoke_loop, states=[]; '
                       'foreach (a in loop.active) states.append(a.ctx.state.nav_found); '
                       'return {states=states,errors=HLBA.errors};')
        result={"map":mapname,"normal":normal,"loop":loop,"warnings":warnings}
        result["passed"]=(normal["nearest"] and normal["info"] and 0<normal["nearby"]<=8 and
                          normal["path"] and normal["ambient"] and bool(loop["states"]) and
                          all(loop["states"]) and not loop["errors"])
        print(json.dumps(result,indent=2),flush=True)
        return result
    finally:
        try:
            bench.run('if ("HL_BA_Stop_wqsmoke_loop" in getroottable()) HL_BA_Stop_wqsmoke_loop(); return true;')
        finally:
            path.unlink(missing_ok=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--bsp",required=True)
    args=parser.parse_args()
    with Bench() as bench:
        result=run(bench,args.bsp)
    raise SystemExit(0 if result["passed"] else 1)
