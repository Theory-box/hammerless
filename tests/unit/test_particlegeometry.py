import unittest
import numpy as np
from hammerless.core.particlegeometry import build_geometry,trail_quad
from hammerless.core.particlepreview import Simulation
from hammerless.core.particlesheet import Sequence
from tests.unit.test_particlepreview import definition


def geometry(s,renderer=None,sequences=None):
    return build_geometry(s,renderer or {'functionName':'render_animated_sprites'},sequences or {},
                          np.eye(4),[1,0,0],[0,1,0],[0,0,10])


class GeometryTests(unittest.TestCase):
    def test_sprite_yaw_flip_secondary_alpha(self):
        s=Simulation(definition());s.spawn(1);s.radius[:]=1;s.alpha2[:]=.25
        batch,gaps=geometry(s);self.assertFalse(gaps)
        np.testing.assert_allclose(np.ptp(batch['pos'],axis=0),[2,2,0])
        np.testing.assert_allclose(batch['tint'][:,3],.25)
        s.yaw[:]=np.pi/2;s.flip[:]=True
        tilted,_=geometry(s)
        np.testing.assert_allclose(np.ptp(tilted['pos'],axis=0),[0,2,2],atol=1e-10)
        np.testing.assert_allclose(tilted['uv'][:,0],1-batch['uv'][:,0])

    def test_atlas_frame_and_missing_sequence(self):
        seq=Sequence(False,np.array([.5,.5]),np.array([[[0,0,.5,1]],[[.5,0,1,1]]]))
        s=Simulation(definition());s.spawn(1);s.age[:]=.75
        batch,_=geometry(s,sequences={0:seq})
        self.assertGreaterEqual(batch['uv'][:,0].min(),.5)
        s.sequence[:]=2
        batch,gaps=geometry(s,sequences={0:seq})
        self.assertEqual(len(batch['pos']),0);self.assertIn('missing sheet sequence 2',gaps)

    def test_trail_length_clamp_fade_and_stationary(self):
        s=Simulation(definition());s.spawn(1)
        renderer={'functionName':'render_sprite_trail','max length':2,'length fade in time':1}
        self.assertEqual(len(geometry(s,renderer)[0]['pos']),0)
        s.previous_position[:]=[-1,0,0];s.trail[:]=1;s.age[:]=.5
        batch,gaps=geometry(s,renderer)
        np.testing.assert_allclose(np.ptp(batch['pos'],axis=0),[1,1,0])
        self.assertFalse(gaps)
        s.previous_position[:]=s.position;s.velocity[:]=[60,0,0]
        self.assertEqual(len(geometry(s,renderer)[0]['pos']),6)

    def test_parallel_view_and_transform_are_finite(self):
        for head in ([0,0,1],[0,1,0],[1,0,0]):
            points=trail_quad(np.array(head,dtype=float),np.zeros(3),.1,np.array(head)*10,
                              np.array([1,0,0]),np.array([0,1,0]))
            self.assertTrue(np.isfinite(points).all())
            self.assertGreater(np.linalg.norm(points[1]-points[0]),.19)
        s=Simulation(definition());s.spawn(1);s.position[:]=[2,0,0]
        matrix=np.eye(4);matrix[:3,3]=[10,20,30]
        batch,_=build_geometry(s,{'functionName':'render_animated_sprites'},{},matrix,[1,0,0],[0,1,0],[0,0,10],2)
        np.testing.assert_allclose((batch['pos'].max(axis=0)+batch['pos'].min(axis=0))/2,[11,20,30])

    def test_ground_plane_and_upright_orientations(self):
        s=Simulation(definition());s.spawn(1);s.radius[:]=1
        flat,_=geometry(s,{'functionName':'render_animated_sprites','orientation_type':2})
        np.testing.assert_allclose(flat['pos'][:,2],0)
        upright,gaps=build_geometry(s,{'functionName':'render_animated_sprites','orientation_type':1},{},np.eye(4),
                                   [1,0,0],[0,0,1],[0,-10,10])
        self.assertFalse(gaps);np.testing.assert_allclose(upright['pos'][:,1],0)
        np.testing.assert_allclose(np.ptp(upright['pos'],axis=0),[2,0,2])
        # Flat particles remain in the CP plane when the camera moves.
        other,_=build_geometry(s,{'functionName':'render_animated_sprites','orientation_type':2},{},np.eye(4),
                              [0,1,0],[0,0,1],[10,0,10])
        np.testing.assert_allclose(flat['pos'],other['pos'])

    def test_orientation_control_point_rotation_and_missing(self):
        s=Simulation(definition());s.spawn(1)
        cp=np.eye(4);cp[:3,:3]=[[1,0,0],[0,0,-1],[0,1,0]];s.control_points.set(1,cp)
        batch,gaps=geometry(s,{'functionName':'render_animated_sprites','orientation_type':2,'orientation_control_point':1})
        np.testing.assert_allclose(batch['pos'][:,1],0,atol=1e-10);self.assertFalse(gaps)
        _,gaps=geometry(s,{'functionName':'render_animated_sprites','orientation_type':2,'orientation_control_point':9})
        self.assertIn('Missing control point 9',gaps)


class BatchedSpriteTests(unittest.TestCase):
    def test_batched_output_matches_scalar_cards(self):
        import inspect
        from hammerless.core import particlegeometry as module
        source=inspect.getsource(module.build_geometry)
        source=source.replace("    if kind=='render_animated_sprites':\n        return sprite_geometry(sim,renderer,sequences,transform,centers,right,up,eye,scale,material)\n",'')
        scope=dict(module.__dict__);exec(source,scope);reference=scope['build_geometry']
        rng=np.random.default_rng(12);sim=Simulation(definition());sim.spawn(40)
        sim.position[:]=rng.normal(size=(40,3));sim.age[:]=rng.uniform(0,4,40);sim.life[:]=3
        sim.rotation[:]=rng.uniform(-3,3,40);sim.yaw[:]=rng.uniform(-1,1,40);sim.flip[:]=rng.random(40)>.5
        sim.radius[:]=rng.uniform(.1,3,40);sim.sequence2[:]=1
        rects=np.array([[[0,0,.2,.5]],[[.2,0,.6,.5]],[[.6,.5,1,1]]])
        for clamp in (False,True):
            sheets={0:Sequence(clamp,np.array([.2,.3,.5]),rects),1:Sequence(clamp,np.array([.3,.4,.3]),rects)}
            for mode in (0,1,2):
                for fit,fps in ((False,False),(True,False),(False,True)):
                    renderer={'functionName':'render_animated_sprites','orientation_type':mode,'animation rate':2.3,'second sequence animation rate':.7,'animation_fit_lifetime':fit,'use animation rate as FPS':fps}
                    args=(sim,renderer,sheets,np.eye(4),np.array([1.,0,0]),np.array([0.,1,0]),np.array([0.,-10,8]))
                    material={'dual_mode':0,'blend_frames':True,'zoom2':2}
                    actual,gaps=build_geometry(*args,material=material);expected,old_gaps=reference(*args,material=material)
                    self.assertEqual(gaps,old_gaps)
                    for key in actual:np.testing.assert_allclose(actual[key],expected[key],atol=2e-6,err_msg=key)
