import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hammerless.core.ir import Polygon
from hammerless.core.vmf import VMFWriter
from hammerless.core.surfacecharts import chart_from_side,chart_triangle_mask
class SurfaceChartsTests(unittest.TestCase):
 def test_halfspaces_match_cross_product_reference(self):
  rng=np.random.default_rng(812)
  for count in (3,4,7,16):
   for offset in (np.zeros(3),np.array([1e8,-2e8,3e8])):
    # Ordered points on an ellipse stay convex under every affine transform.
    angles=np.sort(rng.uniform(0,2*np.pi,count))
    rotation,_=np.linalg.qr(rng.normal(size=(3,3)))
    vertices=np.column_stack((120*np.cos(angles),70*np.sin(angles),np.zeros(count)))@rotation.T+offset
    side=VMFWriter().side(Polygon(vertices.tolist(),lightmap_scale=16))
    chart=chart_from_side(side,vertices)
    local=vertices-vertices[0]
    reference_normal=np.cross(local,np.roll(local,-1,axis=0)).sum(axis=0)
    reference_normal/=np.linalg.norm(reference_normal)
    np.testing.assert_allclose(chart.normal,reference_normal,atol=1e-14)
    # Mix interior triangles, boundary triangles, and plane/facing violations.
    weights=rng.uniform(size=(40,3,count));weights/=weights.sum(axis=2,keepdims=True)
    triangles=weights@local+vertices[0]
    triangles=np.concatenate((triangles,vertices[[0,1,2]][None],triangles[:10]+chart.normal))
    normals=np.tile(chart.normal,(len(triangles),1));normals[::7]*=-1
    relative=triangles-chart.polygon[0]
    expected=(normals@chart.normal>.9999)&np.all(abs(relative@chart.normal)<=.005,axis=1)
    for a,b in zip(chart.polygon,np.roll(chart.polygon,-1,axis=0)):
     edge=b-a
     expected &= np.all(np.cross(edge/np.linalg.norm(edge),relative-(a-chart.polygon[0]))@chart.normal>=-.005,axis=1)
    np.testing.assert_array_equal(chart_triangle_mask(chart,triangles,normals),expected)
    y,x=np.mgrid[:chart.h,:chart.w]
    points=chart.to_world(np.column_stack((x.ravel(),y.ravel())))
    inside=np.ones(len(points),dtype=bool);closest=points.copy();best=np.full(len(points),np.inf)
    for a,b in zip(chart.polygon,np.roll(chart.polygon,-1,axis=0)):
     edge=b-a
     inside &= np.cross(edge,points-a)@chart.normal>=-1e-6
     t=np.clip((points-a)@edge/np.dot(edge,edge),0,1)
     q=a+t[:,None]*edge;distance=np.sum((points-q)**2,axis=1)
     take=distance<best;closest[take]=q[take];best[take]=distance[take]
    points[~inside]=closest[~inside]
    np.testing.assert_allclose(chart.samples(),points,rtol=0,atol=1e-8)
 def test_concave_and_degenerate_edges_remain_rejected(self):
  vertices=[(0,0,0),(128,0,0),(32,32,0),(0,128,0)]
  with self.assertRaisesRegex(ValueError,'convex'):
   chart_from_side(VMFWriter().side(Polygon(vertices)),vertices)
  vertices=[(0,0,0),(128,0,0),(128,128,0),(0,128,0)]
  chart=chart_from_side(VMFWriter().side(Polygon(vertices)),vertices)
  chart.polygon=np.insert(chart.polygon,1,chart.polygon[0],axis=0)
  np.testing.assert_array_equal(chart_triangle_mask(chart,np.array(vertices[:3])[None],[[0,0,1]]),[False])
 def test_matching_requires_whole_same_facing_triangle(self):
  vertices=[(0,0,0),(128,0,0),(128,128,0),(0,128,0)]
  chart=chart_from_side(VMFWriter().side(Polygon(vertices)),vertices)
  triangles=np.array([[(0,0,0),(128,0,0),(0,128,0)],
                      [(0,0,0),(129,0,0),(0,128,0)],
                      [(0,0,1),(128,0,1),(0,128,1)],
                      [(0,0,0),(128,0,0),(0,128,0)]])
  normals=np.array([(0,0,1)]*3+[(0,0,-1)])
  np.testing.assert_array_equal(chart_triangle_mask(chart,triangles,normals),[True,False,False,False])
  offset=np.array([20000,-10000,8000]);chart.polygon+=offset
  np.testing.assert_array_equal(chart_triangle_mask(chart,triangles+offset,normals),[True,False,False,False])
 def test_texture_axes_are_independent_of_lighting_density(self):
  vertices=[(0,0,0),(128,0,0),(128,128,0),(0,128,0)]
  axes=(((1,0,0),17,.25),((0,-1,0),-31,.5))
  uv=[]
  for scale in (4,32):
   chart=chart_from_side(VMFWriter().side(Polygon(vertices,'custom/test',tex_axes=axes,lightmap_scale=scale)),vertices)
   self.assertEqual(chart.material_path,'custom/test')
   actual=chart.texture_uv((256,128));uv.append(actual)
   np.testing.assert_allclose(actual[:,0],(chart.positions[:,0]*4+17)/256)
   np.testing.assert_allclose(actual[:,1],1-(-chart.positions[:,1]*2-31)/128)
   with self.assertRaises(ValueError):chart.texture_uv((0,128))
  np.testing.assert_array_equal(uv[0],uv[1])
 def test_density_uses_exported_scale(self):
  vertices=[(0,0,0),(128,0,0),(128,128,0),(0,128,0)]
  counts=[]
  for scale in (4,8,16,32):
   side=VMFWriter().side(Polygon(vertices,lightmap_scale=scale))
   chart=chart_from_side(side,vertices)
   self.assertEqual((chart.w,chart.h),(128//scale+1,128//scale+1))
   coords=np.array([[.5,.25],[2.5,3.75]])
   np.testing.assert_allclose(chart.to_luxel(chart.to_world(coords)),coords,atol=1e-9)
   counts.append(len(chart.samples()))
  self.assertEqual(counts,[1089,289,81,25])
 def test_slanted_shifted_axes(self):
  vertices=[(0,0,0),(128,0,64),(128,128,64),(0,128,0)]
  axes=(((1,0,0),17,.25),((0,1,0),-31,.5))
  chart=chart_from_side(VMFWriter().side(Polygon(vertices,tex_axes=axes,lightmap_scale=8)),vertices)
  p=chart.samples()
  self.assertLess(np.max(abs((p-p[0])@chart.normal)),1e-8)
  self.assertTrue(np.all((p[:,0]>=-1e-8)&(p[:,0]<=128+1e-8)))
 def test_triangle_margin_stays_on_surface(self):
  vertices=[(0,0,0),(128,0,0),(0,128,0)]
  chart=chart_from_side(VMFWriter().side(Polygon(vertices)),vertices)
  p=chart.samples()
  self.assertTrue(np.all(p[:,0]+p[:,1]<=128+1e-8))
 def test_budget_rejects_instead_of_silently_coarsening(self):
  vertices=[(0,0,0),(128,0,0),(128,128,0),(0,128,0)]
  with self.assertRaises(ValueError):chart_from_side(VMFWriter().side(Polygon(vertices)),vertices,max_samples=4)
if __name__=='__main__':unittest.main()
