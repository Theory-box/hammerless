import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from hammerless.core.vpk import write_vpk
from hammerless.core.modregistry import scan,uninstall,MANIFEST

UID='a'*32
NAME='hlm_'+UID+'.vpk'

class ModRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.addons=self.root/'left4dead2'/'addons';self.addons.mkdir(parents=True)
    def tearDown(self):self.tmp.cleanup()
    def package(self,name=NAME,manifest=True,author='Hammerless',**changes):
        files={'addoninfo.txt':('AddonInfo { "addonAuthor" "'+author+'" "addonTitle" "Test" }').encode(),
               'materials/models/zombie.vmt':b'VertexLitGeneric { }'}
        if manifest:
            data=dict(uid=UID,source_blend='C:/Art/renamed.blend',title='My Zombie');data.update(changes)
            files[MANIFEST]=json.dumps(data).encode()
        write_vpk(str(self.addons/name),files)
    def test_manifest_source_and_actual_targets_across_blends(self):
        self.package(targets=['incorrect'])
        item=scan(self.root)[0]
        self.assertEqual(item['source_blend'],'C:/Art/renamed.blend')
        self.assertEqual(item['targets'],['models/zombie']);self.assertFalse(item['legacy'])
    def test_legacy_and_empty_installs_discovered(self):
        name='hammerless_materials_'+UID+'.vpk';self.package(name,False)
        item=scan(self.root)[0];self.assertEqual(item['filename'],name)
        self.assertTrue(item['legacy']);self.assertEqual(item['source_blend'],'')
    def test_uninstall_moves_out_of_addons_and_retains_backup(self):
        self.package();before=(self.addons/NAME).read_bytes()
        saved=Path(uninstall(self.root,NAME))
        self.assertFalse((self.addons/NAME).exists());self.assertEqual(saved.read_bytes(),before)
        self.assertEqual(scan(self.root),[]);self.assertEqual(saved.parent,self.root/'left4dead2'/'hammerless_uninstalled')
    def test_unrelated_malformed_and_identity_mismatch_rejected(self):
        self.package(author='Someone Else');self.assertEqual(scan(self.root),[])
        with self.assertRaises(ValueError):uninstall(self.root,NAME)
        self.package(uid='b'*32);self.assertEqual(scan(self.root),[])
        (self.addons/NAME).write_bytes(b'not a VPK')
        self.assertEqual(scan(self.root),[])
    def test_no_path_traversal_and_uninstall_failure_retains_package(self):
        self.package()
        for name in ('../'+NAME,str(self.addons/NAME),'other.vpk'):
            with self.assertRaises(ValueError):uninstall(self.root,name)
        with patch('hammerless.core.modregistry.os.replace',side_effect=OSError('locked')):
            with self.assertRaises(OSError):uninstall(self.root,NAME)
        self.assertTrue((self.addons/NAME).exists())
    def test_stale_ui_cannot_uninstall_replaced_unowned_file(self):
        self.package();scan(self.root);self.package(author='Not Hammerless')
        with self.assertRaises(ValueError):uninstall(self.root,NAME)
        self.assertTrue((self.addons/NAME).exists())
    def test_missing_game_root_is_actionable(self):
        for root in (None,''):
            with self.assertRaisesRegex(ValueError,'L4D2 folder'):scan(root)

if __name__=='__main__':unittest.main()
