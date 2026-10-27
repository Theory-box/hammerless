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
    args=parser.parse_args()
    library,errors=load_library(GameContent(args.game))
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
                           'First sprite frame only; no collision, forces, attachments or live control points',
                           'Smoke success does not establish visual fidelity or complete rule support'],
            'coverage':report}
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='coverage'},indent=2))
    return int(bool(errors or failures))

if __name__=='__main__':sys.exit(main())
