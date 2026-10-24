import unittest
from hammerless.core.ir import MapIR, Entity
from hammerless.core.entities import PRESETS
from hammerless.core.build import validate, build_vmf
from hammerless.core.gamefiles import game_files
from hammerless.core.compile import parse_flow
from hammerless.core.flowdiagnostics import message

class SurvivalTests(unittest.TestCase):
    def scene(self):
        ir=MapIR(entities=[Entity('info_player_start',(0,0,16))])
        ir.settings.game_mode='SURVIVAL'
        for key in ('SURVIVAL_START','SURVIVAL_ARENA'):
            ir.entities.extend(p.entity for p in PRESETS[key].parts)
        return ir
    def test_native_survival_build(self):
        ir=self.scene()
        ir.settings.director_enabled=True
        ir.settings.autotest=True
        vmf,rep=build_vmf(ir)
        self.assertIsNotNone(vmf,rep.errors)
        self.assertFalse(any('safe room' in w.lower() for w in rep.warnings+rep.info))
        self.assertIn('PanicEvent',vmf)
        self.assertNotIn('hammerless_autotest',vmf)
        files=game_files(ir)
        self.assertEqual(files['maps/'+ir.settings.name+'.hlmode'],'survival\n')
        self.assertFalse(any('autotest_' in f or 'director_' in f for f in files))
        self.assertIn('BATTLEFIELD',PRESETS['SURVIVAL_ARENA'].parts[0].entity.keyvalues['attributes'])
    def test_campaign_preserved(self):
        ir=self.scene();ir.settings.game_mode='CAMPAIGN'
        self.assertTrue(any('No End Safe Room' in w for w in validate(ir).warnings))
        self.assertEqual(game_files(ir)['maps/'+ir.settings.name+'.hlmode'],'coop\n')
    def test_missing_arena_and_button(self):
        ir=self.scene();ir.entities=ir.entities[:1]
        warnings=validate(ir).warnings
        self.assertTrue(any('start output' in w for w in warnings))
        self.assertTrue(any('BATTLEFIELD' in w for w in warnings))
    def test_survival_diagnostics(self):
        self.assertEqual(message(parse_flow('HAMMERLESS_FLOW arenaok'),'SURVIVAL')[0],'INFO')
        self.assertEqual(message(parse_flow('HAMMERLESS_FLOW noarena'),'SURVIVAL')[0],'ERROR')
        self.assertNotIn('Add a Change Level',message({'state':'noexit'},'SURVIVAL')[1])
    def test_custom_mutation_not_silently_dropped(self):
        ir=self.scene();ir.scripted_mode=True
        ir.logic_hooks={'CanPickupObject':[]}
        self.assertTrue(any('Survival does not yet support mutation override' in e for e in validate(ir).errors))

    def test_shared_hud_uses_survival_wrapper(self):
        ir=self.scene();ir.scripted_mode=True
        self.assertFalse(validate(ir).errors)
        files=game_files(ir)
        self.assertEqual(files['maps/'+ir.settings.name+'.hlmode'],'hammerless_survival\n')
        self.assertIn('scripts/vscripts/'+ir.settings.name+'_hammerless_survival.nut',files)
        from hammerless.core.gamefiles import mode_addon_files
        addon=mode_addon_files()
        self.assertIn(b'"survival"',addon['modes/hammerless_survival.txt'])
        self.assertIn(b'"coop"',addon['modes/hammerless.txt'])
