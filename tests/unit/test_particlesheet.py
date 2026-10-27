import struct
import unittest
import numpy as np
from hammerless.core.particlesheet import read_sequences, Sequence


def fixture(version=0):
    header=bytearray(88)
    header[:4]=b'VTF\0'
    struct.pack_into('<II',header,4,7,3)
    struct.pack_into('<I',header,68,1)
    struct.pack_into('<3sBI',header,80,b'\x10\0\0',0,88)
    data=struct.pack('<IIIIIf',version,1,7,0,2,.75)
    for duration,rect in [(.25,(0,0,.5,1)),(.5,(.5,0,1,1))]:
        data+=struct.pack('<f',duration)
        data+=struct.pack('<4f',*rect)*(1 if version==0 else 4)
    return bytes(header)+struct.pack('<I',len(data))+data


class SheetTests(unittest.TestCase):
    def test_versions_and_nonuniform_times(self):
        for version in (0,1):
            seq=read_sequences(fixture(version))[7]
            self.assertEqual(seq.rectangles.shape,(2,1 if version==0 else 4,4))
            self.assertEqual(seq.frame(.24,1),0)
            self.assertEqual(seq.frame(.25,1),1)
            self.assertEqual(seq.frame(.75,1),0)

    def test_clamp_fit_and_fps(self):
        seq=read_sequences(fixture())[7];seq.clamp=True
        self.assertEqual(seq.frame(100,1),1)
        self.assertEqual(seq.frame(1,1,fit=True),1)
        self.assertEqual(seq.frame(.2,10,rate=10,fps=True),1)
        seq.clamp=False
        self.assertEqual(seq.frame(.2,10,rate=10,fps=True),0)

    def test_malformed_resources(self):
        with self.assertRaises(ValueError):read_sequences(fixture()[:-3])
        data=bytearray(fixture());struct.pack_into('<I',data,92,99)
        with self.assertRaises(ValueError):read_sequences(data)
        self.assertEqual(read_sequences(b''),{})
