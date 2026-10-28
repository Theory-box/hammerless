import unittest
from types import SimpleNamespace
import numpy as np
from hammerless.core.lightdensity import resolution_mesh, COLORS


class DensityTests(unittest.TestCase):
    def mesh(self, sx, sy, offset=(0, 0)):
        return SimpleNamespace(atlas=np.zeros((128, 256, 4)),
            positions=np.array([[0, 0, 0], [sx * 2, 0, 0], [0, sy * 2, 0]], dtype=float),
            uvs=(np.array([[0, 0], [2, 0], [0, 2]], dtype=float) + offset) / (256, 128))

    def test_actual_scale_independent_of_atlas_offset(self):
        for scale, color in zip((4, 8, 16, 32, 64, 128), COLORS):
            uv, colors, limits = resolution_mesh(self.mesh(scale, scale, (41.5, 20.5)))
            np.testing.assert_allclose(colors, [color] * 3)
            np.testing.assert_allclose(limits, (scale, scale))
            np.testing.assert_allclose(uv[0], (41.5, 20.5))

    def test_anisotropic_mapping_reports_both_axes(self):
        _, colors, limits = resolution_mesh(self.mesh(4, 64))
        self.assertEqual(limits, (4, 64))
        np.testing.assert_allclose(colors, [COLORS[4]] * 3)

    def test_degenerate_mapping_is_grey_not_nan(self):
        m = self.mesh(16, 16)
        m.uvs[:] = 0
        _, colors, limits = resolution_mesh(m)
        self.assertEqual(limits, (0, 0))
        np.testing.assert_allclose(colors, [[0.4] * 3] * 3)

    def test_rotation_does_not_change_density(self):
        m = self.mesh(8, 32)
        m.positions = m.positions[:, (2, 0, 1)] + (1000, -500, 50)
        self.assertEqual(resolution_mesh(m)[2], (8, 32))

    def test_compiled_axes_override_unstable_preview_triangle(self):
        m = self.mesh(8, 8)
        m.uvs[:] = 0  # quantized/degenerate preview UVs must not change the metric
        m.luxel_uvs = np.array([[0.5, 0.5], [2.5, 0.5], [0.5, 2.5]])
        m.luxel_steps = np.array([[8.0, 64.0]])
        uv, colors, limits = resolution_mesh(m)
        self.assertEqual(limits, (8, 64))
        np.testing.assert_allclose(colors, [COLORS[4]] * 3)
        np.testing.assert_allclose(uv, m.luxel_uvs)
