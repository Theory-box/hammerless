import unittest
import numpy as np
from hammerless.core.particlepreview import Simulation
from hammerless.core.particleeffect import EffectSimulation
from hammerless.core.particlecontrol import ControlPoints
from tests.unit.test_particlepreview import definition


def transform(x=0,y=0,z=0,angle=0):
    m=np.eye(4);c=np.cos(angle);s=np.sin(angle)
    m[:3,:3]=[[c,-s,0],[s,c,0],[0,0,1]];m[:3,3]=[x,y,z]
    return m


def burst(operators=None,initializers=None):
    return definition([{'functionName':'emit_instantaneously','num_to_emit':1}],
        [{'functionName':'Lifetime Random','lifetime_min':10,'lifetime_max':10}]+(initializers or []),operators)


class ControlPointTests(unittest.TestCase):
    def test_free_particles_stay_while_locked_follow(self):
        free=EffectSimulation({'root':burst()},'root')
        locked=EffectSimulation({'root':burst([{'functionName':'Movement Lock to Control Point'}])},'root')
        for effect in (free,locked):effect.advance(1/60);effect.advance(1,{0:transform(x=6)})
        np.testing.assert_allclose(free.systems[0].position,[[0,0,0]])
        np.testing.assert_allclose(locked.systems[0].position,[[6,0,0]])

    def test_new_births_not_double_translated_and_inherit_speed(self):
        d=definition([{'functionName':'emit_continuously','emission_rate':60}],
            [{'functionName':'Velocity Inherit from Control Point','velocity scale':.5}],
            [{'functionName':'Movement Lock to Control Point'}])
        effect=EffectSimulation({'root':d},'root')
        effect.advance(.5,{0:transform(x=30)})
        sim=effect.systems[0]
        np.testing.assert_allclose(sim.position[:,0],30)
        np.testing.assert_allclose(sim.velocity[:,0],30)
        np.testing.assert_allclose(sim.position[:,1:],0)

    def test_partition_invariance_of_moving_control_point(self):
        d=definition([{'functionName':'emit_continuously','emission_rate':60}],
            [{'functionName':'Velocity Inherit from Control Point'}],[{'functionName':'Movement Basic'}])
        a,b=EffectSimulation({'root':d},'root'),EffectSimulation({'root':d},'root')
        a.advance(1,{0:transform(x=60)})
        for i in range(10):b.advance(.1,{0:transform(x=(i+1)*6)})
        np.testing.assert_allclose(a.systems[0].position,b.systems[0].position,atol=1e-9)
        np.testing.assert_allclose(a.systems[0].velocity,b.systems[0].velocity,atol=1e-9)

    def test_rotation_and_lock_fade(self):
        d=burst([{'functionName':'Movement Lock to Control Point','lock rotation':True}],
                [{'functionName':'Position Within Box Random','min':(2,0,0),'max':(2,0,0)}])
        e=EffectSimulation({'root':d},'root');e.advance(1/60);e.advance(.5,{0:transform(angle=np.pi/2)})
        np.testing.assert_allclose(e.systems[0].position,[[0,2,0]],atol=1e-8)
        d.rules['operators'][0].update(start_fadeout_min=0,start_fadeout_max=0,end_fadeout_min=0,end_fadeout_max=0)
        e=EffectSimulation({'root':d},'root');e.advance(1/60);e.advance(.5,{0:transform(x=20)})
        np.testing.assert_allclose(e.systems[0].position,[[2,0,0]])

    def test_static_cp_defined_before_spawn_and_linear_path(self):
        d=definition([{'functionName':'emit_instantaneously','num_to_emit':3}],
            [{'functionName':'Position Along Path Sequential','start control point number':1,'end control point number':2,
              'particles to map from start to end':3}],
            [{'functionName':'Set Control Point Positions','First Control Point Location':(10,0,0),
              'Second Control Point Location':(20,0,0)}])
        e=EffectSimulation({'root':d},'root');e.advance(1/60)
        np.testing.assert_allclose(e.systems[0].position,[[10,0,0],[15,0,0],[20,0,0]])

    def test_parent_inheritance_delays_and_missing_parent(self):
        root=burst([{'functionName':'Movement Basic'}],[{'functionName':'Velocity Random',
            'speed_in_local_coordinate_system_min':(6,0,0),'speed_in_local_coordinate_system_max':(6,0,0)}])
        root.children=[('child',.5)]
        child=burst(initializers=[{'functionName':'Position From Parent Particles','Inherited Velocity Scale':.5}])
        e=EffectSimulation({'root':root,'child':child},'root');e.advance(.5)
        self.assertEqual(len(e.systems[1].age),0)
        e.advance(1/60)
        np.testing.assert_allclose(e.systems[1].position,e.systems[0].position)
        np.testing.assert_allclose(e.systems[1].velocity,[[3,0,0]])
        orphan=Simulation(child);orphan.advance(.1)
        self.assertEqual(len(orphan.age),0);self.assertTrue(orphan.warnings)

    def test_child_point_mapping_and_instance_isolation(self):
        root=burst([{'functionName':'Set child control points from particle positions','First control point to set':1}],
            [{'functionName':'Position Within Box Random','min':(8,0,0),'max':(8,0,0)}]);root.children=[('child',0)]
        child=burst(initializers=[{'functionName':'Position Within Sphere Random','control_point_number':1}])
        a,b=EffectSimulation({'root':root,'child':child},'root'),EffectSimulation({'root':root,'child':child},'root')
        a.advance(1/60)
        np.testing.assert_allclose(a.systems[1].position,[[8,0,0]])
        self.assertNotIn(1,b.systems[1].control_points.current)
        self.assertEqual(len(b.systems[0].age),0)

    def test_missing_cp_invalid_data_cycles_and_bounds(self):
        s=Simulation(burst(initializers=[{'functionName':'Velocity Inherit from Control Point','control point number':9}]))
        s.advance(.1);self.assertIn('Missing control point 9',s.warnings)
        cp=ControlPoints()
        for index in (-1,64):
            with self.assertRaises(ValueError):cp.set(index,np.eye(4))
        with self.assertRaises(ValueError):cp.set(0,np.full((4,4),float('nan')))
        d=burst();d.children=[('root',0),('missing',0)]
        e=EffectSimulation({'root':d},'root')
        self.assertEqual(len(e.systems),1);self.assertEqual(len(e.warnings),2)

    def test_center_and_pull_force_stay_finite_at_target(self):
        d=burst([{'functionName':'Movement Basic'},{'functionName':"Set Control Point To Particles' Center",'Control Point Number to Set':2}])
        d.rules['forces']=[{'functionName':'Pull towards control point','amount of force':10,'falloff power':2}]
        e=EffectSimulation({'root':d},'root');e.advance(.1)
        self.assertTrue(np.isfinite(e.systems[0].position).all())
        np.testing.assert_allclose(e.systems[0].control_points.current[2][:3,3],0)
