import sys
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
from hammerless.blender import quality, ops
hammerless.register()
s=bpy.context.scene.hammerless
s.settings_version=quality.SETTINGS_VERSION
s.light_bounce_method='GI'
s.light_bounces=200
s['light_gi_ceiling']=32
quality.migrate(s)
assert s.light_bounces==32
assert 'light_gi_ceiling' not in s
s.light_bounces=64
quality.migrate(s)
assert s.light_bounces==64
args=ops.compile_options(s).hlvrad_args()
assert args[args.index('-gi')+1]=='64',args
s.light_bounces=0
assert '-gi' not in ops.compile_options(s).hlvrad_args()
del s['_hl_unified_bounces']
s.light_bounce_method='PATCHES'
s.light_bounces=200
s['light_gi_ceiling']=32
quality.migrate(s)
assert s.light_bounces==200
hammerless.unregister()
print('MAX_BOUNCES_PASS')
