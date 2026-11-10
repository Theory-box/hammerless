"""CPU-only NativeView invalidation checks; no Blender, GPU or subprocess."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
class Client:
    def __init__(self):self.requests=[];self.pauses=0
    def pause(self,owner):self.pauses+=1
    def poll(self,owner,token):return None
    def submit(self,*args):self.requests.append(args);return True
    def release(self,owner):pass

class InvalidationTests(unittest.TestCase):
    def setUp(self):
        self.client=Client()
        pkg=types.ModuleType('nativeview_test');pkg.__path__=[]
        gpu=types.ModuleType('gpu')
        self.viewport=[0,0,100,60]
        gpu.state=types.SimpleNamespace(viewport_get=lambda:tuple(self.viewport))
        transport=types.ModuleType('nativeview_test.previewnative')
        transport.get_client=lambda:self.client
        transport.MAX_POINTS=8_000_000
        history=types.ModuleType('nativeview_test.previewhistory');history.FrameHistory=object
        draw=types.ModuleType('nativeview_test.l4d2preview');draw.draw_depth=lambda *a:None
        extras=types.ModuleType('gpu_extras');extras.__path__=[]
        batch=types.ModuleType('gpu_extras.batch');batch.batch_for_shader=lambda *a:None
        self.modules=patch.dict(sys.modules,{'nativeview_test':pkg,
            'nativeview_test.previewnative':transport,'nativeview_test.previewhistory':history,
            'nativeview_test.l4d2preview':draw,'gpu':gpu,'gpu_extras':extras,'gpu_extras.batch':batch})
        self.modules.start();self.addCleanup(self.modules.stop)
        spec=importlib.util.spec_from_file_location('nativeview_test.previewnativeview',ROOT/'hammerless/blender/previewnativeview.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.view=module.NativeView(123)
        self.view.vertices=[];self.view.metadata=[];self.view.lights=[]
        self.view.geometry_token='geo';self.view.lights_token='light0'
        self.captures=[]
        def capture(cache,matrix,size):
            self.captures.append((matrix,size));self.view.points=[];self.view.covered=[]
        self.view._capture=capture
        self.cache=types.SimpleNamespace(unit_scale=64.,sky_color=(0.,0.,0.))
        self.matrix=((1.,0.,0.,0.),(0.,1.,0.,0.),(0.,0.,1.,0.),(0.,0.,0.,1.))
    def draw(self,signature=0,rays=4,depth=1,exposure=0.):
        return self.view.draw(self.cache,self.matrix,signature,rays,depth,72,exposure)
    def test_lighting_changes_reuse_capture_but_restart_requests(self):
        self.draw();first=self.view.request
        self.view.lights_token='light1';self.draw(signature=1)
        self.assertNotEqual(first,self.view.request)
        self.draw(signature=1,rays=8)
        self.draw(signature=1,rays=8,depth=2)
        self.cache.sky_color=(1.,2.,3.);self.draw(signature=1,rays=8,depth=2)
        self.assertEqual(len(self.captures),1)
        self.assertEqual(len(self.client.requests),5)
        self.assertEqual(self.view.completed,0)
        self.assertIsNone(self.view.light_texture)
        token=self.view.request
        self.draw(signature=1,rays=8,depth=2,exposure=2.)
        self.assertEqual(token,self.view.request)
        self.assertEqual(len(self.captures),1)
    def test_receiver_changes_require_capture(self):
        self.draw()
        self.view.geometry_token='material_or_geometry_edit';self.draw()
        self.matrix=tuple(tuple(v*2 if i==j else v for j,v in enumerate(row)) for i,row in enumerate(self.matrix));self.draw()
        self.viewport[2]=120;self.draw()
        self.cache.unit_scale=32.;self.draw()
        self.assertEqual(len(self.captures),5)
    def test_failed_capture_is_not_cached(self):
        self.view._capture=lambda *a: (_ for _ in ()).throw(RuntimeError('capture failed'))
        with self.assertRaisesRegex(RuntimeError,'capture failed'):self.draw()
        self.assertIsNone(self.view.capture_signature)
        self.assertIsNone(self.view.signature)
        self.assertEqual(len(self.client.requests),0)

if __name__=='__main__':unittest.main()
