import unittest
import numpy as np
from hammerless.core.particlepreview import Simulation,audit
from tests.unit.test_particlepreview import definition
from tests.unit.test_particlegeometry import geometry

class RopeTests(unittest.TestCase):
    def sim(self):
        s=Simulation(definition());s.spawn(3)
        s.position[:]=[[0,0,0],[1,1,0],[2,0,0]];s.radius[:]=.2
        return s

    def test_topology_independent_of_camera_and_storage_order(self):
        s=self.sim();r={'functionName':'render_rope','subdivision_count':3}
        batch,gaps=geometry(s,r);self.assertFalse(gaps);self.assertEqual(len(batch['pos']),36)
        # Reorder every retained particle field; ids must restore birth topology.
        s.retain(np.array([2,0,1]));other,_=geometry(s,r)
        np.testing.assert_allclose(batch['pos'],other['pos'])
        np.testing.assert_allclose(batch['uv'],other['uv'])

    def test_width_color_scroll_and_dead_particles(self):
        s=self.sim();s.radius[:]=[.1,.2,.3];s.alpha2[:]=.5
        r={'functionName':'render_rope','texel_size':2,'texture_scroll_rate':3}
        a,_=geometry(s,r);s.time=2;b,_=geometry(s,r)
        np.testing.assert_allclose(b['uv'][:,1]-a['uv'][:,1],3)
        np.testing.assert_allclose(b['tint'][:,3],.5)
        self.assertAlmostEqual(np.linalg.norm(a['pos'][1]-a['pos'][0]),.2)
        s.retain(np.array([True,False,True]));self.assertEqual(len(geometry(s,r)[0]['pos']),6)

    def test_coincident_and_bounded_subdivision(self):
        s=self.sim();s.position[:]=0
        self.assertEqual(len(geometry(s,{'functionName':'render_rope'})[0]['pos']),0)
        s.position[:,2]=[0,1,2]
        b,g=geometry(s,{'functionName':'render_rope','subdivision_count':10000})
        self.assertEqual(len(b['pos']),192);self.assertTrue(g)
        self.assertTrue(np.isfinite(b['pos']).all())
        self.assertEqual(b['pos'].dtype,np.float32)

    def test_secondary_sequences_survive_spawn_and_removal(self):
        d=definition(initializers=[{'functionName':'Sequence Two Random','sequence_min':5,'sequence_max':9}])
        s=Simulation(d,seed=12);s.spawn(40)
        self.assertTrue(np.all((s.sequence2>=5)&(s.sequence2<=9)))
        before=s.sequence2.copy();s.retain(s.ids%2==0)
        np.testing.assert_array_equal(s.sequence2,before[::2])
        s.spawn(3);self.assertEqual(len(s.sequence2),len(s.age))
        self.assertTrue(any('supported material profile' in w for w in s.warnings))
        self.assertIn('material: dual-sequence blending requires supported material profile',audit({'test':d})['test']['recursive_gaps'])

    def test_texture_density_scales_with_texture_height(self):
        from hammerless.core.particlegeometry import build_geometry
        s=self.sim();r={'functionName':'render_rope'}
        a,_=geometry(s,r)
        b,_=build_geometry(s,r,{},np.eye(4),[1,0,0],[0,1,0],[0,0,10],texture_height=8)
        np.testing.assert_allclose(a['uv'][:,1]/8,b['uv'][:,1])
