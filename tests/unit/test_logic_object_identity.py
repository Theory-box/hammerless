"""Exact object handles must not collapse intentionally shared targetnames."""
import unittest
from hammerless.core.ir import Entity, MapIR
from hammerless.core.logic import _Compiler
from hammerless.core.gamefiles import game_files


class ObjectIdentityTests(unittest.TestCase):
    def test_shared_names_keep_io_but_register_distinct_handles(self):
        ir = MapIR()
        a = Entity('commentary_zombie_spawner', (0, 0, 0), (0, 0, 0),
                   {'targetname': 'zombie_spawner', 'vscripts': 'custom/first custom/second'})
        b = Entity('commentary_zombie_spawner', (0, 0, 0), (0, 0, 0),
                   {'targetname': 'ZOMBIE_SPAWNER'})
        ir.entities = [a, b]
        c = _Compiler(ir, [])
        ref_a, ref_b = c.object_handle(a, 'A'), c.object_handle(b, 'B')
        self.assertNotEqual(ref_a, ref_b)  # even colocated entities
        self.assertEqual(a.keyvalues['targetname'], 'zombie_spawner')
        self.assertEqual(b.keyvalues['targetname'], 'ZOMBIE_SPAWNER')
        self.assertTrue(a.keyvalues['vscripts'].endswith('custom/first custom/second'))
        self.assertEqual(_Compiler(ir, []).object_handle(a, 'A'), ref_a)
        self.assertEqual(len(ir.extra_scripts), 2)  # no repeated registrations across graphs
        files = game_files(ir)
        for e in (a, b):
            path = e.keyvalues['vscripts'].split()[0]
            self.assertIn(f'::HL_ObjectRefs["{path}"] <- self;', files[f'scripts/vscripts/{path}.nut'])

    def test_unique_or_unnamed_entities_keep_simple_lookup(self):
        ir = MapIR()
        e = Entity('info_target', (0, 0, 0), (0, 0, 0), {})
        ir.entities = [e]
        c = _Compiler(ir, [])
        self.assertIn('Entities.FindByName', c.object_handle(e, 'Object'))
        self.assertFalse(ir.extra_scripts)
        self.assertNotIn('vscripts', e.keyvalues)


if __name__ == '__main__':
    unittest.main()
