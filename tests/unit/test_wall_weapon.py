import unittest

from hammerless.core.ir import Entity, MapIR
from hammerless.core.logic import LNode, compile_graph


class WallWeaponExportTests(unittest.TestCase):
    def make_ir(self):
        return MapIR(entities=[Entity("prop_dynamic_override", (16, 32, 64), (0, 90, 0),
            {"model": "models/w_models/weapons/w_smg_uzi.mdl"}, [], "Gun")])

    def script(self, ir):
        return next(iter(ir.extra_scripts.values()))

    def test_controller_can_be_compiled_after_station(self):
        ir = self.make_ir()
        self.assertEqual(compile_graph([LNode("buy", "WALL_WEAPON", {"price": 321}, obj="Gun")], [], ir, "station"), [])
        self.assertIn("::HL_WallPointsEnabled <- false", self.script(ir))
        self.assertEqual(compile_graph([LNode("points", "PERSONAL_POINTS")], [], ir, "points"), [])
        script = self.script(ir)
        self.assertIn("::HL_WallPointsEnabled <- true", script)
        self.assertEqual(script.count("::HL_WallStations.append("), 1)
        self.assertIn('weapon = "smg", title = "Uzi SMG", price = 321', script)
        gun = next(e for e in ir.entities if e.source == "Gun")
        self.assertEqual(gun.origin, (16, 32, 64))
        self.assertEqual(gun.angles, (0, 90, 0))
        self.assertEqual(gun.keyvalues["solid"], "0")
        self.assertTrue(any(e.keyvalues.get("thinkfunction") == "HL_Think" for e in ir.entities))

    def test_same_station_is_not_registered_twice(self):
        ir = self.make_ir()
        compile_graph([LNode("buy", "WALL_WEAPON", obj="Gun")], [], ir, "one")
        problems = compile_graph([LNode("buy", "WALL_WEAPON", obj="Gun")], [], ir, "two")
        self.assertTrue(any("already has" in p for p in problems))
        self.assertEqual(self.script(ir).count("::HL_WallStations.append("), 1)

    def test_unsupported_model_is_left_unchanged(self):
        ir = self.make_ir()
        ir.entities[0].keyvalues["model"] = "models/props/chair.mdl"
        problems = compile_graph([LNode("buy", "WALL_WEAPON", obj="Gun")], [], ir)
        self.assertTrue(problems)
        self.assertNotIn("solid", ir.entities[0].keyvalues)

    def test_multiple_stations_share_one_runtime(self):
        ir = self.make_ir()
        ir.entities.append(Entity("prop_dynamic_override", (80, 32, 64), (0, 0, 0),
            {"model": "models/w_models/weapons/w_rifle_m16a2.mdl"}, [], "Rifle"))
        compile_graph([LNode("points", "PERSONAL_POINTS")], [], ir, "points")
        for obj in ("Gun", "Rifle"):
            self.assertEqual(compile_graph([LNode("buy", "WALL_WEAPON", obj=obj)], [], ir, obj), [])
        script = self.script(ir)
        self.assertEqual(script.count("::HL_WallThink <- function"), 1)
        self.assertEqual(script.count("::HL_WallStations.append("), 2)
        self.assertEqual(script.count("function OnGameEvent_round_start"), 1)


if __name__ == "__main__":
    unittest.main()

class GenericPurchaseTests(unittest.TestCase):
    def test_every_stock_item_accepts_an_unrelated_display(self):
        from hammerless.core.wall_weapon import ITEMS
        for key,data in ITEMS.items():
            with self.subTest(item=key):
                ir=MapIR(entities=[Entity('prop_dynamic_override',(0,0,0),keyvalues={'model':'models/props/crate.mdl'},source='Display')])
                problems=compile_graph([LNode('buy','WALL_WEAPON',{'item':key,'title':'Custom "title"'},obj='Display')],[],ir)
                self.assertEqual(problems,[])
                script=next(iter(ir.extra_scripts.values()))
                self.assertIn('classname = "'+data['classname']+'"',script)
                self.assertIn('weapon = "'+key+'"',script)
    def test_auto_medkit_and_custom_item(self):
        from hammerless.core.wall_weapon import resolve
        key,data=resolve({},'models/w_models/weapons/w_eq_medkit.mdl')
        self.assertEqual(key,'first_aid_kit')
        key,data=resolve({'item':'CUSTOM','custom_item':'weapon_customgun'},'irrelevant')
        self.assertEqual(data['classname'],'weapon_customgun')
        for key in ('health','ammo','weapon_rifle_spawn','bad"code'):
            with self.assertRaises(ValueError):resolve({'item':'CUSTOM','custom_item':key},'')
    def test_melee_confirmation_distinguishes_models(self):
        from hammerless.core.wall_weapon import resolve
        _,a=resolve({'item':'fireaxe'},'');_,b=resolve({'item':'katana'},'')
        self.assertEqual(a['classname'],b['classname'])
        self.assertNotEqual(a['match_model'],b['match_model'])
