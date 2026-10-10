"""Hammerless: build Left 4 Dead 2 maps from Blender scenes.

The `core` package is pure Python (no bpy) so it can be tested outside Blender;
the `blender` package is only loaded when running inside Blender.
"""
# Old-style add-on info: Blender 4.0 / 4.1 need it, and lets 4.2+ install the same zip from the Add-ons
# list too (as an extension, 4.2+ reads blender_manifest.toml instead). Keep "version" in step with the
# manifest (tests/unit checks).
bl_info = {
    "name": "Hammerless",
    "author": "Hammerless contributors",
    "version": (0, 11, 0),
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
    from .blender import props, ops, ui, presets, spawn, problems, navview, lightview, logic, logic_examples, watchdog, visview, sound, quality, vmfimport, skyview, mapcollection, texturing
    watchdog.register()
    props.register()
    quality.register()
    for c in presets.CLASSES:
        bpy.utils.register_class(c)
    logic.register()
    logic_examples.register()
    spawn.register()
    skyview.register()            # (before the lighting view: the sky goes behind everything)
    lightview.register()          # (first: its draw goes under the nav view and problem markers)
    visview.register()
    sound.register()
    problems.register()
    navview.register()
    ops.register()
    vmfimport.register()
    mapcollection.register()
    texturing.register()
    ui.register()


def unregister():
    from .blender import props, ops, ui, presets, spawn, problems, navview, lightview, logic, logic_examples, watchdog, visview, sound, quality, vmfimport, skyview, mapcollection, texturing
    ui.unregister()
    texturing.unregister()
    mapcollection.unregister()
    vmfimport.unregister()
    skyview.unregister()
    ops.unregister()
    lightview.unregister()
    visview.unregister()
    sound.unregister()
    navview.unregister()
    problems.unregister()
    spawn.unregister()
    logic_examples.unregister()
    logic.unregister()
    for c in reversed(presets.CLASSES):
        bpy.utils.unregister_class(c)
    quality.unregister()
    props.unregister()
    watchdog.unregister()
