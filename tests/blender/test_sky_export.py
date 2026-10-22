"""Blender --background --factory-startup --python tests/blender/test_sky_export.py."""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import hammerless
from hammerless.core.ir import MapIR
from hammerless.core.build import Report
from hammerless.core.vmf import Block, VMFWriter, parse
from hammerless.core.vmfimport import Document
from hammerless.blender.ops import map_text
from tests.unit.test_skyexport import Content

hammerless.register()
from hammerless.blender.extract import scene_settings_to_ir
s = bpy.context.scene.hammerless
s.ambient_color = (0, 0, 0)
s.ambient_brightness = 100
for mode in ('SKYBOX', 'IMAGE'):
    # Use the actual enum identifier for the HDRI mode below if available.
    if mode not in s.bl_rna.properties['sky_light'].enum_items.keys():
        continue
    s.sky_light = mode
    settings = scene_settings_to_ir(s)
    assert settings.ambient_color == (255, 255, 255)
    assert settings.ambient_brightness == 100
s.sky_light = 'FLAT'
assert scene_settings_to_ir(s).ambient_color == (0, 0, 0)
assert bpy.context.scene.hammerless.sky_view_exposure == 0
bpy.context.scene.hammerless.sky_view_exposure = -3
with tempfile.TemporaryDirectory() as tmp:
    for imported in (False, True):
        ir = MapIR()
        ir.settings.name = 'sky_test'
        ir.settings.auto_seal = False
        ir.settings.auto_light_environment = False
        ir._sky_export = (Content('Sky { "$hdrcompressedtexture" "skybox/source" }'), tmp, -3)
        base = None
        if imported:
            doc = Document('world { "id" "1" "classname" "worldspawn" "skyname" "original" }')
            base = (doc, doc.world, [], VMFWriter())
        _, text, report = map_text(ir, None, base, Report())
        assert text is not None, report.errors
        world = next(b for b in parse(text) if b.name == 'world')
        assert world.get('skyname').startswith('hammerless/sky_test/sky_')
        assert len(list((Path(tmp)/'materials/skybox/hammerless/sky_test').glob('*.vmt'))) == 6
hammerless.unregister()
print('Sky export: authored and imported maps use generated sky; registration succeeds.')
