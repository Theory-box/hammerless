import struct
import unittest
import numpy as np
from hammerless.core.vtf_read import preview_digest, read_vtf


def fixture():
    header=bytearray(80)
    struct.pack_into('<4s3I',header,0,b'VTF\0',7,2,80)
    struct.pack_into('<HHIHH',header,16,4,4,0,1,0)
    struct.pack_into('<iBiBB',header,52,0,3,-1,0,0)
    struct.pack_into('<H',header,63,1)
    return header+bytearray([10,20,30,255])*21


class PreviewDigestTests(unittest.TestCase):
    def test_unused_mip_does_not_invalidate_identical_preview(self):
        original=fixture();changed=original.copy();changed[-4]=99
        self.assertEqual(preview_digest(original,2),preview_digest(changed,2))
        np.testing.assert_array_equal(read_vtf(original,2)[2],read_vtf(changed,2)[2])
        self.assertNotEqual(preview_digest(original,4),preview_digest(changed,4))

    def test_used_mip_and_decode_format_invalidate(self):
        original=fixture();changed=original.copy();changed[84]=99
        self.assertNotEqual(preview_digest(original,2),preview_digest(changed,2))
        self.assertFalse(np.array_equal(read_vtf(original,2)[2],read_vtf(changed,2)[2]))
        changed=original.copy();struct.pack_into('<i',changed,52,1)
        self.assertNotEqual(preview_digest(original,2),preview_digest(changed,2))

    def test_dimensions_returned_and_pixels_preserved(self):
        w,h,pixels=read_vtf(fixture(),2)
        self.assertEqual((w,h),(4,4));self.assertEqual(pixels.shape,(2,2,4))
        np.testing.assert_array_equal(pixels,np.tile([10,20,30,255],(2,2,1)))


if __name__=='__main__':unittest.main()
