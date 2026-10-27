import unittest
import numpy as np
from hammerless.core import particlefields as fields
from hammerless.core.particlepreview import Simulation,audit
from hammerless.core.particlesheet import Sequence
from tests.unit.test_particlepreview import definition


class FieldTests(unittest.TestCase):
    def test_initializer_order_and_initial_multiplier(self):
        d=definition(initializers=[{'functionName':'Radius Random','radius_min':5,'radius_max':5},
            {'functionName':'Remap Initial Scalar','input field':3,'input minimum':0,'input maximum':10,
             'output minimum':1,'output maximum':3,'output is scalar of initial random range':True}])
        s=Simulation(d);s.spawn(2)
        np.testing.assert_allclose(s.radius,10)
        np.testing.assert_allclose(s.initial_fields[3],10)
        s.definition.rules['operators']=[{'functionName':'Remap Scalar','input field':8,'input maximum':1,
            'output field':3,'output minimum':2,'output maximum':2,'output is scalar of initial random range':True}]
        s.advance(.5);np.testing.assert_allclose(s.radius,20)
        s.advance(.5);np.testing.assert_allclose(s.radius,20)

    def test_range_gate_reversed_range_and_equal_endpoints(self):
        s=Simulation(definition());s.spawn(3);s.radius[:]=[-1,5,11]
        fields.remap(s,{'input field':3,'output field':7,'input minimum':10,'input maximum':0,
                       'only active within specified input range':True})
        np.testing.assert_allclose(s.color[:,3],[1,.5,1])
        np.testing.assert_allclose(fields.remap_fraction(np.array([0,1,2]),1,1),[0,1,1])

    def test_emitter_window_and_birth_time(self):
        d=definition(initializers=[{'functionName':'Remap Initial Scalar','output minimum':12,'output maximum':12,
                                   'emitter lifetime start time (seconds)':1,'emitter lifetime end time (seconds)':2}])
        s=Simulation(d);s.spawn(1);s.advance(1.5);s.spawn(1);s.advance(1);s.spawn(1)
        np.testing.assert_allclose(s.initial_radius,[5,12,5])
        np.testing.assert_allclose(s.birth,[0,1.5,2.5])
        fields.write(s,8,np.array([2.25]),slice(2,None))
        np.testing.assert_allclose(s.age[-1],.25)

    def test_vectors_angles_integer_clamps_and_removal(self):
        s=Simulation(definition());s.spawn(2)
        fields.remap(s,{'input field':3,'output field':4,'output minimum':90,'output maximum':90})
        np.testing.assert_allclose(s.rotation,np.pi/2)
        fields.remap(s,{'output field':6,'output minimum':(2,3,4),'output maximum':(2,3,4)},vector=True)
        np.testing.assert_allclose(s.color[:,:3],[(2,3,4)]*2)
        fields.write(s,16,[2,-1]);np.testing.assert_allclose(s.alpha2,[1,0])
        fields.write(s,9,[1.6,-10]);np.testing.assert_array_equal(s.sequence,[2,0])
        s.retain(np.array([False,True]))
        self.assertTrue(all(len(v)==1 for v in s.initial_fields.values()))
        with self.assertRaises(ValueError):fields.write(s,3,[float('nan')])

    def test_noise_remap_and_unknown_field_diagnostics(self):
        d=definition(initializers=[{'functionName':'Remap Noise to Scalar','output field':1,'output minimum':2,'output maximum':4}])
        s=Simulation(d);s.spawn(10)
        self.assertTrue(np.all((s.life>=2)&(s.life<=4)))
        d.rules['initializers'][0]['output field']=999
        self.assertIn('Remap Noise to Scalar: unsupported output field 999',audit({'test':d})['test']['recursive_gaps'])

    def test_lifetime_from_actual_sequence_frame_count(self):
        seq=Sequence(False,np.ones(8)*.1,np.zeros((8,1,4)))
        d=definition(initializers=[{'functionName':'Sequence Random','sequence_min':3,'sequence_max':3},
                                  {'functionName':'Lifetime From Sequence','Frames Per Second':4}])
        s=Simulation(d,sequences={3:seq});s.spawn(2)
        np.testing.assert_allclose(s.life,2);self.assertFalse(s.warnings)
        missing=Simulation(d);missing.spawn(1)
        self.assertIn('Lifetime From Sequence: missing sheet sequence 3',missing.warnings)
        d.rules['initializers'][-1]['Frames Per Second']=0
        invalid=Simulation(d,sequences={3:seq});invalid.spawn(1)
        self.assertTrue(invalid.warnings)

    def test_rotation_and_trail_initializers(self):
        d=definition(initializers=[{'functionName':'Rotation Speed Random','rotation_speed_constant':90},
             {'functionName':'Trail Length Random','length_min':.5,'length_max':.5}],operators=[{'functionName':'Rotation Basic'}])
        s=Simulation(d);s.spawn(3);s.advance(1)
        np.testing.assert_allclose(s.rotation,np.pi/2)
        np.testing.assert_allclose(s.trail,.5)

    def test_random_exponents_and_local_sphere_velocity(self):
        plain=definition(initializers=[{'functionName':'Radius Random','radius_min':0,'radius_max':10}])
        biased=definition(initializers=[{'functionName':'Radius Random','radius_min':0,'radius_max':10,'radius_random_exponent':2}])
        a,b=Simulation(plain,seed=5),Simulation(biased,seed=5);a.spawn(100);b.spawn(100)
        self.assertTrue(np.all(b.radius<=a.radius))
        d=definition(initializers=[{'functionName':'Position Within Sphere Random','speed_min':2,'speed_max':2,
            'distance_bias_absolute_value':(0,0,1),'distance_bias':(1,1,-1),
            'speed_in_local_coordinate_system_min':(10,0,0),'speed_in_local_coordinate_system_max':(10,0,0)}])
        s=Simulation(d);s.spawn(50)
        self.assertTrue(np.all(s.velocity[:,2]<=0))
        np.testing.assert_allclose(np.linalg.norm(s.velocity-[10,0,0],axis=1),2)

    def test_radius_bias_and_ease_endpoints(self):
        spec={'functionName':'Radius Scale','radius_start_scale':0,'radius_end_scale':2,'scale_bias':.25}
        s=Simulation(definition(operators=[spec]));s.spawn(1);s.advance(.5)
        np.testing.assert_allclose(s.radius,2.5)
        s.advance(.5);np.testing.assert_allclose(s.radius,10)
        spec['ease_in_and_out']=True
        s=Simulation(definition(operators=[spec]));s.spawn(1);s.advance(.5)
        np.testing.assert_allclose(s.radius,1.5625)
