import unittest
from hammerless.core.entities import default_keyvalues
from hammerless.core.ir import Entity
from hammerless.core.tonemapper import configure, PREFIX
class TonemapperTests(unittest.TestCase):
 def entity(self, **settings):
  kv=default_keyvalues('env_tonemap_controller');kv.update({PREFIX+k:v for k,v in settings.items()})
  return Entity('env_tonemap_controller',keyvalues=kv)
 def test_auto(self):
  src=self.entity(); out=configure([src]); self.assertEqual(len(out),2)
  self.assertFalse(any(k.startswith(PREFIX) for k in out[0].keyvalues))
  self.assertIn(PREFIX+'mode',src.keyvalues)
  self.assertEqual([o.input for o in out[1].outputs],['UseDefaultAutoExposure','SetAutoExposureMin','SetAutoExposureMax','SetTonemapRate'])
  self.assertTrue(all(o.target==out[0].keyvalues['targetname'] for o in out[1].outputs))
 def test_fixed_all_settings(self):
  out=configure([self.entity(mode='FIXED',scale='2',blend='3',bloom='0.4',bloom_exponent='2',bloom_saturation='1',bright_pixels='1',target='45',min_luminance='3')])
  ops={o.input:o.parameter for o in out[1].outputs}
  self.assertEqual(ops['SetAutoExposureMin'],'2'); self.assertEqual(ops['SetAutoExposureMax'],'2');self.assertIn('SetBloomExponent',ops)
  self.assertNotIn('SetTonemapScale',ops);self.assertNotIn('BlendTonemapScale',ops)
 def test_reset_cannot_cancel_limits_in_reverse_output_order(self):
  actions=configure([self.entity()])[1].outputs
  enabled=set()
  for action in sorted(reversed(actions),key=lambda o:o.delay):
   if action.input=='UseDefaultAutoExposure':enabled.clear()
   elif action.input in ('SetAutoExposureMin','SetAutoExposureMax'):enabled.add(action.input)
  self.assertEqual(enabled,{'SetAutoExposureMin','SetAutoExposureMax'})
 def test_zero_luminance_disables_protection_and_percentage_is_literal(self):
  out=configure([self.entity(bright_pixels='5',min_luminance='0')])
  ops={o.input:o.parameter for o in out[1].outputs}
  self.assertEqual(ops['SetTonemapPercentBrightPixels'],'5')
  self.assertEqual(ops['SetTonemapMinAvgLum'],'0')
 def test_defaults_and_multiple(self):
  out=configure([self.entity(mode='DEFAULT'),self.entity()]); self.assertNotEqual(out[0].keyvalues['targetname'],out[2].keyvalues['targetname'])
  self.assertNotIn('SetAutoExposureMin',[o.input for o in out[1].outputs])
 def test_validation(self):
  for kw in ({'min':'5','max':'1'},{'max':'nan'},{'bloom':'bad'},{'target':'101'}):
   with self.assertRaises(ValueError):configure([self.entity(**kw)])
 def test_raw_controller_unchanged(self):
  e=Entity('env_tonemap_controller');self.assertIs(configure([e])[0],e)
if __name__=='__main__':unittest.main()
