"""GPU history averaging, reset, exposure and depth preservation."""
from pathlib import Path
import sys,json,traceback
import bpy,gpu,numpy as np
from gpu_extras.batch import batch_for_shader
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender.previewhistory import FrameHistory
OUT=ROOT.parents[1]/'outputs/l4d2-preview/history-check.json'


def run():
    history=FrameHistory();calls=[]
    info=gpu.types.GPUShaderCreateInfo();info.vertex_in(0,'VEC2','position')
    info.vertex_source('void main(){gl_Position=vec4(position,-.4,1);}')
    info.fragment_source('void main(){}')
    depth_shader=gpu.shader.create_from_info(info)
    depth_batch=batch_for_shader(depth_shader,'TRIS',{'position':[(-1,-1),(3,-1),(-1,3)]})
    def draw_depth():depth_shader.bind();depth_batch.draw(depth_shader)
    def sample(frame):
        calls.append(frame)
        c=.2 if frame%2==0 else .6
        gpu.state.active_framebuffer_get().clear(color=(c,c,c,1),depth=.3)
    off=gpu.types.GPUOffScreen(8,8,format='RGBA32F')
    try:
        with off.bind():
            gpu.state.viewport_set(0,0,8,8)
            for _ in range(4):history.render('scene1',sample,draw_depth,max_frames=2)
            assert calls==[0,1],calls
            fb=gpu.state.active_framebuffer_get()
            pixel=np.array(fb.read_color(4,4,1,1,4,0,'FLOAT').to_list()[0][0])
            expected=1.055*.4**(1/2.4)-.055
            assert np.allclose(pixel[:3],expected,atol=1e-5),pixel
            depth=fb.read_depth(4,4,1,1).to_list()[0][0]
            assert abs(depth-.3)<1e-5,depth
            history.render('scene1',sample,draw_depth,exposure=1,max_frames=2)
            assert calls==[0,1],'Exposure retraced the scene'
            pixel=fb.read_color(4,4,1,1,4,0,'FLOAT').to_list()[0][0]
            assert abs(pixel[0]-(1.055*.8**(1/2.4)-.055))<1e-5,pixel
            history.render('scene2',sample,draw_depth,max_frames=2)
            assert calls==[0,1,0] and history.frames==1
            pixel=fb.read_color(4,4,1,1,4,0,'FLOAT').to_list()[0][0]
            assert abs(pixel[0]-(1.055*.2**(1/2.4)-.055))<1e-5,pixel
            gpu.state.viewport_set(0,0,4,4)
            history.render('scene2',sample,draw_depth,max_frames=2)
            assert history.frames==1 and calls[-1]==0
        return {'ok':True,'checks':['linear average before display conversion','frame limit','depth preserved','exposure without retracing','scene reset','resize reset']}
    finally:off.free()

try:result=run()
except Exception:result={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
