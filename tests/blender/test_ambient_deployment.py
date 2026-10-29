"""Exercise actual script deployment: new bake, no bake, and feature removal."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from hammerless.blender.ops import write_map_scripts
from hammerless.core.ambientquery import MARKER
from hammerless.core.build import Report
from tests.unit.test_ambientquery import bsp

with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    compiled = folder / "test.bsp"
    compiled.write_bytes(bsp())
    rel = "scripts/vscripts/hammerless/logic_test.nut"
    source = {rel: "// test\n" + MARKER}
    report = Report()
    write_map_scripts(directory, "test", source, report, bsp_path=str(compiled))
    deployed = folder / rel
    assert "HL_AmbientData.probes.append" in deployed.read_text()
    assert not report.warnings, report.warnings
    assert MARKER in source[rel]  # Pending source remains reusable, never baked in-place.

    # A subsequent export-only must not pick up the old BSP beside the VMF.
    report = Report()
    write_map_scripts(directory, "test", source, report)
    assert "::HL_AmbientData <- null;" in deployed.read_text()
    assert "probes.append" not in deployed.read_text()
    assert report.warnings

    # A missing/corrupt current BSP also clears old successful data.
    compiled.write_bytes(b"bad")
    report = Report()
    write_map_scripts(directory, "test", source, report, bsp_path=str(compiled))
    assert "::HL_AmbientData <- null;" in deployed.read_text()
    assert report.warnings
    write_map_scripts(directory, "test", {}, Report())
    assert not deployed.exists()

print("PASS ambient deployment freshness, explicit unavailable, and stale-script removal")
