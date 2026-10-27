import unittest
import numpy as np
from hammerless.core.particlepreview import Simulation, audit
from tests.unit.test_particlepreview import definition


def oscillator(vector=False, **options):
    spec={'functionName':'Oscillate Vector' if vector else 'Oscillate Scalar',
          'oscillation field':0 if vector else 3,
          'oscillation rate min':(1,2,3) if vector else 1,
          'oscillation rate max':(1,2,3) if vector else 1,
          'oscillation frequency min':0,'oscillation frequency max':0,
          'proportional 0/1':False,'start/end proportional':False}
    spec.update(options)
    return spec


class OscillationTests(unittest.TestCase):
    def test_zero_frequency_and_time_window(self):
        spec=oscillator(**{'start time min':.2,'start time max':.2,'end time min':.4,'end time max':.4})
        s=Simulation(definition(operators=[spec]));s.spawn(1)
        s.advance(.1);np.testing.assert_allclose(s.radius,5)
        s.advance(.2);np.testing.assert_allclose(s.radius,5.1)
        s.advance(.7);np.testing.assert_allclose(s.radius,5.2)

    def test_vector_offsets_do_not_accumulate_twice(self):
        s=Simulation(definition(operators=[oscillator(True)]));s.spawn(1)
        s.advance(.5);np.testing.assert_allclose(s.position,[(.5,1,1.5)])
        s.advance(1);np.testing.assert_allclose(s.position,[(1,2,3)])

    def test_full_cycle_and_proportional_frequency(self):
        spec=oscillator(**{'oscillation frequency min':1,'oscillation frequency max':1,
                           'proportional 0/1':True,'start/end proportional':True})
        s=Simulation(definition(initializers=[{'functionName':'Lifetime Random','lifetime_min':2,'lifetime_max':2}],operators=[spec]))
        s.spawn(1);s.advance(.5)
        np.testing.assert_allclose(s.radius,5+1/np.pi)
        s.advance(1.5);np.testing.assert_allclose(s.radius,5,atol=1e-10)

    def test_death_preserves_samples_and_frame_partition(self):
        spec=oscillator(True,**{'oscillation frequency min':(.1,.2,.3),
                                'oscillation frequency max':(2,3,4),
                                'oscillation rate min':(-1,-2,-3)})
        a,b=Simulation(definition(operators=[spec]),seed=7),Simulation(definition(operators=[spec]),seed=7)
        a.spawn(3);b.spawn(3);a.advance(.25);b.advance(.25)
        a.retain(np.array([False,True,True]))
        a.advance(.5)
        for _ in range(5):b.advance(.1)
        np.testing.assert_allclose(a.position,b.position[1:])

    def test_color_clamps_and_unimplemented_fields_are_reported(self):
        spec=oscillator(True,**{'oscillation field':6,'oscillation rate min':(-10,10,0),'oscillation rate max':(-10,10,0)})
        s=Simulation(definition(operators=[spec]));s.spawn(1);s.advance(.5)
        np.testing.assert_allclose(s.color[0,:3],(0,1,1))
        for field in (1,999):
            d=definition(operators=[oscillator(**{'oscillation field':field})])
            self.assertIn(f'Oscillate Scalar: unsupported oscillation field {field}',audit({'test':d})['test']['recursive_gaps'])
            s=Simulation(d);s.spawn(1);s.advance(.1)
            np.testing.assert_allclose(s.radius,5)

    def test_empty_simulation(self):
        s=Simulation(definition(operators=[oscillator(),oscillator(True)]))
        s.advance(.1)
        self.assertEqual(len(s.age),0)
