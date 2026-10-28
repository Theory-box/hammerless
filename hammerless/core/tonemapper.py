"""Native L4D2 tonemapper settings applied with map-start entity I/O."""
from dataclasses import replace
import math
from .ir import Entity, Output

# Blank numeric fields leave that engine setting untouched.
PARAMETERS = (
    ("min", "Minimum Exposure", "SetAutoExposureMin"),
    ("max", "Maximum Exposure", "SetAutoExposureMax"),
    ("rate", "Adaptation Speed", "SetTonemapRate"),
    ("bloom", "Bloom Strength", "SetBloomScale"),
    ("bloom_exponent", "Bloom Exponent", "SetBloomExponent"),
    ("bloom_saturation", "Bloom Saturation", "SetBloomSaturation"),
    ("bright_pixels", "Bright Pixels (%)", "SetTonemapPercentBrightPixels"),
    ("target", "Target Brightness (%)", "SetTonemapPercentTarget"),
    ("min_luminance", "Minimum Average Luminance", "SetTonemapMinAvgLum"),
)
PREFIX = "_hl_tm_"

def configure(entities):
    """Leave raw/imported controllers alone; convert only our authored settings."""
    result = []
    taken = {e.keyvalues.get("targetname", "") for e in entities}
    for e in entities:
        kv = e.keyvalues
        if e.classname != "env_tonemap_controller" or PREFIX + "mode" not in kv:
            result.append(e)
            continue
        mode = kv[PREFIX + "mode"]
        if mode not in ("AUTO", "DEFAULT", "FIXED"):
            raise ValueError("Tonemapper: unknown exposure mode")
        values = {}
        for key in [p[0] for p in PARAMETERS] + ["scale", "blend"]:
            text = kv.get(PREFIX + key, "").strip()
            if not text:
                continue
            try:
                value = float(text)
            except ValueError:
                raise ValueError(f"Tonemapper: {key} must be a number or blank") from None
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Tonemapper: {key} must be finite and nonnegative")
            if key in ("bright_pixels", "target") and value > 100:
                raise ValueError(f"Tonemapper: {key} must be between 0 and 100")
            values[key] = value
        if mode == "AUTO" and values.get("min", 0) > values.get("max", float("inf")):
            raise ValueError("Tonemapper: minimum exposure must not exceed maximum exposure")
        name = kv.get("targetname", "").strip()
        if not name:
            i = 1
            while f"hl_tonemapper_{i}" in taken: i += 1
            name = f"hl_tonemapper_{i}"
            taken.add(name)
        elif sum(x.keyvalues.get("targetname", "").strip() == name for x in entities) > 1:
            raise ValueError(f"Tonemapper: target name {name!r} must be unique")
        clean = {k: v for k, v in kv.items() if not k.startswith(PREFIX)}
        clean["targetname"] = name
        result.append(replace(e, keyvalues=clean))
        outputs = []
        def send(input_name, parameter="", delay=0.1):
            outputs.append(Output("OnMapSpawn", name, input_name, parameter, delay, -1))
        if mode != "FIXED":
            # Source prepends output actions. At equal delays the reset can run
            # AFTER our limits, clearing both custom-exposure flags again.
            send("UseDefaultAutoExposure", delay=0.0)
        for key, label, input_name in PARAMETERS:
            if key in ("min", "max") and mode != "AUTO": continue
            if key in values: send(input_name, format(values[key], ".9g"))
        if mode == "FIXED":
            scale = format(values.get("scale", 1), ".9g")
            # These limits are networked to clients; Set/BlendTonemapScale only
            # write a server cvar in Source and are unreliable in multiplayer.
            send("SetAutoExposureMin", scale)
            send("SetAutoExposureMax", scale)
        # Repeat at round/map start, rather than a one-shot only in the first round.
        result.append(Entity("logic_auto", e.origin, keyvalues={"spawnflags": "0"}, outputs=outputs))
    return result
