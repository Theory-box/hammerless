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
        d.attrs['rotation_speed']=2
        self.assertIn('system: rotation_speed',audit({'test':d})['test']['recursive_gaps'])

    def test_child_cycles_and_missing_children(self):
        d=definition();d.children=[('test',0),('missing',0)]
        gaps=audit({'test':d})['test']['recursive_gaps']
        self.assertIn('child cycle: test',gaps)
        self.assertIn('missing child: missing',gaps)

    def test_invalid_time(self):
        s=Simulation(definition())
        for dt in [-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):s.advance(dt)

    def test_fades_and_identity_survive_removal(self):
        d=definition([{'functionName':'emit_instantaneously','num_to_emit':3}],
                     [{'functionName':'Lifetime Random','lifetime_min':2,'lifetime_max':2}],
                     [{'functionName':'Alpha Fade In Random','fade in time min':.5,'fade in time max':.5}])
        s=Simulation(d,seed=7);s.advance(.5)
        np.testing.assert_allclose(s.color[:,3],.5)
        samples=s.stable_random().copy()
        s.retain(np.array([False,True,True]))
        np.testing.assert_array_equal(s.stable_random(),samples[1:])
        s.advance(.5);np.testing.assert_allclose(s.color[:,3],1)

    def test_sequence_and_rotation_are_per_particle(self):
        d=definition([{'functionName':'emit_instantaneously','num_to_emit':64}],
                     [{'functionName':'Sequence Random','sequence_min':2,'sequence_max':4},
                      {'functionName':'Rotation Random','rotation_offset_min':90,'rotation_offset_max':90}])
        s=Simulation(d);s.advance(.02)
        self.assertEqual(set(s.sequence),{2,3,4})
        np.testing.assert_allclose(s.rotation,np.pi/2)

    def test_fade_out_is_duration_before_death(self):
        for proportional,duration in [(True,.25),(False,.5)]:
            d=definition(initializers=[{'functionName':'Lifetime Random','lifetime_min':2,'lifetime_max':2}],
                operators=[{'functionName':'Alpha Fade Out Random','fade out time min':duration,
                            'fade out time max':duration,'proportional 0/1':proportional}])
            s=Simulation(d);s.spawn(1);s.advance(1.5)
            np.testing.assert_allclose(s.color[:,3],1)
            s.advance(.25);np.testing.assert_allclose(s.color[:,3],.5)
            s.advance(.25);np.testing.assert_allclose(s.color[:,3],0,atol=1e-10)

    def test_combined_fade_reclaims_capacity(self):
        d=definition([{'functionName':'emit_continuously','emission_rate':60}],
            operators=[{'functionName':'Alpha Fade and Decay','end_fade_in_time':.25,'start_fade_out_time':.75}])
        s=Simulation(d,limit=8);s.advance(3)
        self.assertGreater(s.next_id,8)
        self.assertLessEqual(len(s.age),8)
        self.assertTrue(np.all((s.color[:,3]>=0)&(s.color[:,3]<=1)))
        self.assertTrue(np.all(s.age<1))

    def test_spin_integrates_across_stop_time(self):
        d=definition(operators=[{'functionName':'Rotation Spin Roll','spin_rate_degrees':90,
                                'spin_rate_min':10,'spin_stop_time':.3}])
        s=Simulation(d);s.spawn(1);s.advance(1)
        np.testing.assert_allclose(s.rotation,np.deg2rad(22))
        s.advance(1);np.testing.assert_allclose(s.rotation,np.deg2rad(32))

    def test_forces_need_movement_and_preserve_emission_random_stream(self):
        d=definition(initializers=[{'functionName':'Position Within Box Random','min':(-1,-1,-1),'max':(1,1,1)}])
        d.rules['forces']=[{'functionName':'random force','min force':(6,0,0),'max force':(6,0,0)}]
        s=Simulation(d);s.spawn(1);original=s.position.copy();s.advance(.5)
        np.testing.assert_allclose(s.position,original)
        d.rules['operators']=[{'functionName':'Movement Basic'}]
        s.advance(.5);np.testing.assert_allclose(s.velocity,[(3,0,0)])
        a,b=Simulation(d),Simulation(d);a.spawn(1);b.spawn(1)
        a.advance(.5);a.spawn(1);b.spawn(1)
        np.testing.assert_allclose(a.position[-1],b.position[-1])

    def test_mirror_state_survives_death(self):
        for probability in (0,1):
            d=definition(initializers=[{'functionName':'Rotation Yaw Flip Random','Flip Percentage':probability}])
            s=Simulation(d);s.spawn(3)
            s.retain(np.array([False,True,True]))
            np.testing.assert_array_equal(s.flip,np.full(2,bool(probability)))
