"""Read-only installed-game audit and bounded smoke simulation of every definition.

python tools/audit_particles.py --game ".../Left 4 Dead 2" --output report.json
No game assets are copied into the repository or report.
"""
import argparse
from collections import Counter
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hammerless.core.vpk import GameContent
from hammerless.core.particlepreview import load_library, audit, Simulation
import numpy as np

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--game',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--trees',action='store_true',help='Simulate synchronized child systems too')
    parser.add_argument('--textures',action='store_true',help='Also decode every referenced material and animation sheet')
    parser.add_argument('--seconds',type=float,default=.25,help='Simulation duration per effect (default .25)')
    args=parser.parse_args()
    if not np.isfinite(args.seconds) or not 0 < args.seconds <= 60:
        parser.error('--seconds must be greater than 0 and at most 60')
    content=GameContent(args.game)
    library,errors=load_library(content)
    report=audit(library)
    failures=[]
    runtime_warnings={};sheets={}
    def assets(definition):
        from hammerless.core.particlesheet import load_sequences
        material=definition.attrs.get('material','')
        if material not in sheets:sheets[material]=load_sequences(content,material) if material else {}
        return sheets[material]
    for name,definition in library.items():
        try:
            warnings=set()
            if args.trees:
                from hammerless.core.particleeffect import EffectSimulation
                tree=EffectSimulation(library,name,asset_loader=assets,limit=64)
                tree.advance(args.seconds);systems=tree.systems;warnings.update(tree.warnings)
            else:
                sim=Simulation(definition,limit=64,sequences=assets(definition))
                sim.advance(args.seconds);systems=[sim]
            for sim in systems:
                assert all(np.isfinite(getattr(sim,k)).all() for k in ('position','velocity','color','radius','life','age','rotation'))
                assert all(len(getattr(sim,k))==len(sim.age) for k in ('position','velocity','color','radius','life','ids','flip','sequence','sequence2'))
                assert len(sim.age)<=64
                warnings.update(sim.warnings)
            if warnings:runtime_warnings[name]=sorted(warnings)
        except Exception as exc:
            failures.append({'effect':name,'error':str(exc)})
    priority=Counter(gap for effect in report.values() for gap in effect['missing_rules'])
    parameter_priority=Counter(gap for effect in report.values() for gap in effect['unmapped_parameters'])
    result={'trees':args.trees,'systems':len(library),'simulation_seconds':args.seconds,'parse_errors':errors,'smoke_failures':failures,
            'runtime_warnings':runtime_warnings,
            'missing_rule_priority':dict(priority.most_common()),
            'unmapped_parameter_priority':dict(parameter_priority.most_common()),
            'limitations':['Independent approximate maths; not visually validated against Source',
                           'Animated sprites and random forces; live control points; no collision or bone attachments',
                           'Smoke success does not establish visual fidelity or complete rule support'],
            'coverage':report}
    if args.textures:
        from hammerless.core.particlesheet import load_atlas
        materials=sorted({d.attrs.get('material','') for d in library.values()}-{''})
        textures={'materials':len(materials),'animated_atlases':0,'sequences':0,'missing':[],'errors':[]}
        for material in materials:
            try:
                atlas=load_atlas(content,material)
                if atlas is None:textures['missing'].append(material)
                else:
                    textures['animated_atlases']+=bool(atlas[1]);textures['sequences']+=len(atlas[1])
            except Exception as exc:textures['errors'].append({'material':material,'error':str(exc)})
        result['textures']=textures
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='coverage'},indent=2))
    return int(bool(errors or failures or result.get('textures',{}).get('errors')))

if __name__=='__main__':sys.exit(main())
