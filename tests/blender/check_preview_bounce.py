"""First-bounce GPU transport checks with independently controlled plane reflectivity."""
from pathlib import Path
import sys,json,traceback
import bpy,gpu,numpy as np
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender.l4d2preview import Geometry,SceneCache,diagnostic_shader,draw_geometry
from hammerless.blender.previewtrace import TraceCache
from hammerless.blender.previewmaterials import BounceMaterial
OUT=ROOT.parents[1]/'outputs/l4d2-preview/first-bounce.json'


def run():
    pos=np.array([[-10,-10,0],[10,-10,0],[10,10,0],[-10,-10,0],[10,10,0],[-10,10,0]],np.float32)
    floor=Geometry(pos,np.tile([0,0,1],(6,1)).astype(np.float32))
    ceiling=Geometry(pos.reshape(2,3,3)[:,::-1].reshape(6,3).copy(),np.tile([0,0,-1],(6,1)).astype(np.float32))
    cache=SceneCache();cache.unit_scale=64
    cache.geometry={1:floor,2:ceiling}
    cache.instances=[(1,Matrix.Identity(4)),(2,Matrix.Translation((0,0,4)))]
    cache.lights=[((0,0,2),(2000000.,)*3,(0,0,1.))]
    cache.trace=TraceCache()
    class Resolver:
        color=(.6,0.,0.)
        def resolve(self,mat):return BounceMaterial(self.color,True,True,'controlled reference')
    resolver=Resolver();cache.trace.update_bounce_materials(cache,resolver)
    shader=diagnostic_shader(lit=True,shadows=True,bounce=True)
    off=gpu.types.GPUOffScreen(1,1,format='RGBA32F')
    def draw(samples,sampled_sky=False):
        with off.bind():
            gpu.state.viewport_set(0,0,1,1)
            fb=gpu.state.active_framebuffer_get();fb.clear(color=(0,0,0,1),depth=1)
            draw_geometry(cache,shader,Matrix.Scale(.5,4),lit=True,shadows=True,bounce_samples=samples,sampled_bounce_sky=sampled_sky)
            return np.array(fb.read_color(0,0,1,1,4,0,'FLOAT').to_list()[0][0][:3])
    try:
        direct=draw(0);full=draw(128);indirect=full-direct
        assert indirect[0]>.01 and np.max(abs(indirect[1:]))<1e-6,(direct,full)
        resolver.color=(.3,0,0);cache.trace.update_bounce_materials(cache,resolver)
        half=draw(128)-draw(0)
        assert np.allclose(half,indirect*.5,atol=1e-5),(half,indirect)
        cache.lights=[((0,0,2),(4000000.,)*3,(0,0,1.))];cache.light_texture=None
        doubled=draw(128)-draw(0)
        assert np.allclose(doubled,half*2,atol=1e-5)
        cache.lights=[];cache.light_texture=None;cache.sky_color=(51.,)*3
        sky_full=draw(8192)-draw(0)
        sky_sampled=draw(8192,True)-draw(0)
        assert sky_full[0]>.001,sky_full
        assert np.allclose(sky_sampled,sky_full,rtol=.06,atol=.0005),(sky_sampled,sky_full)
        cache.sky_color=(0.,)*3
        cache.lights=[((0,0,2),(4000000.,)*3,(0,0,1.))];cache.light_texture=None
        # Flip only the hit surface: back faces block but must not return light.
        ceiling.positions=pos.copy();ceiling.normals=np.tile([0,0,1],(6,1)).astype(np.float32)
        ceiling.shadow_revision+=1
        cache.trace.update_bounce_materials(cache,resolver)
        assert np.allclose(draw(128),draw(0),atol=1e-6),'Back face reflected light'
        cache.instances=cache.instances[:1];cache.geometry={1:floor}
        cache.trace.update_bounce_materials(cache,resolver)
        assert np.allclose(draw(128),draw(0),atol=1e-6),'Empty hemisphere returned light'
        return {'ok':True,'direct':direct.tolist(),'first_bounce':indirect.tolist(),
            'sky_full':sky_full.tolist(),'sky_sampled':sky_sampled.tolist(),
            'checks':['red color transfer','reflectivity linearity','light intensity linearity','back-face rejection','misses return zero'],
            'scope':'Controlled first-bounce transport, 128 rays; not bake parity or realtime performance'}
    finally:off.free()

try:result=run()
except Exception:result={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
