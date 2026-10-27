"""Read-only installed-game audit and bounded smoke simulation of every definition.

python tools/audit_particles.py --game ".../Left 4 Dead 2" --output report.json
No game assets are copied into the repository or report.
"""
import argparse
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
    parser.add_argument('--textures',action='store_true',help='Also decode every referenced material and animation sheet')
    args=parser.parse_args()
    content=GameContent(args.game)
    library,errors=load_library(content)
    report=audit(library)
    failures=[]
    for name,definition in library.items():
        try:
            sim=Simulation(definition,limit=64)
            sim.advance(.25)
            assert all(np.isfinite(getattr(sim,k)).all() for k in ('position','velocity','color','radius','life','age'))
            assert len(sim.age)<=64
        except Exception as exc:
            failures.append({'effect':name,'error':str(exc)})
    result={'systems':len(library),'parse_errors':errors,'smoke_failures':failures,
            'limitations':['Independent approximate maths; not visually validated against Source',
                           'Animated sprite sheets; no collision, forces, attachments or live control points',
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
