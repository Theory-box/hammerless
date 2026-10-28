import unittest
from hammerless.core.overlays import project, resolve, stop_at_surface
from hammerless.core.ir import Entity, Brush, Polygon, MapIR, MapSettings
from hammerless.core.vmf import VMFWriter, Block, parse
from hammerless.core.build import build_vmf, Report
from hammerless.core.geometry import box_brush as box


def settings(**kw):
    d=dict(material="decals/test", BasisOrigin="0 0 0",BasisU="1 0 0",BasisV="0 1 0",BasisNormal="0 0 1",
           StartU="0",EndU="1",StartV="0",EndV="1",RenderOrder="0",_hl_overlay_width="8",_hl_overlay_height="8",_hl_overlay_depth="4")
    for i,p in enumerate(("-4 -4 0","-4 4 0","4 4 0","4 -4 0")):d["uv"+str(i)]=p
    d.update(kw);return d

class OverlayTests(unittest.TestCase):
    def test_clip_and_uv(self):
        p=project(settings(),[(-10,-10,0),(10,-10,0),(10,10,0),(-10,10,0)])
        self.assertEqual(len(p),4)
        self.assertEqual({uv for _,uv in p},{(0,0),(1,0),(1,1),(0,1)})
        self.assertTrue(all(abs(v[0])<=4 and abs(v[1])<=4 for v,_ in p))
    def test_backface_and_tools(self):
        face=[(-3,-3,0),(3,-3,0),(3,3,0),(-3,3,0)]
        self.assertFalse(project(settings(),face[::-1]))
        self.assertFalse(project(settings(),face,"tools/toolsnodraw"))
    def test_depth_selects_intersecting_face_without_trimming(self):
        face=[(-4,-4,0),(4,-4,4),(4,4,4),(-4,4,0)]
        self.assertEqual(len(project(settings(),face)),4)
        self.assertEqual(len(project(settings(_hl_overlay_depth="10"),face)),4)
        self.assertFalse(project(settings(),[(x,y,z+10) for x,y,z in face]))
    def test_tilted_default_overlay_keeps_floor(self):
        import math
        a=math.radians(20);c=math.cos(a);s=math.sin(a)
        kv=settings(BasisU=f"{c} 0 {-s}",BasisNormal=f"{s} 0 {c}",
                    _hl_overlay_width="64",_hl_overlay_height="64",_hl_overlay_depth="16")
        face=[(-100,-100,0),(100,-100,0),(100,100,0),(-100,100,0)]
        result=project(kv,face)
        self.assertEqual(len(result),4)
        self.assertTrue(all(abs(p[2])<1e-6 for p,uv in result))

    def test_projection_starts_at_emitter_not_above_it(self):
        floor=[(-4,-4,-60),(4,-4,-60),(4,4,-60),(-4,4,-60)]
        kv=settings(_hl_overlay_depth="1300")
        self.assertTrue(project(kv,floor))
        for dz in (200,320):
            self.assertFalse(project(kv,[(x,y,z+dz) for x,y,z in floor]))
        self.assertFalse(project(settings(_hl_overlay_depth="16"),floor))

    def test_touching_edge_is_not_receiver(self):
        self.assertFalse(project(settings(),[(4,-3,0),(8,-3,0),(8,3,0),(4,3,0)]))
    def test_object_restriction(self):
        face=[(-3,-3,0),(3,-3,0),(3,3,0),(-3,3,0)]
        kv=settings(_hl_overlay_receivers='["Wall"]')
        self.assertTrue(project(kv,face,source="Wall (part 2)"))
        self.assertFalse(project(kv,face,source="Wall Other"))
    def test_sloped_face_preserves_world_geometry(self):
        face=[(-4,-4,-1),(4,-4,1),(4,4,1),(-4,4,-1)]
        p=project(settings(),face)
        self.assertEqual(len(p),4)
        self.assertTrue(all(abs(v[2]-v[0]/4)<1e-6 for v,_ in p))
    def test_final_ids_after_multiple_brushes_and_rebuild(self):
        for detail in ("WORLD","DETAIL"):
            ir=MapIR(settings=MapSettings(auto_seal=False,auto_director=False,auto_light_environment=False))
            for lo,hi in (((-8,-8,-8),(0,8,0)),((0,-8,-8),(8,8,0))):
                b=box(lo,hi,"brick/test");b.detail=detail;ir.brushes.append(b)
            ir.entities.append(Entity("info_overlay",(0,0,0),keyvalues=settings()))
            text,report=build_vmf(ir)
            self.assertTrue(text,report.errors)
            blocks=parse(text);overlay=next(b for b in blocks if b.get("classname")=="info_overlay")
            ids=overlay.get("sides").split();self.assertEqual(len(ids),2)
            valid={side.get("id") for owner in blocks for solid in owner.blocks("solid") for side in solid.blocks("side")}
            self.assertTrue(set(ids)<=valid)
            self.assertNotIn("_hl_overlay_",text)
    def test_no_receivers_is_clear_export_error(self):
        ir=MapIR(settings=MapSettings(auto_seal=False))
        ir.entities.append(Entity("info_overlay",(0,0,0),keyvalues=settings()))
        text,report=build_vmf(ir)
        self.assertIsNone(text);self.assertTrue(any("no receiving" in e for e in report.errors))
    def test_stop_at_surface_layers_and_toggle(self):
        kv=settings(_hl_overlay_depth="500")
        def face(z,x0=-4,x1=4):return [(x0,-4,z),(x1,-4,z),(x1,4,z),(x0,4,z)]
        hits=[(str(z),project(kv,face(z))) for z in (-10,-200,-400)]
        self.assertEqual([i for i,p in stop_at_surface(kv,hits)],["-10"])
        self.assertEqual(len(stop_at_surface(dict(kv,_hl_overlay_stop="0"),hits)),3)
        # A seam on the upper floor must not let the lower floor through.
        hits=[('left',project(kv,face(-10,-4,0))),('right',project(kv,face(-10,0,4))),('below',project(kv,face(-200)))]
        self.assertEqual([i for i,p in stop_at_surface(kv,hits)],['left','right'])
        # Side-by-side steps do not occlude one another.
        hits=[('left',project(kv,face(-10,-4,0))),('right',project(kv,face(-20,0,4)))]
        self.assertEqual(len(stop_at_surface(kv,hits)),2)
        # Native overlays select whole faces; partially blocked faces are excluded.
        hits=[('upper',project(kv,face(-10,-4,0))),('lower',project(kv,face(-20)))]
        self.assertEqual([i for i,p in stop_at_surface(kv,hits)],['upper'])

    def test_stop_at_surface_export(self):
        for enabled,expected in (("1",1),("0",2)):
            ir=MapIR(settings=MapSettings(auto_seal=False,auto_director=False,auto_light_environment=False))
            ir.brushes=[box((-8,-8,-20),(8,8,-10),'brick/test'),box((-8,-8,-210),(8,8,-200),'brick/test')]
            ir.entities.append(Entity('info_overlay',(0,0,0),keyvalues=settings(_hl_overlay_depth="500",_hl_overlay_stop=enabled)))
            text,rep=build_vmf(ir)
            self.assertTrue(text,rep.errors)
            overlay=next(b for b in parse(text) if b.get('classname')=='info_overlay')
            self.assertEqual(len(overlay.get('sides').split()),expected)
            self.assertNotIn('_hl_overlay_stop',text)

    def test_invalid_axes_rejected(self):
        with self.assertRaises(ValueError):project(settings(BasisU="1 1 0"),[(-3,-3,0),(3,-3,0),(3,3,0)])

if __name__=="__main__":unittest.main()

