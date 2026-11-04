import tempfile
from pathlib import Path
import unittest
import numpy as np
from hammerless.core.texturemods import build_package,material_path,resolved_material
from hammerless.core.textures import write_vtf
from hammerless.core.vpk import VPK
from hammerless.core.vtf_read import read_vtf
from hammerless.core.vmf import parse

class Content:
    def __init__(self,files):self.files=files
    def read(self,path):return self.files.get(path)

class TextureModsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.rgba=np.full((4,4,4),255,np.uint8);self.rgba[:,:,:3]=[60,80,100]
        old=self.rgba.copy();old[:,:,3]=np.arange(16,dtype=np.uint8).reshape(4,4)*10
        write_vtf(str(self.path/'old.vtf'),old)
        self.old=old
        self.content=Content({'materials/shared.vmt':b'Infected { "$basetexture" "old" "$disablevariation" "1" "$woundcutouttexture" "wound" Proxies { Test { "resultVar" "$foo" } } }',
                              'materials/models/zombie.vmt':b'Patch { include "materials/shared.vmt" insert { "$phong" "1" } replace { "$basetexture" "old" } }',
                              'materials/old.vtf':(self.path/'old.vtf').read_bytes()})
    def tearDown(self):self.temp.cleanup()
    def test_patch_preserves_shader_nested_blocks_alpha_and_rebuild_removes(self):
        out=self.path/'mod.vpk'
        result=build_package(self.content,{'models/zombie':{'basecolor':self.rgba}},out,'test')
        self.assertEqual(result['materials'],1);v=VPK(str(out));root=parse(v.read('materials/models/zombie.vmt').decode())[0]
        self.assertEqual(root.name,'Infected');self.assertEqual(root.get('$woundcutouttexture'),'wound');self.assertEqual(len(root.blocks('Proxies')),1)
        _,_,im=read_vtf(v.read('materials/'+root.get('$basetexture')+'.vtf'))
        np.testing.assert_array_equal(im,self.old)
        build_package(self.content,{},out,'test',source_blend='C:/project/source.blend')
        self.assertEqual(set(VPK(str(out)).entries),{'addoninfo.txt','hammerless_material_mod.json'})
        import json
        manifest=json.loads(VPK(str(out)).read('hammerless_material_mod.json'))
        self.assertEqual(manifest['source_blend'],'C:/project/source.blend')
        self.assertEqual(manifest['targets'],[])
    def test_path_validation_and_duplicate_targets(self):
        self.assertEqual(material_path('Materials/Models\\ZOMBIE.vmt'),'models/zombie')
        for value in ('../x','x/../y','/absolute','c:/x','x//y','x\nfoo','x"foo',''):
            with self.assertRaises(ValueError):material_path(value)
        with self.assertRaises(ValueError):build_package(self.content,{'models/zombie':{'basecolor':self.rgba},'Models/Zombie.vmt':{'basecolor':self.rgba}},self.path/'x.vpk','test')
    def test_failure_leaves_old_package_untouched(self):
        out=self.path/'mod.vpk';out.write_bytes(b'previous')
        with self.assertRaises(ValueError):build_package(self.content,{'missing':{'basecolor':self.rgba}},out,'test')
        self.assertEqual(out.read_bytes(),b'previous')
    def test_normal_for_supported_shader(self):
        self.content.files['materials/shared.vmt']=b'VertexLitGeneric { "$basetexture" "old" }'
        out=self.path/'mod.vpk';build_package(self.content,{'models/zombie':{'basecolor':self.rgba,'normal':self.rgba}},out,'test')
        v=VPK(str(out));root=parse(v.read('materials/models/zombie.vmt').decode())[0]
        self.assertIsNotNone(root.get('$bumpmap'));self.assertEqual(len(v.entries),5)
    def test_missing_alpha_source_and_patch_cycle_fail(self):
        del self.content.files['materials/old.vtf']
        with self.assertRaisesRegex(ValueError,'alpha'):build_package(self.content,{'models/zombie':{'basecolor':self.rgba}},self.path/'x.vpk','test')
        self.content.files['materials/shared.vmt']=b'Patch { include "models/zombie" }'
        with self.assertRaisesRegex(ValueError,'Cyclic'):resolved_material(self.content,'models/zombie')
    def test_infected_normal_alpha_and_stock_normal_mask(self):
        out=self.path/'mod.vpk';normal=self.rgba.copy();normal[:,:,3]=32
        report=build_package(self.content,{'models/zombie':{'basecolor':self.rgba,'normal':normal}},out,'test')
        self.assertTrue(any('approximation' in w for w in report['warnings']))
        v=VPK(str(out));root=parse(v.read('materials/models/zombie.vmt').decode())[0]
        _,_,pixels=read_vtf(v.read('materials/'+root.get('$bumpmap')+'.vtf'))
        np.testing.assert_array_equal(pixels[:,:,3],normal[:,:,3])
        self.content.files['materials/shared.vmt']=b'Infected { "$basetexture" "old" "$bumpmap" "old" }'
        report=build_package(self.content,{'models/zombie':{'basecolor':self.rgba,'normal':normal}},out,'test')
        v=VPK(str(out));root=parse(v.read('materials/models/zombie.vmt').decode())[0]
        self.assertEqual(root.get('$disablevariation'),'1')
        self.assertTrue(any('palette variation' in w for w in report['warnings']))
        _,_,pixels=read_vtf(v.read('materials/'+root.get('$bumpmap')+'.vtf'))
        np.testing.assert_array_equal(pixels[:,:,3],self.old[:,:,3])
        self.assertFalse(any('approximation' in w for w in report['warnings']))

if __name__=='__main__':unittest.main()
