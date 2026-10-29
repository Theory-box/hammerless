import struct
import tempfile
import unittest
from pathlib import Path
import os
import sys

from hammerless.core.ambientquery import read_ambient, augment_scripts, MARKER

if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0, os.environ['SQUIRREL_PYTHONPATH'])
try:
    import squirrel
except ImportError:
    squirrel = None


def bsp(*, hdr=True, fallback=False):
    chunks = {}
    chunks[1] = (0, struct.pack('<4fi', 1, 0, 0, 0, 0))
    chunks[5] = (0, struct.pack('<3i', 0, -1, -2) + bytes(20))
    leaf = struct.pack('<ihh6h', 0, 0, 0, -100, -100, -100, 100, 100, 100) + bytes(12)
    chunks[10] = (1, leaf * 2)
    chunks[14] = (0, bytes(36) + struct.pack('<i', 0) + bytes(8))
    chunks[51 if hdr else 52] = (0, struct.pack('<4H', 1, 0, 0 if fallback else 1, 0 if fallback else 1))
    chunks[55 if hdr else 56] = (1, (bytes((255, 128, 0, 0)) * 6 + bytes((128, 128, 128, 0))) + (bytes((0, 0, 255, 0)) * 6 + bytes((128, 128, 128, 0))))
    data = bytearray(b'VBSP' + struct.pack('<i', 21) + bytes(1028))
    for i, (ver, raw) in chunks.items():
        struct.pack_into('<4i', data, 8 + i * 16, ver, len(data), len(raw), 0)
        data.extend(raw)
    return data


class AmbientTests(unittest.TestCase):
    @unittest.skipUnless(squirrel is not None and hasattr(squirrel, 'SQVM'), 'optional squirrel-lang not installed')
    def test_runtime_leaf_separation_and_missing(self):
        from hammerless.core.ambientquery import squirrel_data
        runtime = (Path(__file__).resolve().parents[2] / 'hammerless/core/ambient_query_runtime.nut').read_text()
        vector = '''class Vector {
            x=0.0;y=0.0;z=0.0;
            constructor(a,b,c){x=a;y=b;z=c;}
            function _add(v){return Vector(x+v.x,y+v.y,z+v.z);}
            function _mul(v){return Vector(x*v,y*v,z*v);}
        }
        function check(v){if(!v)throw "ambient test failed";}
        '''
        vm = squirrel.SQVM()
        vm.execute(vector + runtime + '\ncheck(!HLWQ_Ambient(Vector(1,0,0)).available);')
        vm.execute(squirrel_data(read_ambient(bsp())) + '''
            local a=HLWQ_Ambient(Vector(1,0,0));
            local b=HLWQ_Ambient(Vector(-1,0,0));
            check(a.available && b.available && a.color.x>0.99 && b.color.x==0);
            check(a.color.z==0 && b.color.z>0.99 && !a.fallback);
            HL_AmbientData.leaves[1][1]=0;
            HL_AmbientData.leaves[1][2]=0;
            b=HLWQ_Ambient(Vector(-1,0,0));
            check(b.available && b.fallback && b.color.x>0.99);
            HL_AmbientData.leaves[1][3]=true;
            check(!HLWQ_Ambient(Vector(-1,0,0)).available);
        ''')

    def test_hdr_leaf_local_samples_linear_color(self):
        d = read_ambient(bsp())
        self.assertTrue(d['hdr'])
        self.assertEqual(d['nodes'], [[0, -1, -2]])
        self.assertAlmostEqual(d['probes'][0][3], 1)
        self.assertAlmostEqual(d['probes'][0][4], 128 / 255)
        self.assertAlmostEqual(d['probes'][1][5], 1)
        self.assertEqual(d['leaves'][1][:3], [1, 1, -1])

    def test_ldr_and_neighbor_leaf_reference(self):
        d = read_ambient(bsp(hdr=False, fallback=True))
        self.assertFalse(d['hdr'])
        self.assertEqual(d['leaves'][1][:3], [1, 0, 0])

    def test_reject_corruption(self):
        for index in (1, 5, 10, 14, 51, 55):
            data = bsp()
            struct.pack_into('<i', data, 8 + index * 16 + 4, len(data) + 1)
            with self.assertRaises(ValueError):
                read_ambient(data)
        data = bsp()
        node_off = struct.unpack_from('<i', data, 8 + 5 * 16 + 4)[0]
        struct.pack_into('<i', data, node_off + 4, 0)
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            read_ambient(data)

    def test_only_requested_script_augmented_and_missing_explicit(self):
        files = {'a': MARKER, 'b': 'untouched'}
        out, warnings = augment_scripts(files, None, 'test')
        self.assertIn('null', out['a'])
        self.assertTrue(warnings)
        self.assertEqual(out['b'], 'untouched')
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'test.bsp'
            p.write_bytes(bsp())
            out, warnings = augment_scripts(files, p, 'test')
        self.assertFalse(warnings)
        self.assertIn('HL_AmbientData.probes.append', out['a'])
        self.assertNotIn(MARKER, out['a'])


if __name__ == '__main__':
    unittest.main()
