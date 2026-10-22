import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from hammerless.core.skyexport import sky_materials, write_materials, pack_sky, SIDES
from hammerless.core.vmf import parse
from hammerless.core.stringtables import put_files


class Content:
    def __init__(self, material):
        self.material = material
    def read(self, path):
        return self.material.encode() if path.endswith('.vmt') else None


class SkyExportTests(unittest.TestCase):
    def test_hdr_override_is_scaled_not_left_at_original_brightness(self):
        content = Content('Sky { "$color" "[.65 .65 .65]" Sky_HDR_dx9 { "$color" "[.1 .1 .1]" } }')
        _, files = sky_materials(content, None, 'map', 'test_moon_hdr', -12)
        block = parse(next(iter(files.values())))[0]
        for owner, original in ((block, .65), (block.blocks('Sky_HDR_dx9')[0], .1)):
            values = [float(v) for v in owner.get('$color').strip('[]').split()]
            for value in values:
                self.assertAlmostEqual(value, original / 4096, places=12)

    def test_hdr_tint_and_nested_settings_preserved(self):
        content = Content('Sky { "$hdrcompressedtexture" "skybox/original" "$basetexture" "skybox/ldr" '
                          '"$color" "{128 255 64}" HDR { "$foo" "bar" } }')
        name, files = sky_materials(content, None, 'test_map', 'sky', -2)
        self.assertEqual(len(files), 6)
        for text in files.values():
            b = parse(text)[0]
            self.assertEqual(b.get('$hdrcompressedtexture'), 'skybox/original')
            self.assertEqual(b.get('$basetexture'), 'skybox/ldr')
            self.assertEqual(b.blocks('HDR')[0].get('$foo'), 'bar')
            values = [float(v) for v in b.get('$color').strip('[]').split()]
            self.assertAlmostEqual(values[0], 128 / 255 / 4)
            self.assertEqual(values[1], .25)
        self.assertEqual(sky_materials(content, None, 'test_map', 'sky', -2)[0], name)
        self.assertNotEqual(sky_materials(content, None, 'test_map', 'sky', -1)[0], name)

    def test_zero_and_missing(self):
        self.assertEqual(sky_materials(None, None, 'map', 'sky', 0), ('sky', {}))
        with self.assertRaises(ValueError):
            sky_materials(None, None, 'map', 'sky', -1)

    def test_packing_preserves_existing_assets_and_other_lumps(self):
        with tempfile.TemporaryDirectory() as tmp:
            name, files = sky_materials(Content('Sky { "$basetexture" "skybox/original" }'), tmp, 'map', 'sky', -1)
            write_materials(tmp, files)
            entities = ('{\n"classname" "worldspawn"\n"skyname" "' + name + '"\n}\n\0').encode()
            data = bytearray(1036)
            data[:4] = b'VBSP'
            struct.pack_into('<i', data, 4, 21)
            struct.pack_into('<4i', data, 8, 0, len(data), len(entities), 0)
            data += entities
            path = Path(tmp) / 'map.bsp'
            path.write_bytes(data)
            put_files(str(path), {'keep.txt': b'unchanged'})
            pack_sky(str(path), tmp, 'map')
            b = path.read_bytes()
            _, off, size, _ = struct.unpack_from('<4i', b, 8 + 16 * 40)
            with zipfile.ZipFile(io.BytesIO(b[off:off+size])) as z:
                self.assertEqual(z.read('keep.txt'), b'unchanged')
                for rel, text in files.items():
                    self.assertEqual(z.read(rel), text.encode())
            self.assertEqual(b[1036:1036+len(entities)], entities)

    def test_patch_and_loose_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'materials/skybox'
            folder.mkdir(parents=True)
            (folder / 'base.vmt').write_text('Sky { "$color" "[.8 .6 .4]" "$hdrbasetexture" "skybox/hdr" }')
            for side in SIDES:
                (folder / ('sky' + side + '.vmt')).write_text('Patch { "include" "skybox/base.vmt" replace { "$color" "[.4 .2 .1]" } }')
            _, files = sky_materials(None, tmp, 'map', 'sky', 1)
            b = parse(next(iter(files.values())))[0]
            self.assertEqual(b.get('$color'), '[0.8 0.4 0.2]')
            self.assertEqual(b.get('$hdrbasetexture'), 'skybox/hdr')
