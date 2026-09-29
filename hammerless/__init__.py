"""Hammerless: build Left 4 Dead 2 maps from Blender scenes.

The `core` package is pure Python (no bpy) so it can be tested outside Blender;
the `blender` package is only loaded when running inside Blender.
"""
try:
    import bpy  # noqa: F401
    _IN_BLENDER = True
except ImportError:
    _IN_BLENDER = False


def register():
    from .blender import props, ops, ui, presets, spawn, problems, navview, logic
    props.register()
    for c in presets.CLASSES:
        bpy.utils.register_class(c)
    logic.register()
    spawn.register()
    problems.register()
    navview.register()
    ops.register()
    ui.register()


def unregister():
    from .blender import props, ops, ui, presets, spawn, problems, navview, logic
    ui.unregister()
    ops.unregister()
    navview.unregister()
    problems.unregister()
    spawn.unregister()
    logic.unregister()
    for c in reversed(presets.CLASSES):
        bpy.utils.unregister_class(c)
    props.unregister()
