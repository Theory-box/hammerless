"""Everything the Add panel can place, grouped by what it's for.

Presets and single entities share one list. Categories are about purpose
(Events, Safe rooms, Volumes...), not Source engine classes.
"""
from __future__ import annotations

from dataclasses import dataclass

from .entities import CATALOG, PRESETS

# (key, label, Blender icon), in tile order
CATEGORIES = (
    ("FAVORITES", "Favorites", "SOLO_ON"),
    ("EVENTS", "Events", "OUTLINER_OB_FORCE_FIELD"),
    ("SAFE_ROOMS", "Safe Rooms", "HOME"),
    ("SURVIVORS", "Survivors", "USER"),
    ("INFECTED", "Infected", "GHOST_ENABLED"),
    ("WEAPONS", "Weapons", "MOD_PHYSICS"),
    ("ITEMS", "Items", "PACKAGE"),
    ("PROPS", "Props", "MESH_CUBE"),
    ("VOLUMES", "Volumes", "MOD_WIREFRAME"),
    ("LOGIC", "Logic", "NODETREE"),
    ("LIGHTS", "Lights", "LIGHT"),
    ("CUSTOM", "Custom", "NODETREE"),
)
CATEGORY_LABELS = {k: label for k, label, _ in CATEGORIES}
CATEGORY_ICONS = {k: icon for k, _, icon in CATEGORIES}

_PRESET_CATEGORY = {
    "SURVIVAL_START": "EVENTS", "SURVIVAL_ARENA": "VOLUMES",
    "START_SAFE_ROOM": "SAFE_ROOMS", "END_SAFE_ROOM": "SAFE_ROOMS",
    "HORDE_TRIGGER": "EVENTS", "HORDE_BUTTON": "EVENTS", "TANK_AMBUSH": "EVENTS",
    "CRESCENDO_BUTTON": "EVENTS", "ZOMBIE_SPAWN_AREA": "EVENTS", "LADDER": "VOLUMES",
    "ZOMBIE_LADDER": "INFECTED", "ZOMBIE_CLIMB": "INFECTED", "GATE_BUTTON": "EVENTS",
}
_ENTITY_CATEGORY = {
    "Players": "SURVIVORS", "Infected": "INFECTED", "Weapons": "WEAPONS", "Items": "ITEMS",
    "Props": "PROPS", "Brush Entities": "VOLUMES", "Logic": "LOGIC", "Lights": "LIGHTS",
}
_CLASS_CATEGORY = {   # entities that belong somewhere other than their catalog category
    "info_landmark": "SAFE_ROOMS", "info_changelevel": "SAFE_ROOMS",
    "prop_door_rotating_checkpoint": "SAFE_ROOMS", "info_director": "LOGIC",
    "hammerless_crescendo": "EVENTS",
}
# Clearer names than the catalog's where two items would look alike
_LABELS = {"func_ladder": "Ladder Volume (no model)", "hammerless_crescendo": "Crescendo Definition (only)",
           "info_zombie_spawn": "Infected Spawn Spot"}
# Extra search words
_ALIASES = {
    "env_tonemap_controller": "tonemapper tone map exposure adaptation bloom hdr brightness",
    "func_movelinear": "gate door lift elevator moving slide platform garage drawbridge",
    "weapon_item_spawn": "random pills health throwable molotov pipe bomb bile adrenaline medkit",
    "weapon_first_aid_kit_spawn": "medkit health heal", "weapon_pain_pills_spawn": "health heal",
    "weapon_adrenaline_spawn": "health", "weapon_defibrillator_spawn": "defib revive",
    "weapon_vomitjar_spawn": "boomer vomit throwable", "weapon_molotov_spawn": "fire throwable",
    "weapon_pipe_bomb_spawn": "throwable grenade", "commentary_zombie_spawner": "tank witch hunter common spawn",
    "info_zombie_spawn": "tank witch special", "START_SAFE_ROOM": "spawn start checkpoint",
    "END_SAFE_ROOM": "finish exit checkpoint next map", "HORDE_TRIGGER": "panic mob",
    "HORDE_BUTTON": "panic mob alarm", "CRESCENDO_BUTTON": "panic waves event",
    "ZOMBIE_SPAWN_AREA": "commons nav obscured", "trigger_once": "touch", "func_detail": "world brush",
    "env_player_blocker": "invisible wall clip", "prop_static": "model", "prop_physics": "model barrel",
    "hammerless_control": "no nav cut bake lightmap skip vis detail sound spawn only exclude volume",
    "hammerless_nav_area": "nav square area manual roof smoker ledge mesh",
    "func_precipitation": "rain storm snow ash bugs smoke weather precipitation wind",
}
_TIPS = {
    "START_SAFE_ROOM": "Comes with 4 survivor spawns, weapons and a door. One per map.",
    "END_SAFE_ROOM": "Set Next Map on it afterwards. Zombies only wander when a map has both rooms.",
    "ZOMBIE_SPAWN_AREA": "Scale it over open ground where commons may appear.",
    "HORDE_TRIGGER": "Scale it across the whole path so nobody can walk around it.",
    "info_changelevel": "The End Safe Room preset already includes one.",
    "info_landmark": "The safe room presets already include one.",
    "prop_door_rotating_checkpoint": "The safe room presets already include one.",
    "info_director": "Added automatically. You only need this to name it yourself.",
    "info_player_start": "Added automatically when missing.",
    "light_environment": "Or set the sun in Lighting > Environment > Sun: added automatically when the scene has none.",
    "func_precipitation": "Scale it over the whole playable area, up to well above the roofs.",
}

