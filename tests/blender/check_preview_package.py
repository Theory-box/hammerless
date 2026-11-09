"""Register the exact packaged addon, without changing user preferences."""
from pathlib import Path
import json,sys,zipfile,traceback,hashlib
import bpy
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT.parents[1]/'outputs/l4d2-preview'
def run():
 archive_path=OUT/'hammerless-l4d2-preview-experimental.zip'
 target=OUT/('package-smoke-'+hashlib.sha256(archive_path.read_bytes()).hexdigest()[:12])
 with zipfile.ZipFile(archive_path) as archive:
  archive.extractall(target)
 sys.path.insert(0,str(target))
 import hammerless
 assert Path(hammerless.__file__).resolve().is_relative_to(target.resolve())
 hammerless.register()
 from hammerless.blender.l4d2preview import HAMMERLESS_L4D2Preview
 bpy.context.scene.render.engine=HAMMERLESS_L4D2Preview.bl_idname
 assert bpy.context.scene.hl_preview_refine_frames==72
 assert bpy.context.scene.hl_preview_bounce_samples==0
 assert bpy.context.scene.hl_preview_surface_cache==False
 from hammerless.blender.previewlivecache import LiveSurfaceCache
 from hammerless.blender.previewlightcache import SurfaceLightCache
 assert 'visible_groups' in SurfaceLightCache.refine.__code__.co_varnames
 bpy.context.scene.hl_preview_surface_cache=True
 assert bpy.context.scene.hl_preview_surface_cache
 bpy.context.scene.render.engine='BLENDER_EEVEE_NEXT'
 hammerless.unregister()
 return {'ok':True,'imported_from':str(hammerless.__file__),'checks':['packaged registration','engine selection','defaults','unregistration']}
try:r=run()
except Exception:r={'ok':False,'traceback':traceback.format_exc()}
(OUT/'package-smoke.json').write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
