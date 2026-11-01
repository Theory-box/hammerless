import os,sys,unittest
from pathlib import Path
if os.environ.get('SQUIRREL_PYTHONPATH'):sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
try:import squirrel
except ImportError:squirrel=None
from hammerless.core.routes import HELPERS
from tests.unit.test_worldqueries import compile_nodes,N,L
class RouteCompilerTests(unittest.TestCase):
    def test_normal_snapshot_and_definition(self):
        errors,script=compile_nodes([N('start','MAP_START'),N('route','ROUTE_DATA',{'points':[(0,0,0),(10,0,0)]}),N('point','ROUTE_POINT',consts={'index':1})],[L('start','start','point','run'),L('route','route','point','route',True)])
        self.assertFalse(errors,errors);self.assertIn('HL_Route_route <- [Vector(',script);self.assertIn('HLRoute.Point(::HL_Route_route, 1',script)
    def test_bad_definition_rejected(self):
        for settings in ({'points':[]},{'points':[(float('nan'),0,0)]},{'points':[(0,0,0)]*257},{'error':'Duplicate order'}):
            errors,_=compile_nodes([N('start','MAP_START'),N('route','ROUTE_DATA',settings),N('point','ROUTE_POINT')],[L('start','start','point','run'),L('route','route','point','route',True)])
            self.assertTrue(errors,settings)
    def test_action_snapshot_path(self):
        nodes=[N('find','BA_FIND'),N('loop','BA_LOOP'),N('route','ROUTE_DATA',{'points':[(0,0,0)]}),N('point','ROUTE_POINT'),N('pub','CO_SIGNAL_PUBLISH',consts={'channel':'goal','ttl':1})]
        links=[L('find','bots','loop','bots',True),L('loop','each','point','run'),L('route','route','point','route',True),L('point','next','pub','run'),L('point','position','pub','position',True)]
        errors,_=compile_nodes(nodes,links);self.assertFalse(errors,errors)
        links[1]=L('loop','each','pub','run');errors,_=compile_nodes(nodes,links);self.assertTrue(any('earlier' in str(e) for e in errors),errors)
@unittest.skipUnless(squirrel is not None and hasattr(squirrel,'SQVM'),'optional squirrel VM unavailable')
class RouteRuntimeTests(unittest.TestCase):
    def test_cursor_operations(self):
        squirrel.SQVM().execute('function Vector(x,y,z){return {x=x,y=y,z=z};}\n'+HELPERS+Path(__file__).with_suffix('.nut').read_text())
if __name__=='__main__':unittest.main()
