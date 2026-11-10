"""Isolated fixed-BSP persistent-GI surface viewer/check; LIGHTING ONLY.

Run in a disposable Blender window, not the user's working session:
 blender --factory-startup --python this.py -- --reference ref.bsp --blob replay.0.bin --out DIR
Optional --edited-blob swaps only an explicitly supplied replacement atlas.
Optional --interactive leaves the draw handler active after checks for orbiting.
No compiler/solver, installed-addon changes, preferences writes, or scene saves.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import sys
import traceback
import numpy as np
import bpy
import gpu
from mathutils import Matrix,Vector
from gpu_extras.batch import batch_for_shader
import struct
import zlib

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests/compile'))
from inspect_gi_replay import replay,override_lump
from hammerless.core.lightmap import read_lightmaps
from hammerless.blender.lightview import _shader

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference',type=Path,required=True)
parser.add_argument('--blob',type=Path,required=True)
parser.add_argument('--edited-blob',type=Path)
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--interactive',action='store_true')
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
args.out.mkdir(parents=True,exist_ok=True)
reference=args.reference.read_bytes()

def decode(path):
    faces,lighting=replay(path.read_bytes())
    data=read_lightmaps(override_lump(override_lump(reference,58,faces),53,lighting),0)
    if not data.faces or not len(data.positions):raise ValueError('No lightmapped surface geometry')
    return data

def digest(array):return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()

def write_png(path,rgb):
    """Small dependency-free RGB8 artifact writer; no Blender image state."""
    height,width,_=rgb.shape
    def chunk(kind,payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
    rows=b''.join(b'\x00'+np.ascontiguousarray(row).tobytes() for row in rgb)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',width,height,8,2,0,0,0))+
                     chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b''))

class SurfaceViewer:
    def __init__(self,data):
        self.data=data;self.uploads=0;self.draws=0
        self.shader=_shader()
        self.batch=batch_for_shader(self.shader,'TRIS',{'pos':data.positions,'uv':data.uvs})
        self.replace(data)
    def replace(self,data):
        if not np.array_equal(data.positions,self.data.positions) or not np.array_equal(data.uvs,self.data.uvs) or data.atlas.shape!=self.data.atlas.shape:
            raise ValueError('Replacement must preserve fixed-BSP geometry and atlas mapping')
        atlas=np.maximum(data.atlas,0).astype(np.float32)
        height,width=atlas.shape[:2]
        self.texture=gpu.types.GPUTexture((width,height),format='RGBA32F',data=gpu.types.Buffer('FLOAT',atlas.size,atlas.reshape(-1)))
        self.data=data;self.atlas_hash=digest(atlas);self.uploads+=1
    def gpu_hash(self):
        buffer=self.texture.read();buffer.dimensions=(self.data.atlas.size,)
        return digest(np.frombuffer(buffer,dtype=np.float32,count=self.data.atlas.size))
    def draw(self,matrix):
        depth=gpu.state.depth_test_get();mask=gpu.state.depth_mask_get();blend=gpu.state.blend_get()
        try:
            gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True);gpu.state.blend_set('NONE')
            self.shader.bind()
            self.shader.uniform_float('ModelViewProjectionMatrix',matrix)
            self.shader.uniform_float('exposure',1.)
            self.shader.uniform_float('bias',0.)
            self.shader.uniform_sampler('image',self.texture)
            self.batch.draw(self.shader);self.draws+=1
        finally:
            gpu.state.depth_test_set(depth);gpu.state.depth_mask_set(mask);gpu.state.blend_set(blend)

viewer=None
handler=None

def on_draw():
    if viewer is not None:
        viewer.draw(bpy.context.region_data.perspective_matrix)

def projection(aspect,near,far):
    f=1/math.tan(math.radians(65)/2)
    return Matrix(((f/aspect,0,0,0),(0,f,0,0),(0,0,-(far+near)/(far-near),-2*far*near/(far-near)),(0,0,-1,0)))

def capture(matrix,name):
    off=gpu.types.GPUOffScreen(720,540,format='RGBA32F')
    viewport=gpu.state.viewport_get();mask=gpu.state.depth_mask_get()
    try:
        with off.bind():
            gpu.state.viewport_set(0,0,720,540);gpu.state.depth_mask_set(True)
            fb=gpu.state.active_framebuffer_get();fb.clear(color=(.025,.025,.025,1),depth=1)
            viewer.draw(matrix)
            color=fb.read_color(0,0,720,540,4,0,'FLOAT');color.dimensions=(720*540*4,)
            pixels=np.frombuffer(color,dtype=np.float32).reshape(540,720,4).copy()
            raw_depth=fb.read_depth(0,0,720,540);raw_depth.dimensions=(720*540,)
            depth=np.frombuffer(raw_depth,dtype=np.float32).reshape(540,720).copy()
        write_png(args.out/(name+'.png'),np.uint8(np.clip(pixels[::-1,:,:3],0,1)*255+.5))
        assert np.count_nonzero(depth<1)>100,'No surface depth rendered'
        assert float(pixels[:,:,:3].max())>.05,'Lighting image is empty/dark'
        return {'image':name+'.png','image_hash':digest(pixels),'depth_hash':digest(depth),
                'covered_pixels':int(np.count_nonzero(depth<1)),'atlas_hash':viewer.gpu_hash(),
                'uploads':viewer.uploads},pixels
    finally:
        gpu.state.depth_mask_set(mask);gpu.state.viewport_set(*viewport);off.free()

def run():
    global viewer,handler
    try:
        data=decode(args.blob);viewer=SurfaceViewer(data)
        low=data.positions.min(axis=0);high=data.positions.max(axis=0)
        center=Vector((low+high)*.5);span=float(np.max(high-low))
        directions=[Vector((1,.35,-.25)).normalized(),Vector((-.4,1,-.35)).normalized()]
        matrices=[]
        for direction in directions:
            camera=direction.to_track_quat('-Z','Y').to_matrix().to_4x4();camera.translation=center
            matrices.append(projection(720/540,max(.01,span*.001),span*10)@camera.inverted())
        initial_texture=viewer.texture;initial_hash=viewer.gpu_hash()
        first,pixels1=capture(matrices[0],'initial-view-a')
        second,pixels2=capture(matrices[1],'initial-view-b')
        assert first['image_hash']!=second['image_hash'],'Camera move did not change displayed geometry'
        assert first['depth_hash']!=second['depth_hash'],'Camera move reused old screen depth'
        assert viewer.texture is initial_texture and viewer.uploads==1
        assert first['atlas_hash']==second['atlas_hash']==initial_hash==viewer.atlas_hash
        # Pause means simply drawing the retained surface cache; no invalidation.
        paused,_=capture(matrices[0],'paused-view-a')
        assert paused['image_hash']==first['image_hash'] and viewer.uploads==1
        records=[first,second,paused]
        if args.edited_blob:
            viewer.replace(decode(args.edited_blob))
            assert viewer.atlas_hash!=initial_hash,'Edited artifact did not change the lightmap'
            edited,pixels3=capture(matrices[0],'edited-view-a')
            edited_camera,_=capture(matrices[1],'edited-view-b')
            assert edited['image_hash']!=first['image_hash'],'Changed lightmap did not change display'
            assert edited['atlas_hash']==edited_camera['atlas_hash']==viewer.atlas_hash
            assert viewer.uploads==2
            records.extend((edited,edited_camera))
        for obj in list(bpy.context.scene.objects):bpy.data.objects.remove(obj,do_unlink=True)
        for area in bpy.context.screen.areas:
            if area.type=='VIEW_3D':
                space=area.spaces.active;space.overlay.show_overlays=False
                space.region_3d.view_rotation=directions[0].to_track_quat('-Z','Y')
                space.region_3d.view_distance=span*.25
                space.region_3d.view_location=center+directions[0]*span*.25
                space.region_3d.view_perspective='PERSP';space.clip_end=span*20
                area.tag_redraw()
        handler=bpy.types.SpaceView3D.draw_handler_add(on_draw,(),'WINDOW','POST_VIEW')
        report={'ok':True,'scope':'Fixed compiled geometry, light-only surface display; no materials/props or editable geometry. Camera changes redraw the same lighting atlas.',
                'reference':str(args.reference),'artifact':str(args.blob),'faces':data.faces,'luxels':data.luxels,
                'atlas_size':list(data.atlas.shape),'uploads':viewer.uploads,'captures':records}
    except Exception:
        report={'ok':False,'traceback':traceback.format_exc()}
    (args.out/'viewport-check.json').write_text(json.dumps(report,indent=2))
    if not args.interactive or not report['ok']:
        bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
    return None

if __name__=='__main__':bpy.app.timers.register(run,first_interval=.3)
