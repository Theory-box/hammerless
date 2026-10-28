import sys
from pathlib import Path
import bpy
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hammerless.blender.collisiontool import _world_triangles, _transfer_appearance
bpy.ops.mesh.primitive_cube_add()
o=bpy.context.object
o.scale=(-2,3,1); o.rotation_euler=(0.2,0.4,0.7); o.location=(3,4,5)
for n in ('A','B'): o.data.materials.append(bpy.data.materials.new(n))
for f in o.data.polygons:
 f.material_index=f.index%2
 for j,l in enumerate(f.loop_indices): o.data.uv_layers.active.data[l].uv=((j%2)*0.7+f.index, (j//2)*1.3)
bpy.context.view_layer.update()
co,tri,mi,mats,uv=_world_triangles(o,bpy.context.evaluated_depsgraph_get())
m=bpy.data.meshes.new('result'); m.from_pydata(co.tolist(),[],tri.tolist()); m.materials.append(bpy.data.materials.new('nodraw'))
_transfer_appearance(m,co,tri,mi,mats,uv)
for f in m.polygons:
 assert m.materials[f.material_index]==mats[mi[f.index]], (f.index,f.material_index)
 assert m.attributes['hl_tex_uv'].data[f.index].value==1
 actual=np.array([m.uv_layers.active.data[l].uv[:] for l in f.loop_indices])
 assert np.allclose(actual,uv[f.index],atol=1e-5),(actual,uv[f.index])
# Cut face inside the solid must remain nodraw.
n=bpy.data.meshes.new('cut'); n.from_pydata([(3,4,5),(3.1,4,5),(3,4.1,5)],[],[(0,1,2)]); n.materials.append(m.materials[0])
_transfer_appearance(n,co,tri,mi,mats,uv)
assert n.polygons[0].material_index==0
print('PASS: mirrored transformed multi-material UV transfer and internal nodraw')
