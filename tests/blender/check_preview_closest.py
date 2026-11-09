"""GPU nearest-hit regression: ordering, transforms, misses and barycentrics."""
from pathlib import Path
import sys,json,traceback
import bpy,gpu,numpy as np
from mathutils import Matrix,Vector
from gpu_extras.batch import batch_for_shader
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hammerless.blender.previewtrace import GLSL,TraceCache
from hammerless.blender.l4d2preview import Geometry,SceneCache
from hammerless.blender.previewmaterials import BounceMaterial
OUT=ROOT.parents[1]/'outputs/l4d2-preview/closest-hit.json'


def run():
    info=gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0,'VEC2','position');info.fragment_out(0,'VEC4','color')
    for i,name in enumerate(('traceNodes','traceTriangles','traceInstances','traceTop')):
        info.sampler(i,'FLOAT_2D',name)
    info.sampler(4,'FLOAT_2D','traceSurfaces')
    for name in ('traceInstanceCount','traceTopCount','outputKind'):info.push_constant('INT',name)
    info.push_constant('VEC3','origin');info.push_constant('VEC3','direction');info.push_constant('FLOAT','limit')
    info.vertex_source('void main(){gl_Position=vec4(position,0,1);}')
    info.fragment_source(GLSL+'''void main(){float t;vec3 n;vec2 uv;int tri,inst;
        bool hit=traceClosest(origin,direction,limit,t,n,uv,tri,inst);
        color=outputKind==0?vec4(n,t):vec4(uv,float(tri),hit?1.0:0.0);
        if(outputKind==2)color=hit?traceFetch(traceSurfaces,tri):vec4(0);}''')
    shader=gpu.shader.create_from_info(info)
    batch=batch_for_shader(shader,'TRIS',{'position':[(-1,-1),(3,-1),(-1,3)]})
    scene=SceneCache()
    # Local XY triangle, positive Z normal. Off-center hit makes barycentric tests useful.
    positions=np.array([[-2,-2,0],[2,-2,0],[0,2,0]],np.float32)
    scene.geometry={1:Geometry(positions,np.tile([0,0,1],(3,1)).astype(np.float32))}
    scene.instances=[(1,Matrix.Translation((0,0,8))),(1,Matrix.Translation((0,0,3)))]
    trace=TraceCache();trace.update(scene)
    class Resolver:
        value=(.2,.4,.6)
        def resolve(self,mat):return BounceMaterial(self.value,True,True,'test')
    resolver=Resolver();trace.update_bounce_materials(scene,resolver)
    off=gpu.types.GPUOffScreen(1,1,format='RGBA32F')
    def query(origin=(0,0,0),direction=(0,0,1),limit=100,kind=0):
        with off.bind():
            gpu.state.viewport_set(0,0,1,1)
            shader.bind();trace.bind(shader)
            shader.uniform_sampler('traceSurfaces',trace.surfaces)
            shader.uniform_float('origin',origin);shader.uniform_float('direction',direction)
            shader.uniform_float('limit',limit);shader.uniform_int('outputKind',kind)
            batch.draw(shader)
            return np.array(gpu.state.active_framebuffer_get().read_color(0,0,1,1,4,0,'FLOAT').to_list()[0][0])
    results=[]
    try:
        value=query();assert np.allclose(value,[0,0,1,3],atol=1e-5),value
        uv=query(kind=1);assert np.allclose(uv[:2],[.25,.5]) and uv[3]==1,uv
        assert query(limit=2,kind=1)[3]==0
        assert query(direction=(1,0,0),kind=1)[3]==0
        assert query(direction=(0,0,0),kind=1)[3]==0
        results.append('nearest, barycentric coordinates, bounded and parallel misses')
        assert np.allclose(query(kind=2),[.2,.4,.6,1])
        builds=trace.builds
        resolver.value=(.7,.1,.3);trace.update_bounce_materials(scene,resolver)
        assert np.allclose(query(kind=2),[.7,.1,.3,1]) and trace.builds==builds
        scene.geometry[1].use_uv=True;trace.update_bounce_materials(scene,resolver)
        assert np.allclose(query(kind=2),0),'Model contributed bounce radiance'
        scene.geometry[1].use_uv=False;trace.update_bounce_materials(scene,resolver)
        results.append('hit reflectivity, material updates preserve BVH, models block without bouncing')
        builds=trace.builds
        transform=Matrix.Translation((0,0,5))@Matrix.Rotation(.4,4,'Y')@Matrix.Diagonal((-2.,3.,.5,1.))
        scene.instances=[(1,transform)];trace.update(scene)
        target=transform@Vector((0,0,0))
        expected_normal=transform.to_3x3().inverted().transposed()@Vector((0,0,1))
        expected_normal.normalize()
        origin=target+expected_normal*7
        value=query(tuple(origin),tuple(-expected_normal))
        assert np.allclose(value[:3],expected_normal,atol=1e-5) and abs(value[3]-7)<1e-5,value
        assert trace.builds==builds,'Transform rebuilt triangles'
        results.append('rotated, mirrored, nonuniform scale preserves normal and world distance')
        scene.instances=[];trace.update(scene)
        assert query(kind=1)[3]==0
        layers=np.concatenate([positions+np.array([0,0,32-i],np.float32) for i in range(32)])
        geom=Geometry(layers,np.tile([0,0,1],(96,1)).astype(np.float32))
        geom.material_ids=np.arange(32);geom.materials=tuple(range(32))
        geom.shadow_triangle_ids=np.arange(1,32,2)
        geom.shadow_positions=layers.reshape(-1,3,3)[geom.shadow_triangle_ids].reshape(-1,3)
        scene.geometry={2:geom};scene.instances=[(2,Matrix.Identity(4))]
        class IndexedResolver:
            def resolve(self,mat):return BounceMaterial((mat/100,0,0),True,True,'indexed')
        trace.update_bounce_materials(scene,IndexedResolver())
        assert np.allclose(query(kind=2),[.31,0,0,1]),query(kind=2)
        assert abs(query()[3]-1)<1e-5
        results.append('filtered and BVH-reordered triangles retain their original material')
        return {'ok':True,'checks':results+['deleted instances miss'],'gpu':gpu.platform.renderer_get()}
    finally:off.free()

try:result=run()
except Exception:result={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
