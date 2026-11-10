"""Cache invalidation/remapping tests without Blender or a worker process."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


class NativeSurfaceTests(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType('surface_test'); package.__path__ = []
        display = types.ModuleType('surface_test.previewlightcache')
        display.SurfaceCacheDisplay = lambda *a, **kw: object()
        cull = types.ModuleType('surface_test.previewcull')
        cull.visible_bounds = lambda bounds, matrix: np.ones(len(bounds), dtype=bool)
        gpu = types.ModuleType('gpu')
        gpu.types = types.SimpleNamespace(GPUTexture=lambda *a, **kw: object(), Buffer=lambda *a: a[-1])
        self.modules = patch.dict(sys.modules, {'surface_test': package,
            'surface_test.previewlightcache': display, 'surface_test.previewcull': cull, 'gpu': gpu})
        self.modules.start(); self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location('surface_test.previewnativesurface',
            ROOT / 'hammerless/blender/previewnativesurface.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.module = module
        self.cache = module.NativeSurfaceCache()
        self.live = types.SimpleNamespace(generation=1, positions=np.zeros((4, 3)),
            normals=np.tile((0., 0., 1.), (4, 1)), faces=[], bindings={}, units=1,
            bounds=np.zeros((2, 8, 4)), charts=[{'face': 0, 'start': 0, 'width': 2, 'height': 1},
                {'face': 1, 'start': 2, 'width': 2, 'height': 1}], display_tokens={0: ('a', 0), 1: ('b', 0)},
            object_data={10: {'generation': 'a'}, 20: {'generation': 'b'}}, keys={10, 20}, remaining={})
        self.submissions = []; self.result = None
        client = types.SimpleNamespace(poll=lambda *a: self.result,
            submit=lambda *a: self.submissions.append(a))
        self.native = types.SimpleNamespace(client=client, owner=1, serial=0,
            geometry_token='geometry', lights_token='lights', vertices=[], metadata=[], lights=[],
            pause=lambda: None, _flush_pending=lambda: None)
        self.scene = types.SimpleNamespace(unit_scale=1., sky_color=(0., 0., 0.))

    def step(self, signature='scene', **kw):
        return self.cache.step(self.native, self.live, self.scene, signature,
            rays=4, bounces=1, frames=1, **kw)

    def test_camera_and_pause_retain_illumination(self):
        self.step(); self.result = types.SimpleNamespace(error='', pixels=np.ones((4, 4)))
        self.step(); self.result = None
        before = self.cache.values.copy(); uploads = self.cache.uploads
        self.step(view_projection=np.eye(4))
        self.step(view_projection=np.eye(4)*2, paused=True)
        np.testing.assert_array_equal(self.cache.values, before)
        self.assertEqual(self.cache.uploads, uploads)
        self.assertEqual(self.cache.evaluations, 4)
        self.assertEqual(len(self.submissions), 1)

    def test_light_edit_replaces_stale_not_averages(self):
        self.step(); self.result = types.SimpleNamespace(error='', pixels=np.ones((4, 4)))
        self.step(); self.result = None
        self.step('new light', paused=True)
        self.assertTrue((self.cache.values == 1).all())
        self.assertTrue((self.cache.counts == 0).all())
        self.step('new light')
        self.result = types.SimpleNamespace(error='', pixels=np.full((4, 4), 3.))
        self.step('new light')
        self.assertTrue((self.cache.values[:, :3] == 3).all())

    def test_layout_remaps_only_matching_chart_tokens(self):
        self.cache.sync(self.live)
        self.cache.values[:] = [[1, 1, 1, 1], [2, 2, 2, 1], [3, 3, 3, 1], [4, 4, 4, 1]]
        self.live.generation += 1
        self.live.display_tokens = {0: ('b', 0), 1: ('new geometry', 0)}
        self.cache.sync(self.live)
        np.testing.assert_array_equal(self.cache.values[:2, 0], [3, 4])
        self.assertFalse(self.cache.values[2:].any())
        self.assertFalse(self.cache.counts.any())

    def test_layout_expansion_preserves_completed_sample_counts(self):
        self.step()
        self.result = types.SimpleNamespace(error='',pixels=np.ones((4,4)))
        self.step()
        self.result = None
        self.live.generation += 1
        self.live.positions=np.zeros((6,3));self.live.normals=np.tile((0.,0.,1.),(6,1))
        self.live.charts.append({'face':2,'start':4,'width':2,'height':1})
        self.live.display_tokens[2]=('new',0)
        self.live.object_data[30]={'generation':'new'};self.live.keys.add(30)
        self.live.bounds=np.zeros((3,8,4))
        self.step(paused=True)
        np.testing.assert_array_equal(self.cache.counts,[1,1,1,1,0,0])
        np.testing.assert_array_equal(self.cache.values[:4],np.ones((4,4)))

    def test_whole_object_readiness_partitions_draws(self):
        self.live.display_tokens = {0: ('a', 0), 1: ('a', 1)}
        self.cache.sync(self.live)
        self.cache.values[:2, 3] = 1
        self.cache._refresh_display(self.live)
        self.assertFalse(self.cache.exclude_keys)
        self.assertIsNone(self.cache.display)
        self.cache.values[2:, 3] = 1
        self.cache._refresh_display(self.live)
        self.assertEqual(self.cache.exclude_keys, {10})
        self.assertIsNotNone(self.cache.display)
        # A partially covered brush excludes only its matched faces.
        self.live.generation += 1
        self.live.keys = set(); self.live.remaining = {10: object()}
        self.cache.sync(self.live)
        self.assertFalse(self.cache.exclude_keys)
        self.assertIs(self.cache.remaining[10], self.live.remaining[10])

    def test_visible_unready_object_includes_hidden_charts(self):
        self.live.display_tokens = {0: ('a', 0), 1: ('a', 1)}
        self.module.visible_bounds = lambda *args: np.array([True, False])
        self.step(view_projection=np.eye(4))
        # points are the penultimate native submit argument.
        self.assertEqual(len(self.submissions[-1][-2]), 4)


if __name__ == '__main__':
    unittest.main()
