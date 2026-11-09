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
