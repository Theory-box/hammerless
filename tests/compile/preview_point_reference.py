"""Compare preview point-light math with CPU/GPU VRAD lightmaps, no game launch.

Run with Blender --background --factory-startup --python this.py -- OUTPUT_DIR.
Writes only to OUTPUT_DIR. Direct-only, opaque planar receiver; not GI/material parity.
Default one-unit lightmap spacing limits resampling error for equation validation.
--coarse (16) and --fine (4) intentionally retain the same strict gate to reveal
resampling differences; a failure in those modes must not be called exact parity.
"""
from pathlib import Path
import json
import subprocess
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from hammerless.core.ir import MapIR, Entity
from hammerless.core.geometry import box_brush
from hammerless.core.build import build_vmf
from hammerless.core.compile import find_game_root
from hammerless.core.lightbake import read_faces
from hammerless.core.lightmap import decode_luxels
from hammerless.core.bspdecompile import Bsp
from hammerless.core.previewlighting import PointLight, spot_parameters, spot_factor, vrad_light_direction, sky_intensity

out = Path(sys.argv[sys.argv.index('--')+1]).resolve()
out.mkdir(parents=True, exist_ok=True)
game_root = find_game_root()
assert game_root, 'L4D2 installation not found'
game = Path(game_root) / 'left4dead2'
core = ROOT / 'hammerless/core'

def run(command, name):
    with (out / (name+'.log')).open('w') as log:
        subprocess.run([str(x) for x in command], check=True, stdout=log,
                       stderr=subprocess.STDOUT, timeout=120)

ir = MapIR()
ir.settings.name = 'preview_point_reference'
ir.settings.auto_light_environment = False
ir.settings.ambient_brightness = 0
ir.settings.auto_detail = 'OFF'
ir.brushes.append(box_brush((-128,-128,-16),(128,128,0),'dev/dev_measuregeneric01b'))
for face in ir.brushes[0].faces:
    face.lightmap_scale = 16 if '--coarse' in sys.argv else 1
if '--fine' in sys.argv:
    for face in ir.brushes[0].faces:
        face.lightmap_scale = 4
if '--finest' in sys.argv:
    for face in ir.brushes[0].faces:
        face.lightmap_scale = 1
keys = {'_light':'255 180 90 200', '_constant_attn':'0', '_linear_attn':'0', '_quadratic_attn':'1'}
spot = '--spot' in sys.argv
sky = '--sky' in sys.argv
sun = '--sun' in sys.argv or sky
angles = (-90.,0.,0.) if spot or sun else (0.,0.,0.)
if spot:
    keys.update(_inner_cone='20',_cone='60',_exponent='2.3')
if sun:
    keys.update(_constant_attn='1',_quadratic_attn='0',_ambient='0 0 0 0',SunSpreadAngle='0')
if sky:
    keys.update(_ambient=keys['_light'],_light='0 0 0 0')
origin = np.array([0.,0.,128.])
ir.entities.append(Entity('light_environment' if sun else 'light_spot' if spot else 'light', tuple(origin), angles=angles, keyvalues=keys))
text, report = build_vmf(ir)
assert report.ok, report.errors
vmf = out / 'reference.vmf'
vmf.write_text(text)
run([Path(game_root)/'bin/vbsp.exe','-game',game,vmf], 'vbsp')
run([Path(game_root)/'bin/vvis.exe','-fast','-game',game,vmf.with_suffix('.bsp')], 'vvis')
base = vmf.with_suffix('.bsp').read_bytes()
light = PointLight.from_keys(keys)
results = {}
for mode in ('cpu','gpu'):
    path = out / (mode+'.bsp')
    path.write_bytes(base)
    command = [core/'hlvrad.exe','-noextra','-bounce','0','-game',game,path]
    if mode == 'gpu':
        command.insert(1,'-gpu')
    run(command, mode)
    if mode == 'gpu':
        assert 'Lighting on the GPU:' in (out/'gpu.log').read_text(), 'VRAD fell back to CPU'
    data = path.read_bytes()
    bsp = Bsp(data)
    faces, lump = read_faces(data)
    actual, expected, positions = [], [], []
    for face in faces:
        if face.light_ofs<0 or face.normal[2]<0.99 or np.max(np.abs(face.positions[:,2]))>0.01:
            continue
        values = decode_luxels(bsp.lump(lump)[face.light_ofs:face.light_ofs+4*face.w*face.h]).reshape(face.h,face.w,3)
        for y in range(1,face.h-1):
            for x in range(1,face.w-1):
                p = face.to_world(np.array([x,y]))
                # direct.c SetupGatherInput adds one Source unit along the face
                # normal before evaluating both distance and visibility.
                delta = origin-(p+face.normal)
                distance = np.linalg.norm(delta)
                actual.append(values[y,x])
                factor = spot_factor(float(-delta@np.array(vrad_light_direction(angles,keys))/distance),spot_parameters(keys)) if spot else 1.0
                if sky:
                    expected.append(np.array(sky_intensity(keys,light.intensity))/255)
                elif sun:
                    expected.append(np.array(light.intensity)*max(0.,-float(face.normal@np.array(vrad_light_direction(angles,keys))))/255)
                else:
                    expected.append(np.array(light.irradiance(distance,float(delta@face.normal/distance)))*factor/255)
                positions.append(p.tolist())
    assert actual, 'No interior receiver samples found'
    actual, expected = np.array(actual), np.array(expected)
    relative = np.abs(actual-expected)/np.maximum(expected,1e-8)
    results[mode] = {'samples':len(actual),'max_relative_error':float(relative.max()),
        'max_absolute_error':float(np.abs(actual-expected).max()),
        'mean_relative_error':float(relative.mean()),'center_actual':actual[len(actual)//2].tolist(),
        'center_expected':expected[len(expected)//2].tolist()}
    (out/'results.json').write_text(json.dumps(results,indent=2))
    # Compare continuous evaluation with VRAD's radial resampling and RGBExp32
    # quantization. This is a bounded absolute-error gate, not bitwise parity.
    assert np.abs(actual-expected).max()<1/255, results[mode]
print(json.dumps(results),flush=True)
