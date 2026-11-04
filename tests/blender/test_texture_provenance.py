"""Imported model provenance survives display-name edits and cache reuse."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy
import hammerless
hammerless.register()
from hammerless.blender import preview
from hammerless.core import mdl
from types import SimpleNamespace
import numpy as np
preview.base_texture=lambda *args:None
path='models/test/very_long_shared_material_path'
material=preview._model_material(path,None,None)
assert material['hl_source_material']==path
# A cache hit repairs provenance removed from an older preview material.
del material['hl_source_material']
assert preview._model_material(path,None,None)['hl_source_material']==path
model=SimpleNamespace(triangles=[[0,1,2]],positions=np.array([[0,0,0],[1,0,0],[0,1,0]],dtype=float),uvs=np.array([[0,0],[1,0],[0,1]],dtype=float),triangle_materials=[0],materials=['test'])
mdl.load_model=lambda *args:model
mdl.find_material=lambda *args:path
mesh=preview.model_mesh('models/test/sample.mdl',None,None,1)
assert list(mesh['hl_source_materials'])==[path]
assert mesh['hl_model']=='models/test/sample.mdl'
material.name='Completely renamed material'
mesh.name='Completely renamed mesh'
assert material.copy()['hl_source_material']==path
assert list(mesh.copy()['hl_source_materials'])==[path]
hammerless.unregister()
print('TEXTURE_PROVENANCE_OK')
