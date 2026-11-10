import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

spec=importlib.util.spec_from_file_location('previewsource',Path(__file__).resolve().parents[2]/'hammerless/blender/previewsource.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class SourceRevisionTests(unittest.TestCase):
    def test_exact_changes_and_removal(self):
        cache=module.SourceRevision()
        for raw in ('abc','abc','abd','','','☃','☃'):
            self.assertEqual(cache.update(raw),hashlib.sha256(raw.encode()).digest())
        self.assertEqual(cache.hashes,4)
        self.assertIsNone(cache.update(None));self.assertIsNone(cache.raw)
        self.assertIsNone(cache.digest)
        cache.update('☃');self.assertEqual(cache.hashes,5)

    def test_budget_and_boundary(self):
        text='a'*100
        cache=module.SourceRevision(sys.getsizeof(text))
        cache.update(text);cache.update(''.join(['a']*100))
        self.assertEqual(cache.hashes,1)
        cache.update(text+'a');cache.update(text+'a')
        self.assertIsNone(cache.raw);self.assertEqual(cache.hashes,3)
        cache.update(text);cache.update(text)
        self.assertEqual(cache.hashes,4)

if __name__=='__main__':unittest.main()
