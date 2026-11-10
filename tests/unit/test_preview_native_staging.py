"""CPU-only NativeView staging tests; AST loads class without importing GPU/Blender."""
import ast
from pathlib import Path
from types import SimpleNamespace as S
import unittest
from unittest.mock import Mock

source=Path(__file__).resolve().parents[2]/'hammerless/blender/previewnativeview.py'
node=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='NativeView')

class StagingTests(unittest.TestCase):
    def setUp(self):
        self.client=Mock()
        self.native=S(get_client=lambda:self.client,flatten_scene=Mock(side_effect=lambda cache,resolver:(cache,'meta')),pack_lights=Mock(side_effect=lambda cache:cache))
        scope={'previewnative':self.native}
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope)
        self.view=scope['NativeView'](17)
    def test_latest_only_pause_retains_and_flush_once(self):
        for i in range(50):self.view.stage(i,None,i,i)
        self.view.pause()
        self.assertIsNotNone(self.view._pending_prepare)
        self.native.flatten_scene.assert_not_called();self.native.pack_lights.assert_not_called()
        self.view._flush_pending();self.view._flush_pending()
        self.native.flatten_scene.assert_called_once_with(49,None)
        self.native.pack_lights.assert_called_once_with(49)
        self.assertEqual(self.view.geometry_token,49)
    def test_light_update_reuses_geometry_and_immediate_overrides_stage(self):
        self.view.prepare('initial',None,1,1)
        self.view.stage('light',None,1,2);self.view._flush_pending()
        self.assertEqual(self.native.flatten_scene.call_count,1)
        self.assertEqual(self.view.vertices,'initial');self.assertEqual(self.view.lights,'light')
        self.view.stage('old',None,2,2)
        self.view.prepare('new',None,3,3);self.view._flush_pending()
        self.assertEqual(self.view.vertices,'new');self.assertIsNone(self.view._pending_prepare)
    def test_failure_does_not_commit_and_close_drops_pending(self):
        self.view.prepare('initial',None,1,1)
        self.view.stage('next',None,2,2)
        self.native.pack_lights.side_effect=RuntimeError('pack failed')
        with self.assertRaises(RuntimeError):self.view._flush_pending()
        self.assertEqual(self.view.geometry_token,1);self.assertEqual(self.view.vertices,'initial')
        self.assertIsNotNone(self.view._pending_prepare)
        self.view.close();self.assertIsNone(self.view._pending_prepare)
        self.client.release.assert_called_once_with(17)

if __name__=='__main__':unittest.main()
