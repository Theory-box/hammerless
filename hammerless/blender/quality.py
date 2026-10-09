"""Quality presets. Each system with a speed / quality trade-off has its own Quality (Lighting: settings it fills in,
Custom when one is changed; Visibility: Off / Fast / Full); Build & Play's Quality sets them all at once, and shows
Mixed (stored as CUSTOM) when they're on different levels."""
import bpy

# the settings each Lighting preset fills in (Off only switches lighting off: the settings stay)
_NORMAL = dict(light_fast=False, light_sky_rays=1.0, light_supersample=True, light_ss_points=4, light_ss_passes=4,
               light_ss_threshold=0.0625, light_bounces=100, static_prop_lighting=True, light_prop_polys=True,
               light_patch_size=4.0, light_fix_quirks=False)
LIGHT_PRESETS = {
    "FAST": dict(_NORMAL, light_fast=True, static_prop_lighting=False, light_prop_polys=False),
    "NORMAL": _NORMAL,
    "FINAL": dict(_NORMAL, light_sky_rays=16.0),
    "ULTRA": dict(_NORMAL, light_sky_rays=16.0, light_ss_points=8, light_ss_passes=8, light_ss_threshold=0.03,
                  light_fix_quirks=True),
}


def presets_for(s) -> dict:
    """The Lighting presets for the scene's light compiler: on the GPU (the Hammerless one, not Exact) sky rays are
    nearly free, so Normal casts 4 times vrad's (measured on a real map: half the error, 0.07 s more)."""
    if s.light_tool == "HAMMERLESS" and not s.light_exact:
        return dict(LIGHT_PRESETS, NORMAL=dict(_NORMAL, light_sky_rays=4.0))
    return LIGHT_PRESETS


# Build & Play's Quality: (Visibility, Lighting)
BUILD_LEVELS = {"QUICK": ("SKIP", "OFF"), "FAST": ("FAST", "FAST"), "NORMAL": ("FULL", "NORMAL"),
                "FINAL": ("FULL", "FINAL"), "ULTRA": ("FULL", "ULTRA")}

SETTINGS_VERSION = 1      # (quality.migrate: files saved before per-system qualities are translated once)
_applying = False         # (settings being set by a preset: not a change of the user's own)


def _set(s, values: dict):
    global _applying
    was, _applying = _applying, True
    try:
        for k, v in values.items():
            if getattr(s, k) != v:
                setattr(s, k, v)
    finally:
        _applying = was


def _current(s):
    """(a scene whose qualities have been touched is on today's settings: never translated as an older file's)"""
    if s.settings_version < SETTINGS_VERSION:
        s.settings_version = SETTINGS_VERSION


def _sync_build(s):
    """Build & Play's Quality: the level both systems are on, or Mixed."""
    level = next((k for k, v in BUILD_LEVELS.items() if v == (s.vis_mode, s.light_quality)), "CUSTOM")
    _set(s, {"compile_preset": level})


def on_build_quality(s, context=None):
    if _applying:
        return
    if s.compile_preset not in BUILD_LEVELS:          # (Mixed picked by hand: it's whatever the systems are on)
        _sync_build(s)
        return
    _current(s)
    vis, light = BUILD_LEVELS[s.compile_preset]
    _set(s, {"vis_mode": vis, "light_quality": light})
    _set(s, presets_for(s).get(light, {}))


def on_light_quality(s, context=None):
    if _applying:
        return
    _current(s)
    _set(s, presets_for(s).get(s.light_quality, {}))
    _sync_build(s)


def on_light_tool(s, context=None):
    """Another light compiler (or Exact on / off): a preset's settings follow it (Normal's sky rays)."""
    if not _applying and s.light_quality in LIGHT_PRESETS:
        _set(s, presets_for(s)[s.light_quality])


# the Sky Rays dropdown (a view of light_sky_rays: nothing more is stored)
SKY_RAY_CHOICES = (1, 2, 4, 8, 16, 32, 64)


def get_sky_rays_pick(s) -> int:
    v = s.light_sky_rays
    return next((i for i, x in enumerate(SKY_RAY_CHOICES) if abs(v - x) < 1e-6), 99)


def set_sky_rays_pick(s, value: int) -> None:
    if value != 99:
        s.light_sky_rays = float(SKY_RAY_CHOICES[value])


def on_vis_quality(s, context=None):
    if not _applying:
        _current(s)
        _sync_build(s)


def on_light_setting(s, context=None):
    """A lighting setting changed by hand: the preset is now Custom (unless it still matches one)."""
    if _applying:
        return
    _current(s)
    if s.light_quality == "OFF":
        return
    now = {k: getattr(s, k) for k in _NORMAL}
    match = next((k for k, v in presets_for(s).items() if all(_same(now[n], x) for n, x in v.items())), "CUSTOM")
    _set(s, {"light_quality": match})
    _sync_build(s)


def _same(a, b) -> bool:
    return abs(a - b) < 1e-6 if isinstance(a, float) or isinstance(b, float) else a == b


# ---------------------------------------------------------------- files saved before the per-system qualities


def migrate(s):
    """A scene saved with the single Quality (Quick / Fast / Normal / Final / Custom): the same build with the new
    settings. Custom kept its Visibility, Lighting (vrad) and prop lighting choices."""
    if s.settings_version >= SETTINGS_VERSION:
        return
    old = s.compile_preset
    if old in BUILD_LEVELS:
        on_build_quality(s)
    else:
        light = {"SKIP": "OFF", "FAST": "FAST", "NORMAL": "NORMAL", "FINAL": "FINAL"}[s.rad_mode]
        props = s.static_prop_lighting or light == "FINAL"     # (Final always lit props)
        _set(s, {"light_quality": light})
        _set(s, presets_for(s).get(light, {}))
        if light != "OFF" and props != s.static_prop_lighting:
            # (Custom's own prop lighting choice; lit props were shadowed with their full model)
            _set(s, {"static_prop_lighting": props, "light_prop_polys": props, "light_quality": "CUSTOM"})
        _sync_build(s)
    s.settings_version = SETTINGS_VERSION


@bpy.app.handlers.persistent
def _on_load(_dummy=None):
    for scene in bpy.data.scenes:
        if hasattr(scene, "hammerless") and scene.library is None:     # (linked scenes can't be changed)
            try:
                migrate(scene.hammerless)
            except Exception as ex:          # (one scene's trouble mustn't stop the others)
                print(f"Hammerless: couldn't update {scene.name}'s settings: {ex}")


def register():
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(lambda: _on_load(), first_interval=0.1)    # (the file open when the add-on is enabled)


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
