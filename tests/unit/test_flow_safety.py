import tempfile
import struct
import unittest
from pathlib import Path
from hammerless.core.compile import verify_deployed_bsp, parse_flow
from hammerless.core.flowdiagnostics import message
from hammerless.core.build import validate
from hammerless.core.ir import MapIR, Entity
from hammerless.core.geometry import box_brush


def bsp(entity_text, pak=b""):
    header = bytearray(1036)
    header[:8] = b"VBSP" + struct.pack("<i", 21)
    struct.pack_into("<iiii", header, 8, 0, len(header), len(entity_text), 0)
    struct.pack_into("<iiii", header, 8+40*16, 0, len(header)+len(entity_text), len(pak), 0)
    return bytes(header) + entity_text + pak


class FlowSafetyTests(unittest.TestCase):
    def test_deployment_rejects_old_entities_and_accepts_pak_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            built, game = Path(folder)/"built.bsp", Path(folder)/"game.bsp"
            built.write_bytes(bsp(b'info_changelevel'))
            game.write_bytes(bsp(b'old_map'))
            with self.assertRaisesRegex(RuntimeError, "differs"):
                verify_deployed_bsp(str(built), str(game))
            game.write_bytes(bsp(b'info_changelevel', b'game dictionary'))
            verify_deployed_bsp(str(built), str(game))
            game.unlink()
            with self.assertRaisesRegex(RuntimeError, "Cannot verify"):
                verify_deployed_bsp(str(built), str(game))

    def test_new_and_legacy_flow_messages(self):
        for state in ("nonav", "noexit", "nostart", "noend"):
            report = parse_flow("HAMMERLESS_FLOW " + state)
            severity, text = message(report)
            self.assertEqual(severity, "ERROR")
            self.assertIn("Build & Play", text)
        for suffix, connected in (("connected", True), ("disconnected", False), ("at 1 2 3", False)):
            report = parse_flow("HAMMERLESS_FLOW broken " + suffix)
            self.assertEqual(report["connected"], connected)
            self.assertNotIn("stops at the marker", message(report)[1])
        self.assertIn("not the number", message(parse_flow("HAMMERLESS_FLOW ok 100"))[1])

    def test_checkpoint_only_explains_missing_campaign_endpoint(self):
        region = Entity("hammerless_nav_region", None, keyvalues={"attributes":"CHECKPOINT"},
                        brushes=[box_brush((0,0,-8),(128,128,96),"tools/toolstrigger")])
        ir = MapIR(entities=[Entity("info_player_start", (16,16,0)), region])
        report = validate(ir)
        warning = next(w for w in report.warnings if w.startswith("No End Safe Room"))
        for text in ("CHECKPOINT", "Change Level Volume", "Next Map", "Landmark", "Build & Play"):
            self.assertIn(text, warning)
        ir.entities.append(Entity("trigger_finale", (64,64,0)))
        self.assertFalse(any(w.startswith("No End Safe Room") for w in validate(ir).warnings))

    def test_missing_landmark_has_repair_steps(self):
        ir = MapIR(entities=[Entity("info_player_start", (16,16,0)),
            Entity("info_changelevel", None, keyvalues={"map":"c1m2_streets","landmark":"missing"},
                   brushes=[box_brush((0,0,-8),(128,128,96),"tools/toolstrigger")])])
        self.assertTrue(any("missing Landmark" in w and "Build & Play" in w for w in validate(ir).warnings))
