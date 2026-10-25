# Custom Add menu

Use **Add > Custom > Swelling Pod / Emergency Knife**, then **Add at Cursor**.
Both also appear in search and can be starred. Select the object to edit its
Behaviour settings. Repeated placements have independent settings and Self
references. Pods keep independent charge and shot state; knife shops intentionally
share each survivor's two-knife inventory cap.

The pod and armature are selected together after placement. Duplicate both with
Shift+D to retain the complete animated assembly. Knife shops require **Personal
Kill Points** once per map; adding shops does not duplicate that controller.
The original examples remain available for learning and editing.

## Register another built-in object example

Add `add_menu` metadata to its example definition:

```python
add_menu=dict(id='stable_item_id', label='Item Name', icon='NODETREE',
              description='What the object does.', tip='Optional setup requirement.')
```

The example must define `objects` and `behaviours`, with no scene-level `nodes`.
The stable ID is used by favorites and lookup, so changing example order does not
break menu entries. Registering refreshes entries without duplicates. The standard
example builder creates models, armatures and behaviour graphs; the Add menu does
not maintain a second copy of the logic. Exposed Setting nodes store defaults on
each object's behaviour slot while allowing the underlying graph to be shared.

This registration is definition metadata, not a runtime VScript action or an
in-editor user-preset publishing interface.

Validation: `tests/blender/test_custom_add.py` exercises actual Add operators,
search/favorites, two placements plus Shift+D for each type, armature remapping,
independent settings, distinct exported entity handles, and save/reload.