# Default box (Hammer units, mins/maxs) for a new brush entity added without a mesh
BRUSH_BOXES = {
    "trigger_once": ((-128, -128, 0), (128, 128, 128)),
    "trigger_multiple": ((-128, -128, 0), (128, 128, 128)),
    "env_player_blocker": ((-128, -8, 0), (128, 8, 256)),
    "func_playerinfected_clip": ((-128, -8, 0), (128, 8, 256)),
    "func_button": ((-4, -16, 40), (4, 16, 72)),
    "func_ladder": ((-4, -16, 0), (4, 16, 256)),
    "func_movelinear": ((-64, -4, 0), (64, 4, 128)),
    "hammerless_nav_region": ((-512, -512, -64), (512, 512, 256)),
    "hammerless_nav_cut": ((-512, -512, -64), (512, 512, 256)),
    "hammerless_no_bake": ((-512, -512, -64), (512, 512, 256)),
    "hammerless_control": ((-512, -512, -64), (512, 512, 256)),
    "hammerless_nav_area": ((-256, -256, 0), (256, 256, 8)),
    "info_changelevel": ((0, 0, 0), (320, 256, 128)),
    "func_precipitation": ((-1024, -1024, 0), (1024, 1024, 1024)),
}
DEFAULT_BRUSH_BOX = ((-64, -64, 0), (64, 64, 128))


@dataclass(frozen=True)
class SpawnItem:
    id: str            # "preset:KEY" or "entity:classname"
    label: str
    category: str
    description: str
    kind: str          # "preset" | "entity" | "volume"
    key: str           # preset key or classname
    tip: str = ""
    icon: str = "OUTLINER_OB_EMPTY"

    @property
    def kind_label(self) -> str:
        return {"preset": "Ready-made group", "entity": "Point entity",
                "volume": "Volume (uses a mesh)", "example": "Object with editable behaviour"}[self.kind]


def _build() -> list[SpawnItem]:
    items = []
    for key, p in PRESETS.items():
        items.append(SpawnItem(f"preset:{key}", p.label, _PRESET_CATEGORY.get(key, "EVENTS"),
                               p.description, "preset", key, _TIPS.get(key, "")))
    from .entities import LEGACY_VOLUMES
    for cls, d in CATALOG.items():
        if cls in LEGACY_VOLUMES:
            continue                  # (a Control Volume does what they did)
        cat = _CLASS_CATEGORY.get(cls) or _ENTITY_CATEGORY.get(d.category, "LOGIC")
        items.append(SpawnItem(f"entity:{cls}", _LABELS.get(cls, d.label), cat, d.description,
                               "volume" if d.brush else "entity", cls, _TIPS.get(cls, "")))
    return items


SPAWN_ITEMS: list[SpawnItem] = _build()
BY_ID: dict[str, SpawnItem] = {i.id: i for i in SPAWN_ITEMS}


def register_examples(examples):
    """Publish object examples with add_menu metadata; stable IDs preserve favorites.

    Metadata: id, label, description, optional tip/icon. Re-registering replaces
    only example entries, in place so existing UI imports remain valid.
    """
    entries = []
    for ex in examples:
        menu = ex.get('add_menu')
        if not menu:
            continue
        if not ex.get('objects') or not ex.get('behaviours') or ex.get('nodes'):
            raise ValueError('Add menu examples must be owned by their objects: ' + ex['title'])
        entries.append(SpawnItem('custom:'+menu['id'], menu['label'], 'CUSTOM',
                                 menu['description'], 'example', menu['id'],
                                 menu.get('tip', ''), menu.get('icon', 'NODETREE')))
    if len({i.id for i in entries}) != len(entries):
        raise ValueError('Duplicate custom Add menu ID')
    SPAWN_ITEMS[:] = [i for i in SPAWN_ITEMS if i.kind != 'example'] + entries
    BY_ID.clear()
    BY_ID.update((i.id, i) for i in SPAWN_ITEMS)


def filtered(category: str, search: str = "", favorites: set[str] | frozenset = frozenset()) -> list[SpawnItem]:
    """Items to list. Typing a search looks through everything, ignoring the category."""
    words = search.lower().split()
    if words:
        def text(i):
            return f"{i.label} {i.key} {i.description} {CATEGORY_LABELS[i.category]} {_ALIASES.get(i.key, '')}".lower()
        hits = [i for i in SPAWN_ITEMS if all(w in text(i) for w in words)]
        # label matches first
        return sorted(hits, key=lambda i: not all(w in i.label.lower() for w in words))
    if category == "FAVORITES":
        return [i for i in SPAWN_ITEMS if i.id in favorites]
    return [i for i in SPAWN_ITEMS if i.category == category]
