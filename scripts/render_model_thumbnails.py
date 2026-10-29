"""Offline unlit model thumbnails. Run in background Blender; never opens a user map.
blender -b --factory-startup --python scripts/render_model_thumbnails.py -- --game ROOT [--limit 16]
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import time

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hammerless.core.vpk import GameContent
from hammerless.blender.preview import model_mesh

parser = argparse.ArgumentParser()
parser.add_argument('--game', required=True)
parser.add_argument('--limit', type=int, default=0)
parser.add_argument('--offset', type=int, default=0)
parser.add_argument('--out', default=str(ROOT/'hammerless/data/model_thumbnails'))
a = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
content = GameContent(a.game)
names = sorted(n for n in content.files if n.startswith('models/') and n.endswith('.mdl'))
indexfile = out/'index.json'
index = json.loads(indexfile.read_text()) if indexfile.exists() else {}
print('MODEL_COUNT', len(names), flush=True)
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
scene.render.engine = 'BLENDER_WORKBENCH'
scene.display.shading.light = 'FLAT'
scene.display.shading.color_type = 'TEXTURE'
scene.display.shading.show_backface_culling = False
scene.display.shading.show_shadows = False
scene.display.shading.show_cavity = False
scene.display.shading.show_specular_highlight = False
scene.display.shading.show_object_outline = False
scene.display.shading.background_type = 'WORLD'
scene.world.color = (0.055, 0.055, 0.055)
scene.view_settings.view_transform = 'Standard'
scene.render.resolution_x = scene.render.resolution_y = 192
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGB'
scene.display.render_aa = '8'
camdata = bpy.data.cameras.new('Thumbnail Camera'); camdata.type='ORTHO'
cam = bpy.data.objects.new('Thumbnail Camera',camdata); scene.collection.objects.link(cam); scene.camera=cam
start=time.monotonic()
selected=names[a.offset:a.offset+a.limit] if a.limit else names[a.offset:]
for no,path in enumerate(selected):
    image = hashlib.sha256(path.encode()).hexdigest()[:24]+'.png'
    if path in index and (out/image).exists(): continue
    record={'image':image}
    try:
        raw=content.read(path)
        record['static']=bool(struct.unpack_from('<I',raw,152)[0]&16)
        record['collision']=path[:-4]+'.phy' in content.files
        load_start=time.monotonic()
        mesh=model_mesh(path,content,str(Path(a.game)/'left4dead2'),1.0)
        if mesh is None: raise ValueError('No renderable mesh')
        record['load_seconds']=round(time.monotonic()-load_start,4)
        obj=bpy.data.objects.new('Thumbnail Model',mesh);scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        corners=[Vector(v) for v in obj.bound_box]
        center=sum(corners,Vector())/8
        direction=Vector((1,-1,0.65)).normalized()
        cam.rotation_euler=direction.to_track_quat('Z','Y').to_euler()
        rotation=cam.rotation_euler.to_matrix().transposed()
        projected=[rotation@(v-center) for v in corners]
        extent=max(max(v.x for v in projected)-min(v.x for v in projected),max(v.y for v in projected)-min(v.y for v in projected),1)
        cam.location=center+direction*max(extent*3,10)
        camdata.ortho_scale=extent*1.15
        camdata.clip_start=0.01;camdata.clip_end=max(extent*10,100)
        scene.render.filepath=str(out/image)
        render_start=time.monotonic()
        bpy.ops.render.render(write_still=True)
        record['render_seconds']=round(time.monotonic()-render_start,4)
        record['status']='ok'
    except Exception as ex:
        record['status']='error';record['error']=str(ex)
        print('MODEL_ERROR',path,str(ex),flush=True)
    finally:
        for obj in list(bpy.data.objects):
            if obj!=cam: bpy.data.objects.remove(obj,do_unlink=True)
        for data in (bpy.data.meshes,bpy.data.materials,bpy.data.images):
            for block in list(data):
                if block.users==0: data.remove(block)
    index[path]=record
    if (no+1)%25==0 or no+1==len(selected):
        tmp=indexfile.with_suffix('.tmp');tmp.write_text(json.dumps(index,sort_keys=True,separators=(',',':')));tmp.replace(indexfile)
        print('MODEL_PROGRESS',no+1,'/',len(selected),'seconds',round(time.monotonic()-start,1),flush=True)
print('MODEL_DONE',len(index),'seconds',round(time.monotonic()-start,1),flush=True)
