import unittest
import numpy as np
from hammerless.core.particlepreview import Definition, Simulation, CATEGORIES, audit

def definition(emitters=None, initializers=None, operators=None):
    rules={c:[] for c in CATEGORIES}
    rules.update(emitters=emitters or [],initializers=initializers or [],operators=operators or [])
    return Definition('test','test.pcf',{},rules)

class ParticlePreviewTests(unittest.TestCase):
    def test_fractional_emission_and_partition_invariance(self):
        d=definition([{'functionName':'emit_continuously','emission_rate':2.5}],
                     [{'functionName':'Position Within Sphere Random','distance_max':10}])
        a,b=Simulation(d,seed=42),Simulation(d,seed=42)
        a.advance(2)
        for i in range(120):b.advance(1/60)
        self.assertEqual(len(a.age),5)
        np.testing.assert_allclose(a.position,b.position)
        np.testing.assert_allclose(a.age,b.age)

    def test_burst_once_and_capacity(self):
        s=Simulation(definition([{'functionName':'emit_instantaneously','num_to_emit':1000}]),limit=12)
        s.advance(.1);self.assertEqual(len(s.age),12)
        s.retain(np.zeros(12,dtype=bool));s.advance(.1)
        self.assertEqual(len(s.age),0)

    def test_delayed_emission_and_expiry(self):
        d=definition([{'functionName':'emit_instantaneously','num_to_emit':3,'emission_start_time':1}],
                     [{'functionName':'Lifetime Random','lifetime_min':.1,'lifetime_max':.1}],
                     [{'functionName':'Lifespan Decay'}])
        s=Simulation(d);s.advance(.5);self.assertEqual(len(s.age),0)
        s.advance(.52);self.assertEqual(len(s.age),3)
        s.advance(.2);self.assertEqual(len(s.age),0)

    def test_instances_and_unknown_rules(self):
        d=definition([{'functionName':'emit_continuously','emission_rate':60,'unknown_parameter':7}],
                     operators=[{'functionName':'unknown_rule'}])
        a,b=Simulation(d),Simulation(d);a.advance(.1)
        self.assertEqual(len(b.age),0)
        report=audit({'test':d})['test']
        self.assertIn('operators: unknown_rule',report['missing_rules'])
        self.assertIn('emit_continuously: unknown_parameter',report['unmapped_parameters'])

    def test_child_cycles_and_missing_children(self):
        d=definition();d.children=[('test',0),('missing',0)]
        gaps=audit({'test':d})['test']['recursive_gaps']
        self.assertIn('child cycle: test',gaps)
        self.assertIn('missing child: missing',gaps)

    def test_invalid_time(self):
        s=Simulation(definition())
        for dt in [-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):s.advance(dt)
