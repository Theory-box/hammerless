"""Read-only preview reflectivity agrees with the actual exported VTF header."""
from pathlib import Path
import sys,json,struct,tempfile
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props
from hammerless.blender.previewmaterials import BounceMaterials
from hammerless.blender.extract import image_to_rgba8
from hammerless.core.textures import write_vtf

props.register()
image=bpy.data.images.new('Bounce test',2,2,float_buffer=True)
image.pixels.foreach_set([.1,.2,.3,1, .9,.2,.1,1, .1,.8,.3,1, .4,.2,.7,1])
mat=bpy.data.materials.new('Custom test');mat.use_nodes=True
tex=mat.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image
bsdf=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
mat.node_tree.links.new(tex.outputs['Color'],bsdf.inputs['Base Color'])
resolver=BounceMaterials(None,None,'default')
value=resolver.resolve(mat)
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp)/'test.vtf';write_vtf(str(path),image_to_rgba8(image))
    data=path.read_bytes();header=struct.unpack_from('<3f',data,32)
assert np.allclose(value.reflectivity,header,rtol=0,atol=1e-7),(value,header)

class Content:
    def __init__(self):self.reads=0
    def read(self,path):
        self.reads+=1
        return {'materials/wall.vmt':b'LightmappedGeneric { "$basetexture" "test" }',
                'materials/unlit.vmt':b'UnlitGeneric { "$basetexture" "test" }',
                'materials/patch.vmt':b'patch { "include" "wall.vmt" }',
                'materials/test.vtf':data}.get(path)
content=Content();resolver=BounceMaterials(content,None,'wall')
assert np.allclose(resolver.source('wall').reflectivity,header)
assert resolver.source('wall').receives_light
assert not resolver.source('unlit').receives_light
assert resolver.source('patch').reflectivity==resolver.source('wall').reflectivity
assert resolver.resolve(None)==resolver.source('wall')
plain=bpy.data.materials.new('No texture')
plain.diffuse_color=(1,0,0,1)
assert resolver.resolve(plain)==resolver.source('wall'),'Export default must override Blender color'
reads=content.reads;resolver.source('wall');assert content.reads==reads,'Cache reread game content'
assert not resolver.source('missing').verified and resolver.warnings
result={'ok':True,'reflectivity':header,'checks':['custom VTF header parity','game VTF header','patch material','unlit exclusion','export default','cached reads','missing material warning']}
out=ROOT.parents[1]/'outputs/l4d2-preview/bounce-materials.json';out.write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)

from hammerless.blender.previewmaterials import SurfaceMaterials
surfaces=SurfaceMaterials(content,None,'wall')
default_image, dimensions=surfaces.resolve(None)
assert default_image is not None and dimensions==(2,2),(default_image,dimensions)
assert surfaces.resolve(plain)[0] is default_image, 'Plain Blender color overrode exported default'
assert surfaces.resolve(mat)[0] is image, 'Custom image lost'
mat.hammerless.source_material='wall'
surfaces=SurfaceMaterials(content,None,'wall')
assert surfaces.resolve(mat)[0] is default_image, 'Blender image overrode explicit Source material'
reads=content.reads
surfaces.resolve(mat)
assert content.reads==reads, 'Surface snapshot reread content'
assert not surfaces.warnings,surfaces.warnings
print('PASS: exported surface texture choice, default fallback, custom image, Source precedence, cache')

unlit=bpy.data.materials.new('Unlit source');unlit.hammerless.source_material='unlit'
surfaces.resolve(unlit)
assert surfaces.unlit[unlit.as_pointer()]
assert not surfaces.unlit[mat.as_pointer()]
print('PASS: Source UnlitGeneric surface classification')

# Renderer snapshots must not inherit a texture from another game root, or an
# older byte stream at the same path. Existing material-preview images stay intact.
from hammerless.blender.preview import _image_for_texture
from unittest.mock import patch
with tempfile.TemporaryDirectory() as tmp:
    replacement=Path(tmp)/'replacement.vtf'
    write_vtf(str(replacement),np.full((2,2,4),[0,255,0,255],dtype=np.uint8))
    changed=replacement.read_bytes()
with patch('hammerless.blender.preview.read_texture_bytes',return_value=data):
    a,_,_=_image_for_texture('test',content,None,cache_scope='test-root-a',verify_content=True)
    b,_,_=_image_for_texture('test',content,None,cache_scope='test-root-b',verify_content=True)
    assert a is not b, 'Different roots shared an image'
    old_digest=a['hl_content_digest']
    again,_,_=_image_for_texture('test',content,None,cache_scope='test-root-a',verify_content=True)
    assert again is a
with patch('hammerless.blender.preview.read_texture_bytes',return_value=changed):
    updated,_,_=_image_for_texture('test',content,None,cache_scope='test-root-a',verify_content=True)
    assert updated is a and a['hl_content_digest']!=old_digest
    assert b['hl_content_digest']==old_digest, 'Reload changed another root'
    assert np.allclose(np.asarray(a.pixels[:]).reshape(-1,4)[:,:3],[0,1,0],atol=.001)
with patch('hammerless.blender.preview.read_texture_bytes',return_value=None):
    missing,_,_=_image_for_texture('test',content,None,cache_scope='test-root-a',verify_content=True)
    assert missing is None, 'Missing source silently reused stale image'
print('PASS: source-root isolation, same-path reload, missing-source rejection')

legacy=bpy.data.materials.new('HL_mdl_wall')
legacy_resolver=SurfaceMaterials(content,None,'wall')
assert legacy_resolver.resolve(legacy)[0] is default_image
assert not legacy_resolver.warnings
renamed=bpy.data.materials.new('Renamed model material')
renamed['hl_source_material']='wall'
assert SurfaceMaterials(content,None,'wall').resolve(renamed)[0] is default_image
renamed.hammerless.source_material='unlit'
r=SurfaceMaterials(content,None,'wall');r.resolve(renamed)
assert r.unlit[renamed.as_pointer()], 'Explicit Source path lost precedence over model provenance'
assert BounceMaterials(content,None,'wall').resolve(legacy).verified
print('PASS: legacy model paths, renamed provenance, explicit override')
