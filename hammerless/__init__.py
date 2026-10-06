"""Hammerless: build Left 4 Dead 2 maps from Blender scenes.

The `core` package is pure Python (no bpy) so it can be tested outside Blender;
the `blender` package is only loaded when running inside Blender.
"""
# For Blender 4.0 / 4.1 (old-style add-ons; 4.2+ reads blender_manifest.toml instead and ignores this).
# Keep "version" in step with the manifest (tests/unit checks).
bl_info = {
    "name": "Hammerless",
    "author": "Hammerless contributors",
    "version": (0, 5, 1),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar (N) > Hammerless",
    "description": "Build and play Left 4 Dead 2 maps straight from Blender",
    "doc_url": "https://github.com/Theory-box/hammerless",
    "category": "Import-Export",
}

try:
    import bpy  # noqa: F401
    _IN_BLENDER = True
except ImportError:
    _IN_BLENDER = False


def register():
    from .blender import props, ops, ui, presets, spawn, problems, navview, lightview, logic, watchdog
    watchdog.register()
    props.register()
    for c in presets.CLASSES:
        bpy.utils.register_class(c)
    logic.register()
    spawn.register()
    lightview.register()          # (first: its draw goes under the nav view and problem markers)
    problems.register()
    navview.register()
    ops.register()
    ui.register()


def unregister():
    from .blender import props, ops, ui, presets, spawn, problems, navview, lightview, logic, watchdog
    ui.unregister()
    ops.unregister()
    lightview.unregister()
    navview.unregister()
    problems.unregister()
    spawn.unregister()
    logic.unregister()
    for c in reversed(presets.CLASSES):
        bpy.utils.unregister_class(c)
    props.unregister()
    watchdog.unregister()
