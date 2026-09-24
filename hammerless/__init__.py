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
    from .blender import props, ops, ui
    props.register()
    ops.register()
    ui.register()


def unregister():
    from .blender import props, ops, ui
    ui.unregister()
    ops.unregister()
    props.unregister()
