import struct
import unittest
from hammerless.core.lightmap import nonrendering_faces
from hammerless.core.compile import CompileOptions, _serves, _opts_rest


class LightReceiverTests(unittest.TestCase):
    def test_shared_material_volume_hidden_visible_brush_kept(self):
        entities=b'{"classname" "fog_volume" "model" "*1"} {"classname" "func_brush" "model" "*2"}'
        models=bytearray(3*48)
        for m in range(3):struct.pack_into("<2i",models,m*48+40,m,1)
        faces=bytearray(3*56)
        lumps={0:entities,14:models,6:bytearray(72)}
        self.assertEqual(nonrendering_faces(lambda i:lumps.get(i,b""),faces),{1})

    def test_tool_materials_and_flags_but_not_visible_toolsblack(self):
        materials=['tools/fogvolume','tools/toolsblack','tools/toolsblocklight','dev/wall']
        strings=b''; offsets=[]
        for name in materials:offsets.append(len(strings));strings+=name.encode()+b'\0'
        td=bytearray(32*4);ti=bytearray(72*4);faces=bytearray(56*4)
        for i in range(4):
            struct.pack_into('<i',td,i*32+12,i)
            struct.pack_into('<2i',ti,i*72+64,0x80 if i==3 else 0,i)
            struct.pack_into('<h',faces,i*56+10,i)
        lumps={2:td,6:ti,43:strings,44:struct.pack('<4i',*offsets)}
        self.assertEqual(nonrendering_faces(lambda i:lumps.get(i,b''),faces),{0,2,3})

    def test_old_bakes_are_relit_without_geometry_rebuild(self):
        opts=CompileOptions()
        old=repr(opts).replace(', light_receiver_revision=1','')
        self.assertFalse(_serves(old,opts))
        self.assertEqual(_opts_rest(old),_opts_rest(repr(opts)))
