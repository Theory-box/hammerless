"""Public material-mod operators: rename-safe mapping, byte colors, rebuild removal."""
import bpy,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import texturemods as tm
from hammerless.core.vpk import VPK
from hammerless.blender import ops
import numpy as np
with tempfile.TemporaryDirectory() as temp:
    im=bpy.data.images.new('gray',width=4,height=4,alpha=True)
    im.pixels[:]=[128/255,128/255,128/255,1]*16
    im.filepath_raw=str(Path(temp)/'gray.png');im.file_format='PNG';im.save()
    loaded=bpy.data.images.load(im.filepath_raw)
    assert np.all(tm.pixels(loaded,color=True)[:,:,:3]==128)
    mat=bpy.data.materials.new('rename me');mat.use_nodes=True
    image=mat.node_tree.nodes.new('ShaderNodeTexImage');image.image=loaded
    shader=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    mat.node_tree.links.new(image.outputs['Color'],shader.inputs['Base Color'])
    mat['hl_source_material']='models/test/original';mat.name='Unrelated user name'
    bpy.ops.mesh.primitive_cube_add();o=bpy.context.object;o.name='Renamed object';o.data.materials.append(mat)
    assert bpy.ops.hammerless.material_mod_add()=={'FINISHED'}
    s=bpy.context.scene.hl_material_mods;assert len(s.entries)==1 and s.entries[0].target=='models/test/original'
    class Content:
        def read(self,path):
            if path=='materials/models/test/original.vmt':return b'VertexLitGeneric { "$basetexture" "old" }'
            if path=='materials/old.vtf':
                from hammerless.core.textures import write_vtf
                p=str(Path(temp)/'old.vtf');write_vtf(p,np.full((4,4,4),255,np.uint8));return Path(p).read_bytes()
            return None
    ops.game_root=lambda context:temp;ops.game_content=lambda root:Content()
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(temp)/'test.blend'))
    tm.build(bpy.context,False)
    p=Path(temp)/'hammerless_mods'/('hammerless_materials_'+s.uid+'.vpk')
    assert 'materials/models/test/original.vmt' in VPK(str(p)).entries
    assert bpy.ops.hammerless.material_mod_remove()=={'FINISHED'}
    tm.build(bpy.context,False)
    assert not any(k.startswith('materials/') for k in VPK(str(p)).entries)
    hammerless.unregister();hammerless.register();hammerless.unregister()
print('TEXTURE_MOD_BLENDER_OK')
