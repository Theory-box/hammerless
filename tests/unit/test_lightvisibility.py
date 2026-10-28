import unittest

from hammerless.core.geometry import box_brush
from hammerless.core.vmf import VMFWriter, Block, parse
from hammerless.core.collision import brush_from_vmf_sides
from hammerless.core.lightvisibility import Options, Face, blocked, groups, optimize, scale_for


def document(brushes, entities=()):
    w = VMFWriter()
    world = Block('world').kv('id', 1).kv('classname', 'worldspawn')
    for b in brushes:
        world.add(w.solid(b))
    out = []
    for b in [world, *entities]:
        b.write(out)
    return ''.join(out)


def collision(brush):
    block = VMFWriter().solid(brush)
    return brush_from_vmf_sides([(s.get('plane'), s.get('material')) for s in block.blocks('side')])


def scales(text):
    return [int(s.get('lightmapscale')) for w in parse(text) for b in w.blocks('solid') for s in b.blocks('side')]


class VisibilityTests(unittest.TestCase):
    def test_editor_keywords_do_not_protect_hidden_stock_materials(self):
        from hammerless.core.lightvisibility import _opaque
        class Content:
            def read(self, path):
                return b'LightmappedGeneric { "$basetexture" "brick/wall" "%keywords" "l4d2" }'
        content = Content()
        self.assertTrue(_opaque('brick/wall', content, {}))
        floor = box_brush((-100, -100, -16), (100, 100, 0), 'brick/wall')
        out, report = optimize(document([floor]), content)
        self.assertEqual(scales(out), [16, 64, 16, 16, 16, 16])
        self.assertIn('1 unseen', report)

    def test_keywords_do_not_override_transparency_or_compile_flags(self):
        from hammerless.core.lightvisibility import _opaque
        class Content:
            def __init__(self, flag): self.flag = flag
            def read(self, path):
                return ('LightmappedGeneric { "%keywords" "l4d2" "' + self.flag + '" "1" }').encode()
        for flag in ('$translucent', '$alphatest', '$nocull', '%compileinvisible', '%compileclip'):
            self.assertFalse(_opaque('brick/wall', Content(flag), {}))

    def test_disabled_by_default_and_export_does_not_analyze(self):
        from unittest.mock import patch
        from hammerless.core.ir import MapIR, Entity
        from hammerless.core.build import build_vmf
        ir = MapIR(brushes=[box_brush((-100, -100, -16), (100, 100, 0), 'brick/wall')],
                   entities=[Entity('info_player_start', (0, 0, 1))])
        self.assertFalse(ir.settings.adaptive_lightmaps)
        with patch('hammerless.core.lightvisibility.optimize') as analyze:
            text, report = build_vmf(ir)
            self.assertTrue(text, report.errors)
            analyze.assert_not_called()
        ir.settings.adaptive_lightmaps = True
        with patch('hammerless.core.lightvisibility.optimize', side_effect=lambda text, *a: (text, 'analysis ran')) as analyze:
            text, report = build_vmf(ir)
            self.assertTrue(text, report.errors)
            analyze.assert_called_once()
            self.assertIn('analysis ran', report.info)

    def test_floor_underside_coarse_top_detailed(self):
        floor = box_brush((-100, -100, -16), (100, 100, 0), 'brick/wall')
        for f in floor.faces:
            f.lightmap_scale = 4
        text = document([floor])
        out, report = optimize(text)
        # Input order is +z,-z,-y,+y,+x,-x, unlike collision's canonical order.
        self.assertEqual(scales(out), [4, 64, 4, 4, 4, 4])
        self.assertIn('1 unseen', report)
        before, after = parse(text)[0], parse(out)[0]
        for a, b in zip(before.blocks('solid')[0].blocks('side'), after.blocks('solid')[0].blocks('side')):
            for key in ('id', 'plane', 'material', 'uaxis', 'vaxis'):
                self.assertEqual(a.get(key), b.get(key))

    def test_sky_shell_does_not_create_walkable_viewpoints(self):
        floor = box_brush((-100, -100, -16), (100, 100, 0), 'brick/wall')
        shell = box_brush((-500, -500, -200), (500, 500, -190), 'tools/toolsskybox')
        out, _ = optimize(document([floor, shell]))
        self.assertEqual(scales(out)[1], 64)

    def test_lower_walkway_protects_underside(self):
        ceiling = box_brush((-100, -100, 100), (100, 100, 116), 'brick/wall')
        floor = box_brush((-100, -100, -16), (100, 100, 0), 'brick/wall')
        out, _ = optimize(document([ceiling, floor]))
        self.assertEqual(scales(out)[1], 16)

    def test_full_region_occluded_but_slit_or_large_receiver_not_proven(self):
        wall = collision(box_brush((40, -20, -20), (60, 20, 20), 'brick/wall'))
        eyes = ((0, -1, -1), (1, 1, 1))
        self.assertTrue(blocked(eyes, ((100, -1, -1), (100, 1, 1)), wall))
        self.assertFalse(blocked(eyes, ((100, -100, -1), (100, 100, 1)), wall))
        # A narrow obstruction on the centre ray does not prove the entire region hidden.
        self.assertFalse(blocked(((0, -100, -1), (1, 100, 1)), ((100, -1, -1), (100, 1, 1)), wall))

    def test_behind_target_or_behind_eye_is_not_occlusion(self):
        for x in (-20, 120):
            wall = collision(box_brush((x, -20, -20), (x + 10, 20, 20), 'brick/wall'))
            self.assertFalse(blocked(((0, 0, 0), (0, 1, 1)), ((100, 0, 0), (100, 1, 1)), wall))

    def test_connected_surfaces_and_opposite_sides(self):
        def f(lo, hi, normal=(0, 0, 1)):
            return Face(None, normal, 0, (lo, hi), 4)
        a = f((0, 0, 0), (10, 10, 0))
        b = f((10, 0, 0), (20, 10, 0))
        c = f((30, 0, 0), (40, 10, 0))
        d = f((0, 0, 0), (10, 10, 0), (0, 0, -1))
        self.assertEqual(sorted(map(len, groups([a, b, c, d]))), [1, 1, 2])

    def test_scale_limits_and_unseen_only(self):
        opts = Options(512, 64)
        self.assertEqual([scale_for(4, d, opts) for d in (0, 1023, 1024, 2048, float('inf'))], [4, 4, 8, 16, 64])
        self.assertEqual(scale_for(128, float('inf'), opts), 128)
        self.assertEqual(scale_for(4, 10000, Options(distance=False)), 4)

    def test_unsupported_map_is_unchanged(self):
        floor = box_brush((-100, -100, -16), (100, 100, 0), 'brick/wall')
        text = document([floor], [Block('entity').kv('classname', 'func_tracktrain')])
        self.assertEqual(optimize(text)[0], text)

    def test_transparent_and_unknown_materials_never_occlude(self):
        from hammerless.core.lightvisibility import _opaque
        class Content:
            def read(self, path):
                return {'materials/opaque.vmt': b'LightmappedGeneric { "$basetexture" "wall" }',
                        'materials/fence.vmt': b'LightmappedGeneric { "$alphatest" "1" }',
                        'materials/patch.vmt': b'patch { "include" "opaque" }'}.get(path)
        self.assertTrue(_opaque('opaque', Content(), {}))
        for name in ('fence', 'patch', 'missing', 'tools/toolsskybox'):
            self.assertFalse(_opaque(name, Content(), {}))


if __name__ == '__main__':
    unittest.main()
