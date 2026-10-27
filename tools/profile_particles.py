import sys,time,cProfile,pstats
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from hammerless.core.vpk import GameContent
from hammerless.core.particlepreview import load_library
from hammerless.core.particleeffect import EffectSimulation
from hammerless.core.particlegeometry import build_geometry
from hammerless.core.particlesheet import load_sequences
from hammerless.core.particlematerial import load_profile
import argparse
p=argparse.ArgumentParser();p.add_argument('--game',required=True);args=p.parse_args()
c=GameContent(args.game);lib,_=load_library(c)
t=EffectSimulation(lib,'groundfog_street',asset_loader=lambda d:load_sequences(c,d.attrs.get('material','')))
t.advance(5)
profiles={s.definition.name:load_profile(c,s.definition.attrs.get('material','')) for s in t.systems}
def run():
 for _ in range(30):
  t.advance(1/60)
  for s in t.systems:
   for r in s.definition.rules['renderers']:
    build_geometry(s,r,s.sequences,np.eye(4),[1,0,0],[0,0,1],[0,-768,0],material=profiles[s.definition.name])
p=cProfile.Profile();p.enable();run();p.disable();pstats.Stats(p).sort_stats('cumtime').print_stats(16)
