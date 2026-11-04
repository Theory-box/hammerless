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
    p=Path(temp)/'hammerless_mods'/tm.package_name(s.uid)
    assert 'materials/models/test/original.vmt' in VPK(str(p)).entries
    import shutil
    from unittest.mock import patch
    addons=Path(temp)/'left4dead2'/'addons';addons.mkdir(parents=True)
    legacy=addons/('hammerless_materials_'+s.uid+'.vpk')
    shutil.copyfile(p,legacy)
    with patch.object(tm.os,'replace',side_effect=OSError('test failure')):
        try:tm.install_package(p,temp,s.uid)
        except OSError:pass
        else:raise AssertionError('Expected install failure')
    assert legacy.exists(),'Failed install removed the previous addon'
    tm.install_package(p,temp,s.uid)
    assert not legacy.exists() and (addons/tm.package_name(s.uid)).exists()
    assert tm.refresh_installed(bpy.context)==1
    rows=bpy.context.window_manager.hl_installed_material_mods
    assert rows[0].source_blend==bpy.data.filepath
    other_uid='1'*32
    tm.texturemods.build_package(Content(),{'models/test/original':{'basecolor':np.full((4,4,4),255,np.uint8)}},
        addons/tm.package_name(other_uid),other_uid,title='Other blend',source_blend='C:/other.blend')
    try:tm.build(bpy.context,True)
    except ValueError as e:assert 'already replaced' in str(e)
    else:raise AssertionError('Conflicting mod install was accepted')
    other_scene=bpy.data.scenes.new('Different blend context')
    with bpy.context.temp_override(scene=other_scene):
        assert bpy.ops.hammerless.material_mod_refresh()=={'FINISHED'}
        assert len(rows)==2
        assert bpy.ops.hammerless.material_mod_uninstall(filename=tm.package_name(other_uid))=={'FINISHED'}
        assert len(rows)==1
    assert not (addons/tm.package_name(other_uid)).exists()
    assert len(s.entries)==1,'Global uninstall changed authored scene entries'
    try:tm.install_package(p,str(Path(temp)/('x'*128)),s.uid)
    except ValueError as e:assert 'too long' in str(e)
    else:raise AssertionError('Oversize engine path was accepted')
    assert bpy.ops.hammerless.material_mod_remove()=={'FINISHED'}
    tm.build(bpy.context,False)
    assert not any(k.startswith('materials/') for k in VPK(str(p)).entries)
    hammerless.unregister();hammerless.register();hammerless.unregister()
print('TEXTURE_MOD_BLENDER_OK')

