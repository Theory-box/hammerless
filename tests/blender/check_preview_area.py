"""Area preview samples preserve exporter positions, power and hemisphere."""
from pathlib import Path
import sys,json,math
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props
from hammerless.blender.l4d2preview import SceneCache
from hammerless.blender.emission import area_lights
from hammerless.core.previewlighting import PointLight,vrad_light_direction,spot_parameters
props.register();scene=bpy.context.scene
scene.hammerless.auto_sun=False;scene.hammerless.ambient_brightness=0;scene.hammerless.units_per_meter=52.49
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.object.light_add(type='AREA',location=(1,2,3));lamp=bpy.context.object
lamp.data.energy=100;lamp.data.size=2;lamp.data.size_y=1
lamp.rotation_euler=(.3,.5,.7);lamp.scale=(-1,1.5,1)
cache=SceneCache();rows=[]
for shape in ('SQUARE','RECTANGLE','DISK','ELLIPSE'):
    lamp.data.shape=shape;bpy.context.view_layer.update()
    deps=bpy.context.evaluated_depsgraph_get();cache.update_lights(deps)
    exported=area_lights(lamp,52.49)
    assert len(cache.lights)==len(exported)>0
    for actual,entity in zip(cache.lights,exported):
        parsed=PointLight.from_keys(entity.keyvalues);cone=spot_parameters(entity.keyvalues)
        assert np.allclose(actual[0],np.array(entity.origin)/52.49,atol=1e-6)
        assert np.allclose(actual[1],parsed.intensity)
        assert np.allclose(actual[3],(*vrad_light_direction(entity.angles,entity.keyvalues),cone[0]))
        assert np.allclose(actual[4][:3],cone[1:])
    before=np.sum([l[1] for l in cache.lights],axis=0)
    lamp.data.energy=200;bpy.context.view_layer.update();cache.update_lights(deps)
    after=np.sum([l[1] for l in cache.lights],axis=0)
    assert np.allclose(after,2*before,rtol=1e-6)
    lamp.data.energy=100
    rows.append({'shape':shape,'samples':len(exported),'total_intensity':before.tolist()})
lamp.data.energy=0;bpy.context.view_layer.update();cache.update_lights(deps)
assert not cache.lights
lamp.data.energy=100;lamp.hammerless.role='IGNORE';bpy.context.view_layer.update();cache.update_lights(deps)
assert not cache.lights
report={'ok':True,'shapes':rows,'scope':'CPU snapshot versus exporter for rotated mirrored area lights, linear power, zero energy and Ignore. GPU image and timing still require tests.'}
(ROOT.parents[1]/'outputs/l4d2-preview/area-snapshot-check.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
