"""Visible admission parity and edit/deletion regression; no scene saves."""
from pathlib import Path
import sys,json,traceback
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,vmfimport
from hammerless.blender.l4d2preview import SceneCache
from hammerless.blender.previewlivecache import LiveSurfaceCache
from hammerless.core.geometry import box_brush
from hammerless.core.vmf import VMFWriter,Block
OUT=ROOT.parents[1]/'outputs/l4d2-preview/visible-grid-parity.json'


class White:
    unlit={};path_unlit={};materials={};warnings=[]
    def resolve(self,material):return None,(512,512)
    def resolve_path(self,path):return None,(512,512)


def run():
    props.register();scene=bpy.context.scene
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    writer=VMFWriter();world=Block('world')
    world.kv('id',writer.new_id());world.kv('classname','worldspawn')
    for x in (0,4096,8192):
        world.add(writer.solid(box_brush((x,0,0),(x+128,128,128),'test/white')))
    lines=[];world.write(lines);vmfimport.import_text(bpy.context,''.join(lines),'visible-grid.vmf')
    objects=sorted((o for o in scene.objects if o.get(vmfimport.KIND)=='solid'),key=lambda o:o.matrix_world.translation.x)
    assert len(objects)==3
    cache=SceneCache();cache.surface_materials=White();cache.unit_scale=scene.hammerless.units_per_meter
    def scene_update():
        bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get();cache.update(deps);return deps
    deps=scene_update();live=LiveSurfaceCache();live.stage(bpy.context,deps,cache,0)
    by_key={obj.as_pointer():obj for obj in objects}
    objects=[by_key[key] for key in live.candidate_keys]
    assert live.layouts.builds==0 and live.positions is None and not live.faces
    eager=LiveSurfaceCache();eager.update(bpy.context,deps,cache,0)
    original_geometry={k:g.positions.copy() for k,g in cache.geometry.items()}
    original_instances=list(cache.instances)
    units=cache.unit_scale
    def view(obj):
        bounds=live.candidate_bounds[live.candidate_keys.index(obj.as_pointer())]
        center=bounds[:,:3].mean(axis=0)
        matrix=np.eye(4);matrix[:3,:3]*=units/256;matrix[:3,3]=-center*units/256
        return matrix
    generations={}
    for obj in objects:
        matrix=view(obj);key=obj.as_pointer()
        assert live.has_pending(matrix)
        assert live.advance(bpy.context,deps,cache,0,matrix)
        assert not live.has_pending(matrix)
        assert set(live.object_data)==set(generations)|{key}
        for old,generation in generations.items():assert live.object_data[old]['generation']==generation
        entry=live.object_data[key];reference=eager.object_data[key]
        for name in ('samples','normals','bounds','covered'):
            np.testing.assert_array_equal(entry[name],reference[name])
        assert [(c.w,c.h,c.material_path) for c in entry['charts']]==[(c.w,c.h,c.material_path) for c in reference['charts']]
        generations[key]=entry['generation']
    np.testing.assert_array_equal(live.positions,eager.positions)
    np.testing.assert_array_equal(live.normals,eager.normals)
    assert live.charts==eager.charts
    # An earlier depsgraph object discovered later cannot evict an existing
    # resident when the fixed total sample budget is already full.
    capped=LiveSurfaceCache();capped.stage(bpy.context,deps,cache,0)
    last=objects[-1].as_pointer();first=objects[0].as_pointer()
    capped.SAMPLE_BUDGET=eager.object_data[last]['size']
    capped.advance(bpy.context,deps,cache,0,view(objects[-1]))
    retained=capped.object_data[last]
    capped.advance(bpy.context,deps,cache,0,view(objects[0]))
    assert capped.object_data[last] is retained and retained['samples'] is not None
    assert capped.object_data[first]['samples'] is None
    assert not capped.has_pending(view(objects[0]))
    assert cache.instances==original_instances
    for key,positions in original_geometry.items():np.testing.assert_array_equal(cache.geometry[key].positions,positions)
    # Previously admitted geometry refreshes during stage, before any advance.
    edited=objects[0];key=edited.as_pointer();old=live.object_data[key]
    edited.location.z+=32/units;deps=scene_update();live.stage(bpy.context,deps,cache,0)
    assert live.object_data[key]['generation']!=old['generation']
    assert live.object_data[key]['geometry'] is cache.geometry[key]
    assert live.display is None and live.cache is None
    fresh=LiveSurfaceCache();fresh.update(bpy.context,deps,cache,0)
    np.testing.assert_array_equal(live.positions,fresh.positions)
    removed=objects[1].as_pointer();bpy.data.objects.remove(objects[1],do_unlink=True)
    deps=scene_update();live.stage(bpy.context,deps,cache,0)
    assert removed not in live.requested_keys and removed not in live.object_data
    assert removed not in live.keys and removed not in live.remaining and removed not in live.geometry_refs
    fresh=LiveSurfaceCache();fresh.update(bpy.context,deps,cache,0)
    np.testing.assert_array_equal(live.positions,fresh.positions)
    return {'ok':True,'checks':['empty first stage','visible-only admission','unchanged generations retained','eager sample/normal/chart parity','complete scene geometry retained','immediate edit refresh','deleted object removed']}


try:result=run()
except Exception:result={'ok':False,'traceback':traceback.format_exc()}
OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
