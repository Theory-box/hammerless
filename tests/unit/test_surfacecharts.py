import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from hammerless.core.ir import Polygon
from hammerless.core.vmf import VMFWriter
from hammerless.core.surfacecharts import chart_from_side
class SurfaceChartsTests(unittest.TestCase):
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
