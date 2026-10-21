"""Evaluate the whole map for builds without changing the user's editing visibility."""
from contextlib import contextmanager
from functools import wraps


@contextmanager
def map_visibility(context):
    from .mapcollection import map_collection
    coll = map_collection(context.scene)
    if coll is None:
        yield  # Legacy scenes retain their existing export behavior.
        return
    saved = []

    def enable(owner, prop):
        value = getattr(owner, prop)
        if value:
            saved.append((owner, prop, value))
            setattr(owner, prop, False)

    def visit(layer, within=False):
        within = within or layer.collection == coll
        relevant = within or coll in layer.collection.children_recursive
        if not relevant:
            return
        enable(layer, "exclude")
        enable(layer, "hide_viewport")
        enable(layer.collection, "hide_viewport")
        for child in layer.children:
            visit(child, within)

    eyes = []
    try:
        visit(context.view_layer.layer_collection)
        context.view_layer.update()
        for obj in list(coll.all_objects):
            enable(obj, "hide_viewport")
            if obj.name in context.view_layer.objects and obj.hide_get():
                eyes.append(obj)
                obj.hide_set(False)
        context.view_layer.update()
        yield
    finally:
        for obj in eyes:
            obj.hide_set(True)
        for owner, prop, value in reversed(saved):
            setattr(owner, prop, value)
        context.view_layer.update()


def include_hidden_map(function):
    @wraps(function)
    def wrapped(context, *args, **kwargs):
        with map_visibility(context):
            return function(context, *args, **kwargs)
    return wrapped
