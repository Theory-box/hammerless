"""GPU verification of nine-tap planar filter against independent 16-term CPU sum."""
from pathlib import Path
import sys,json,traceback
import bpy,gpu,numpy as np
from gpu_extras.batch import batch_for_shader
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender.previewfilter import GLSL
OUT=ROOT.parents[1]/'outputs/l4d2-preview/planar-filter-gpu.json'
def run():
    info=gpu.types.GPUShaderCreateInfo();info.vertex_in(0,'VEC2','position')
    info.push_constant('INT','phase');info.fragment_out(0,'VEC4','color')
    info.vertex_source('void main(){gl_Position=vec4(position,0,1);}')
    info.fragment_source('''vec3 filterLighting(vec2 q){
        return vec3(q.x>1.3?1.0:0.0,exp(-dot(q,q)*0.02),sin(q.x)*cos(q.y)*0.5+0.5);
    }'''+GLSL+'''void main(){vec2 q=gl_FragCoord.xy/7.0-4.3;
        color=vec4(regularPlanarFilter(q,phase),1); }''')
    shader=gpu.shader.create_from_info(info)
    batch=batch_for_shader(shader,'TRIS',{'position':[(-1,-1),(3,-1),(-1,3)]})
    off=gpu.types.GPUOffScreen(64,64,format='RGBA32F')
    try:
        with off.bind():
            gpu.state.viewport_set(0,0,64,64);gpu.state.depth_test_set('NONE');gpu.state.blend_set('NONE')
            def draw(phase):
                shader.bind();shader.uniform_int('phase',phase);batch.draw(shader)
                return np.asarray(gpu.state.active_framebuffer_get().read_color(0,0,64,64,4,0,'FLOAT').to_list())[...,:3]
            full=draw(-1);cycle=np.mean([draw(i) for i in range(9)],axis=0)
        y,x=np.mgrid[:64,:64];q=np.stack(((x+.5)/7-4.3,(y+.5)/7-4.3),axis=-1)
        base=np.floor(q);f=q-base;expected=np.zeros((64,64,3))
        for ix in (0,1):
            for iy in (0,1):
                weight=(f[...,0] if ix else 1-f[...,0])*(f[...,1] if iy else 1-f[...,1])/4
                for dx in (-.5,.5):
                    for dy in (-.5,.5):
                        v=base+[ix+dx,iy+dy]
                        value=np.stack((v[...,0]>1.3,np.exp(-np.sum(v*v,axis=-1)*.02),np.sin(v[...,0])*np.cos(v[...,1])*.5+.5),axis=-1)
                        expected+=value*weight[...,None]
        error=float(np.max(abs(full-expected)));cycle_error=float(np.max(abs(cycle-full)))
        assert error<2e-6,error
        assert cycle_error<2e-6,cycle_error
        return {'ok':True,'pixels':4096,'channels':['step discontinuity','smooth decay','oscillating field'],
            'max_cpu16_gpu9_error':error,'max_nine_phase_error':cycle_error,
            'scope':'Algebra and GPU execution only. Not integrated; no BVH performance or arbitrary surface parity claim.'}
    finally:off.free()
try:result=run()
except Exception:result={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
