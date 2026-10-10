"""Curated L4D2 entity catalog and multi-entity presets (safe rooms).

This hand-written catalog covers the entities needed for a playable map. It will
later be replaced/extended by the full FGD database from srctools, but the
preset logic here stays.

Values marked VERIFY haven't been confirmed in-game yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import geometry as g
from .ir import Brush, Entity, Output, Vec3


@dataclass(frozen=True)
class KeyDef:
    key: str
    default: str
    label: str
    choices: tuple[tuple[str, str], ...] = ()  # (value, label)


# trigger spawnflags: who can set it off (Source: 1 clients, 2 NPCs, 64 everything)
TRIGGER_FLAGS = (("1", "Players"), ("3", "Players and NPCs"), ("64", "Everything"))


@dataclass(frozen=True)
class EntityDef:
    classname: str
    label: str
    category: str
    description: str
    keys: tuple[KeyDef, ...] = ()
    size: Vec3 = (16.0, 16.0, 16.0)   # preview box in Hammer units (x, y, z)
    floor_origin: bool = True          # origin sits on the floor (box drawn upward)
    brush: bool = False                # brush entity (needs mesh)
    model: str = ""                    # preview model path
    bounds: tuple[Vec3, Vec3] | None = None  # explicit preview box, overrides size

    def preview_bounds(self) -> tuple[Vec3, Vec3]:
        if self.bounds:
            return self.bounds
        x, y, z = self.size
        if self.floor_origin:
            return (-x / 2, -y / 2, 0.0), (x / 2, y / 2, z)
        return (-x / 2, -y / 2, -z / 2), (x / 2, y / 2, z / 2)


NAV_REGION = "hammerless_nav_region"  # pseudo-entity, see nav.py
NAV_CUT = "hammerless_nav_cut"        # pseudo-entity: no nav mesh is made inside it (nav.py, navgen.py)
NO_BAKE = "hammerless_no_bake"        # pseudo-entity: no light is baked inside it (lightvolumes.py, hlvrad -nobake)
CRESCENDO = "hammerless_crescendo"     # pseudo-entity, see gamefiles.py
CLIMB = "hammerless_zombie_climb"      # pseudo-entity, see nav.py collect_climbs
PSEUDO_ENTITIES = {NAV_REGION, NAV_CUT, NO_BAKE, CRESCENDO, CLIMB}  # never written to the map
# Tallest wall common infected climbed from a hand-made nav link in our tests (160 yes, 172 no)
ZOMBIE_CLIMB_MAX = 160.0
ZOMBIES_ONLY = ("2", "zombies", "zombie", "infected")   # func_ladder "team" values meaning zombies only

ITEM_FLAGS = KeyDef("spawnflags", "2", "Spawn flags", (
    ("0", "None"), ("1", "Enable physics"), ("2", "Must exist"), ("8", "Infinite items"),
    ("10", "Must exist + infinite"),
))

POPULATIONS = tuple((p, p.title()) for p in (
    "default", "tank", "witch", "witch_bride", "church", "infected", "boomer", "hunter",
    "smoker", "charger", "jockey", "spitter",
))


YES_NO = (("1", "Yes"), ("0", "No"))
# Mover direction choices -> func_movelinear movedir (pitch yaw roll; Source +X is yaw 0, down is pitch 90)
MOVE_DIRECTIONS = (("down", "Down"), ("up", "Up"), ("+x", "+X"), ("-x", "-X"), ("+y", "+Y"), ("-y", "-Y"))
MOVE_DIR_ANGLES = {"down": "90 0 0", "up": "-90 0 0", "+x": "0 0 0", "-x": "0 180 0", "+y": "0 90 0", "-y": "0 270 0"}
MOVE_DIR_AXIS = {"down": (2, -1), "up": (2, 1), "+x": (0, 1), "-x": (0, -1), "+y": (1, 1), "-y": (1, -1)}
# weapon_item_spawn choices (key, label, Hammerless default): health and throwables on
RANDOM_ITEMS = (
    ("item4", "Pain pills", "1"), ("item11", "Adrenaline", "1"), ("item2", "First aid kit", "0"),
    ("item12", "Defibrillator", "0"), ("item3", "Molotov", "1"), ("item5", "Pipe bomb", "1"),
    ("item13", "Bile jar", "1"), ("item1", "Ammo pile", "0"), ("item6", "Oxygen tank", "0"),
    ("item7", "Propane tank", "0"), ("item8", "Gas can", "0"), ("item16", "Chainsaw", "0"),
    ("item17", "Grenade launcher", "0"), ("item18", "M60", "0"),
)


def _item(cls, label, count="1", size=(16, 16, 8)):
    return EntityDef(cls, label, "Items",
                     f"Spawns {label.lower()}.",
                     (KeyDef("count", count, "Count"), ITEM_FLAGS), size=size)


def _weapon(cls, label, count="5"):
    return EntityDef(cls, label, "Weapons", f"Spawns {label.lower()}.",
                     (KeyDef("count", count, "Count"), ITEM_FLAGS), size=(32, 8, 8))


CATALOG: dict[str, EntityDef] = {d.classname: d for d in [
    # --- players
    EntityDef("info_player_start", "Player Start", "Players",
              "Fallback player spawn. Every map needs one.",
              size=(32, 32, 72)),
    EntityDef("info_survivor_position", "Survivor Spawn", "Players",
              "Where a survivor spawns at map start. Place 4 (Order 1-4).",
              (KeyDef("Order", "1", "Order", tuple((str(i), str(i)) for i in range(1, 5))),),
              size=(32, 32, 72)),
    EntityDef("info_survivor_rescue", "Rescue Closet", "Players",
              "Where dead survivors can be rescued from.",
              (KeyDef("rescueEyePos", "0 0 64", "Eye position"),), size=(32, 32, 72)),

    # --- director / flow
    EntityDef("info_director", "Director", "Director",
              "The AI Director. Every L4D2 map needs exactly one (auto-added if missing).",
              (KeyDef("targetname", "director", "Name"),)),
    EntityDef("info_landmark", "Landmark", "Director",
              "Pairs the end safe room of one map with the start safe room of the next.",
              (KeyDef("targetname", "landmark_1", "Name"),)),
    EntityDef("info_changelevel", "Change Level Volume", "Director",
              "Brush volume covering the END safe room; survivors inside when the door "
              "closes go to the next map.",
              (KeyDef("map", "", "Next map"), KeyDef("landmark", "landmark_1", "Landmark name")),
              brush=True),
    EntityDef("prop_door_rotating_checkpoint", "Safe Room Door", "Director",
              "Safe room door. Start door spawns closed; end door spawns open.",
              (KeyDef("model", "models/props_doors/checkpoint_door_01.mdl", "Model"),
               KeyDef("spawnpos", "0", "Spawn position", (("0", "Closed"), ("1", "Open"))),
               KeyDef("speed", "200", "Speed"),
               KeyDef("distance", "90", "Open angle"),
               KeyDef("returndelay", "-1", "Auto-close delay"),
               KeyDef("hardware", "1", "Hardware"),
               KeyDef("spawnflags", "8192", "Spawn flags", (
                   ("8192", "Use closes (normal)"), ("0", "Can't be closed with Use"))),
               KeyDef("opendir", "0", "Open direction",
                      (("0", "Both"), ("1", "Forward only"), ("2", "Backward only")))),
              bounds=((-4.0, 0.0, -53.0), (4.0, 56.0, 53.0)),
              model="models/props_doors/checkpoint_door_01.mdl"),

    # --- props (game models; pick one with the model browser)
    EntityDef("prop_static", "Static Prop", "Props",
              "A game model that never moves (cars, dumpsters, fences, rubble). Cheapest kind of prop.",
              (KeyDef("model", "models/props_junk/dumpster.mdl", "Model"),
               KeyDef("solid", "6", "Collision", (("6", "Use model's collision"), ("0", "Not solid"), ("2", "Bounding box"))),
               KeyDef("skin", "0", "Skin"),
               KeyDef("disableshadows", "0", "Disable shadows", (("0", "No"), ("1", "Yes"))),
               KeyDef("fademaxdist", "0", "Fade out distance (0 = never)")),
              model="models/props_junk/dumpster.mdl"),
    EntityDef("prop_dynamic", "Dynamic Prop", "Props",
              "A game model that can animate, be hidden/shown or moved by outputs.",
              (KeyDef("model", "models/props_junk/dumpster.mdl", "Model"),
               KeyDef("solid", "6", "Collision", (("6", "Use model's collision"), ("0", "Not solid"), ("2", "Bounding box"))),
               KeyDef("targetname", "", "Name"),
               KeyDef("DefaultAnim", "", "Default animation"),
               KeyDef("skin", "0", "Skin")),
              model="models/props_junk/dumpster.mdl"),
    EntityDef("prop_physics", "Physics Prop", "Props",
              "A game model that falls, can be pushed and shot around (barrels, crates, cans).",
              (KeyDef("model", "models/props_c17/oildrum001.mdl", "Model"),
               KeyDef("skin", "0", "Skin"),
               KeyDef("spawnflags", "0", "Spawn flags", (("0", "Normal"), ("1", "Start asleep (still until hit)"),
                                                          ("8", "Motion disabled (never moves)")))),
              model="models/props_c17/oildrum001.mdl"),
    EntityDef("prop_door_rotating", "Door", "Props",
              "An ordinary door that opens with Use. Infected can break it.",
              (KeyDef("model", "models/props_doors/doormain01.mdl", "Model"),
               KeyDef("spawnflags", "8192", "Spawn flags", (("8192", "Use closes (normal)"), ("10240", "Starts locked"))),
               KeyDef("distance", "90", "Open angle"),
               KeyDef("speed", "200", "Speed"),
               KeyDef("returndelay", "-1", "Auto-close delay (-1 = never)"),
               KeyDef("hardware", "1", "Handle", (("0", "None"), ("1", "Lever"), ("2", "Push bar"))),
               KeyDef("opendir", "0", "Open direction", (("0", "Both"), ("1", "Forward only"), ("2", "Backward only"))),
               KeyDef("targetname", "", "Name")),
              bounds=((-2.0, 0.0, -54.0), (2.0, 52.0, 54.0)),
              model="models/props_doors/doormain01.mdl"),

    # --- infected
    EntityDef("info_zombie_spawn", "Infected Spawn", "Infected",
              "A spot the Director may use when it spawns this type of infected. It is not a "
              "guaranteed spawn: for 'always spawn here' use a Zombie Spawner. Common infected "
              "spawn by themselves in places survivors can't see.",
              (KeyDef("population", "default", "Population", POPULATIONS),
               KeyDef("offer_tank", "0", "Offer tank to player", (("0", "No"), ("1", "Yes")))),
              size=(32, 32, 72)),

    EntityDef("commentary_zombie_spawner", "Zombie Spawner", "Infected",
              "Spawns infected on command, right here, even in plain sight. Send it the input "
              "SpawnZombie with a parameter: common, tank, witch, hunter, boomer, smoker, charger, "
              "jockey or spitter.",
              (KeyDef("targetname", "zombie_spawner", "Name"),), size=(32, 32, 72)),

    # --- logic
    EntityDef(CRESCENDO, "Crescendo Definition", "Logic",
              "Defines a crescendo (a horde in stages, e.g. 'hold out until the lift arrives'). "
              "Start it with an output: target director, input ScriptedPanicEvent, parameter = this name. "
              "Stages: PANIC n (n hordes), DELAY s (wait s seconds), TANK n.",
              (KeyDef("name", "crescendo_1", "Name"),
               KeyDef("stages", "PANIC 1, DELAY 10, PANIC 1, DELAY 10, PANIC 2", "Stages")),
              size=(24, 24, 24), floor_origin=False),
    EntityDef("logic_auto", "Map Start", "Logic",
              "Fires OnMapSpawn when the map loads. Use it to set things up at the start, e.g. "
              "tell a Zombie Spawner to SpawnZombie witch.",
              size=(16, 16, 16), floor_origin=False),
    EntityDef("logic_relay", "Relay", "Logic",
              "Passes a signal on: send it Trigger, it fires OnTrigger. Handy for one event that "
              "should set off several things.",
              (KeyDef("targetname", "relay", "Name"),), size=(16, 16, 16), floor_origin=False),

    # --- items
    _item("weapon_first_aid_kit_spawn", "First Aid Kit"),
    _item("weapon_pain_pills_spawn", "Pain Pills"),
    _item("weapon_adrenaline_spawn", "Adrenaline"),
    _item("weapon_defibrillator_spawn", "Defibrillator"),
    _item("weapon_molotov_spawn", "Molotov"),
    _item("weapon_pipe_bomb_spawn", "Pipe Bomb"),
    _item("weapon_vomitjar_spawn", "Bile Jar"),
    EntityDef("weapon_item_spawn", "Item (random)", "Items",
              "The game picks one of the items set to 1 each time (0 = never this one). With Spawn "
              "flags 0 the Director may also leave the spot empty.",
              tuple(KeyDef(key, default, label, YES_NO) for key, label, default in RANDOM_ITEMS)
              + (KeyDef("melee_weapon", "", "Melee weapon too: blank = no, 'any', or names"),
                 KeyDef("spawnflags", "0", "Spawn flags", ITEM_FLAGS.choices)),
              size=(16, 16, 8)),
    EntityDef("weapon_ammo_spawn", "Ammo Pile", "Items", "Infinite ammo pile.",
              (KeyDef("model", "models/props/terror/ammo_stack.mdl", "Model"),),
              size=(26, 34, 8), model="models/props/terror/ammo_stack.mdl"),

    # --- weapons
    EntityDef("weapon_spawn", "Weapon (random)", "Weapons",
              "Spawns a weapon from a category.",
              (KeyDef("weapon_selection", "any_primary", "Weapon", tuple((w, w) for w in (
                  "any", "any_primary", "tier1_any", "tier2_any", "any_smg", "any_shotgun",
                  "any_rifle", "any_sniper_rifle", "tier1_shotgun", "tier2_shotgun",
                  "any_pistol"))),
               KeyDef("count", "5", "Count"), ITEM_FLAGS), size=(32, 8, 8)),
    EntityDef("weapon_melee_spawn", "Melee Weapon", "Weapons", "Spawns a melee weapon.",
              (KeyDef("melee_weapon", "any", "Weapon"), KeyDef("count", "1", "Count"), ITEM_FLAGS),
              size=(32, 8, 8)),
    _weapon("weapon_pistol_spawn", "Pistol"),
    _weapon("weapon_pistol_magnum_spawn", "Magnum"),
    _weapon("weapon_smg_spawn", "SMG"),
    _weapon("weapon_pumpshotgun_spawn", "Pump Shotgun"),
    _weapon("weapon_autoshotgun_spawn", "Auto Shotgun"),
    _weapon("weapon_rifle_spawn", "Assault Rifle"),
    _weapon("weapon_hunting_rifle_spawn", "Hunting Rifle"),

    # --- brush entities (the mesh supplies the shape)
    EntityDef("func_detail", "Detail Brush", "Brush Entities",
              "Brush that doesn't cut visibility. Use for small/complex world pieces.",
              brush=True),
    EntityDef("func_playerinfected_clip", "Infected Clip", "Brush Entities",
              "Blocks player-controlled infected.", brush=True),
    EntityDef("env_player_blocker", "Player Blocker", "Brush Entities",
              "Invisible wall for survivors/infected.",
              (KeyDef("BlockType", "0", "Blocks", (
                  ("0", "Everyone"), ("1", "Survivors"), ("2", "Player infected"),
                  ("3", "All players and PZ"))),), brush=True),
    EntityDef("trigger_once", "Trigger (once)", "Brush Entities",
              "Fires its outputs once when a player touches it.",
              (KeyDef("spawnflags", "1", "Touched by", TRIGGER_FLAGS),), brush=True),
    EntityDef("trigger_multiple", "Trigger (multiple)", "Brush Entities",
              "Fires its outputs each time a player touches it.",
              (KeyDef("spawnflags", "1", "Touched by", TRIGGER_FLAGS), KeyDef("wait", "1", "Delay before reset")),
              brush=True),

    EntityDef("func_ladder", "Ladder", "Brush Entities",
              "Climbable volume. Give the side players climb from the tools/toolsinvisibleladder "
              "material (the Ladder preset does this). The compiler turns it into an L4D2 ladder "
              "the nav mesh understands.",
              (KeyDef("team", "everyone", "Who climbs: everyone or zombies"),), brush=True),
    EntityDef("func_movelinear", "Mover (gate, lift, sliding door)", "Brush Entities",
              "A brush that slides in a straight line when told to: gates, garage doors, drawbridges, "
              "lifts. Send it Open to slide out, Close to slide back (e.g. from a button's OnPressed). It "
              "fires OnFullyOpen / OnFullyClosed when it gets there. Players standing on it ride along.",
              (KeyDef("targetname", "gate_1", "Name (outputs send Open/Close to this)"),
               KeyDef("direction", "down", "Direction", MOVE_DIRECTIONS),
               KeyDef("movedistance", "auto", "Distance in units (auto = its own size that way)"),
               KeyDef("move_time", "4", "Seconds to get there"),
               KeyDef("startsound", "", "Sound when it starts (optional)"),
               KeyDef("stopsound", "", "Sound when it stops (optional)")), brush=True),
    EntityDef("func_button", "Button", "Brush Entities",
              "Something players press with Use. Wire its OnPressed output to start events "
              "(e.g. director > ForcePanicEvent for a horde).",
              (KeyDef("spawnflags", "1025", "Spawn flags", (
                  ("1025", "Use activates, doesn't move"), ("1", "Doesn't move (touch/damage only)"),
                  ("1281", "Touch activates"))),
               KeyDef("wait", "-1", "Reset delay (-1 = press once)"),
               KeyDef("targetname", "", "Name")), brush=True),

    # --- nav (not written to the map; used to mark the nav mesh after nav_generate)
    EntityDef(NAV_REGION, "Nav Attribute Region", "Brush Entities",
              "Marks the nav mesh inside this volume after nav generation. Start safe rooms need "
              "PLAYER_START CHECKPOINT. Change-level volumes are marked CHECKPOINT automatically.",
              (KeyDef("attributes", "PLAYER_START CHECKPOINT", "Attributes"),), brush=True),

    EntityDef(NAV_CUT, "No Nav Volume", "Brush Entities",
              "No nav mesh is made inside this volume: put it around roofs, ledges, skybox floors and anything "
              "outside the playable space. The nav builder stops at it like a wall, so big maps build faster and "
              "stay under its limit. Only for nav made in Blender.",
              (), brush=True),

    EntityDef(NO_BAKE, "No Bake Volume", "Brush Entities",
              "No light is baked for surfaces inside this volume: they get the map's ambient colour instead, and "
              "the bake skips them. Put it under the map or around anything never seen. Mode Bake only inside: "
              "it becomes a choice in Lighting's Bake dropdown, to bake just that area (for testing it quickly; "
              "builds still bake the whole map). Surfaces inside still cast shadows. Only for the Hammerless "
              "light compiler.",
              (KeyDef("invert", "0", "Mode", (("0", "Don't bake inside"), ("1", "Bake only inside"))),),
              brush=True),

    EntityDef(CLIMB, "Zombie Climb Point", "Infected",
              "One end of a Zombie Climb (use the Zombie Climb preset: a bottom and a top point). "
              "Adds a one-way nav link so common infected climb from the bottom up to the top.",
              (KeyDef("end", "bottom", "bottom or top"),
               KeyDef("climb", "climb_1", "Climb name (pairs the two ends)")),
              size=(12.0, 12.0, 12.0)),

    # --- lights (basic; full lighting comes later)
    EntityDef("light", "Point Light", "Lights", "Omni light.",
              (KeyDef("_light", "255 240 220 300", "Color + brightness"),), floor_origin=False),
    EntityDef("light_environment", "Sun", "Lights",
              "Sun + sky ambient light. Auto-added if the map has no lights.",
              (KeyDef("_light", "255 245 225 400", "Sun color + brightness"),
               KeyDef("_ambient", "140 160 190 80", "Ambient color + brightness"),
               KeyDef("pitch", "-50", "Sun pitch")), floor_origin=False),
]}

CATEGORIES = ["Players", "Director", "Props", "Infected", "Items", "Weapons", "Logic", "Brush Entities", "Lights"]


def default_keyvalues(classname: str) -> dict[str, str]:
    d = CATALOG.get(classname)
    return {k.key: k.default for k in d.keys} if d else {}


# ---------------------------------------------------------------- presets

@dataclass
class PresetPart:
    """A piece of a preset, positioned relative to the preset origin."""
    name: str
    brush: Brush | None = None          # world brush (mins/maxs box)
    entity: Entity | None = None        # point or brush entity


@dataclass
class Preset:
    key: str
    label: str
    description: str
    parts: list[PresetPart] = field(default_factory=list)


# Safe room interior (Hammer units). The doorway sits in the +X wall.
ROOM_X, ROOM_Y, ROOM_Z = 320.0, 256.0, 128.0
WALL = 16.0
DOOR_W, DOOR_H = 56.0, 104.0
WALL_MATERIAL = "dev/dev_measuregeneric01b"
FLOOR_MATERIAL = "dev/dev_measuregeneric01b"


def _room_shell(material_walls: str, material_floor: str) -> list[PresetPart]:
    """Hollow box room, interior from (0,0,0) to (ROOM_X, ROOM_Y, ROOM_Z), with a
    doorway centred in the +X wall."""
    x, y, z, w = ROOM_X, ROOM_Y, ROOM_Z, WALL
    parts = [
        ("floor", (-w, -w, -w), (x + w, y + w, 0), material_floor),
        ("ceiling", (-w, -w, z), (x + w, y + w, z + w), material_walls),
        ("wall_-x", (-w, 0, 0), (0, y, z), material_walls),
        ("wall_-y", (-w, -w, 0), (x + w, 0, z), material_walls),
        ("wall_+y", (-w, y, 0), (x + w, y + w, z), material_walls),
    ]
    d0 = (y - DOOR_W) / 2
    d1 = d0 + DOOR_W
    parts += [
        ("wall_+x_a", (x, 0, 0), (x + w, d0, z), material_walls),
        ("wall_+x_b", (x, d1, 0), (x + w, y, z), material_walls),
        ("wall_+x_top", (x, d0, DOOR_H), (x + w, d1, z), material_walls),
    ]
    return [PresetPart(n, brush=g.box_brush(a, b, m, n)) for n, a, b, m in parts]


def _ceiling_light() -> PresetPart:
    return PresetPart("light", entity=Entity(
        "light", (ROOM_X / 2, ROOM_Y / 2, ROOM_Z - 12), (0, 0, 0), {"_light": "255 225 180 120"}))


def _door(end: bool) -> PresetPart:
    kv = default_keyvalues("prop_door_rotating_checkpoint")
    kv["model"] = "models/props_doors/checkpoint_door_02.mdl" if end else "models/props_doors/checkpoint_door_01.mdl"
    kv["spawnpos"] = "1" if end else "0"   # VERIFY: end door starts open
    kv["targetname"] = "checkpoint_exit" if end else "checkpoint_entrance"
    # Door model origin is at the hinge, extends along +Y, and is centred vertically.
    origin = (ROOM_X + WALL / 2, (ROOM_Y - DOOR_W) / 2, 53.0)
    return PresetPart("door", entity=Entity("prop_door_rotating_checkpoint", origin, (0, 0, 0), kv))


def start_safe_room(landmark: str = "landmark_1") -> Preset:
    parts = _room_shell(WALL_MATERIAL, FLOOR_MATERIAL)
    parts.append(_door(end=False))
    parts.append(_ceiling_light())
    # Same name and same spot as the end room's landmark, so survivors arriving from
    # the previous map land in the matching position (both rooms share one layout).
    parts.append(PresetPart("landmark", entity=Entity(
        "info_landmark", (ROOM_X / 2, ROOM_Y / 2, 32.0), (0, 0, 0), {"targetname": landmark})))
    region = g.box_brush((0, 0, 0), (ROOM_X, ROOM_Y, ROOM_Z), "tools/toolstrigger", "nav_start")
    parts.append(PresetPart("nav_region", entity=Entity(
        NAV_REGION, None, (0, 0, 0), {"attributes": "PLAYER_START CHECKPOINT"}, [region])))
    for i in range(4):
        parts.append(PresetPart(f"survivor_{i + 1}", entity=Entity(
            "info_survivor_position", (64.0 + 48 * i, ROOM_Y / 2, 1.0), (0, 0, 0), {"Order": str(i + 1)})))
    parts.append(PresetPart("player_start", entity=Entity(
        "info_player_start", (64.0, ROOM_Y / 2 + 48, 1.0), (0, 0, 0), {})))
    parts.append(PresetPart("ammo", entity=Entity(
        "weapon_ammo_spawn", (40.0, 40.0, 1.0), (0, 0, 0), default_keyvalues("weapon_ammo_spawn"))))
    for i, cls in enumerate(("weapon_smg_spawn", "weapon_pumpshotgun_spawn")):
        parts.append(PresetPart(cls, entity=Entity(
            cls, (100.0 + 60 * i, 24.0, 1.0), (0, 90, 0), default_keyvalues(cls))))
    parts.append(PresetPart("pistol", entity=Entity(
        "weapon_pistol_spawn", (220.0, 24.0, 1.0), (0, 90, 0), default_keyvalues("weapon_pistol_spawn"))))
    parts.append(PresetPart("melee", entity=Entity(
        "weapon_melee_spawn", (260.0, 24.0, 1.0), (0, 90, 0), default_keyvalues("weapon_melee_spawn"))))
    return Preset("START_SAFE_ROOM", "Start Safe Room",
                  "Room + closed checkpoint door + 4 survivor spawns + starting weapons/ammo.", parts)


def end_safe_room(next_map: str = "", landmark: str = "landmark_1") -> Preset:
    parts = _room_shell(WALL_MATERIAL, FLOOR_MATERIAL)
    parts.append(_door(end=True))
    parts.append(_ceiling_light())
    for i in range(4):
        parts.append(PresetPart(f"medkit_{i + 1}", entity=Entity(
            "weapon_first_aid_kit_spawn", (40.0 + 24 * i, 24.0, 1.0), (0, 0, 0),
            default_keyvalues("weapon_first_aid_kit_spawn"))))
    parts.append(PresetPart("ammo", entity=Entity(
        "weapon_ammo_spawn", (40.0, ROOM_Y - 40, 1.0), (0, 0, 0), default_keyvalues("weapon_ammo_spawn"))))
    parts.append(PresetPart("landmark", entity=Entity(
        "info_landmark", (ROOM_X / 2, ROOM_Y / 2, 32.0), (0, 0, 0), {"targetname": landmark})))
    vol = g.box_brush((0, 0, 0), (ROOM_X, ROOM_Y, ROOM_Z), "tools/toolstrigger", "changelevel")
    parts.append(PresetPart("changelevel", entity=Entity(
        "info_changelevel", None, (0, 0, 0), {"map": next_map, "landmark": landmark}, [vol])))
    return Preset("END_SAFE_ROOM", "End Safe Room",
                  "Room + open checkpoint door + medkits/ammo + landmark + change-level volume.", parts)


# ---------------------------------------------------------------- horde / event presets
# All centred on the 3D cursor. They talk to the AI Director, which Hammerless always
# names "director" (auto-added info_director).

def horde_trigger() -> Preset:
    vol = g.box_brush((-128, -128, 0), (128, 128, 128), "tools/toolstrigger", "horde_trigger")
    ent = Entity("trigger_once", None, (0, 0, 0), {"spawnflags": "1"}, [vol],
                 outputs=[Output("OnTrigger", "director", "ForcePanicEvent", times=1)])
    return Preset("HORDE_TRIGGER", "Horde Trigger",
                  "Invisible volume: the first survivor to walk in starts a horde (panic event).",
                  [PresetPart("trigger", entity=ent)])


def horde_button() -> Preset:
    btn = g.box_brush((-4, -16, 40), (4, 16, 72), "dev/dev_hazzardstripe01a", "horde_button")
    ent = Entity("func_button", None, (0, 0, 0), {"spawnflags": "1025", "wait": "-1"}, [btn],
                 outputs=[Output("OnPressed", "director", "ForcePanicEvent", times=1)])
    return Preset("HORDE_BUTTON", "Horde Button",
                  "A button (hazard stripes) on a wall: pressing it starts a horde. Like a car alarm "
                  "or the lift buttons in the campaigns.", [PresetPart("button", entity=ent)])


def tank_ambush() -> Preset:
    spawner = Entity("commentary_zombie_spawner", (0, 0, 0), (0, 0, 0), {"targetname": "tank_ambush_spawner"})
    vol = g.box_brush((-640, -128, 0), (-384, 128, 128), "tools/toolstrigger", "tank_ambush_trigger")
    trig = Entity("trigger_once", None, (0, 0, 0), {"spawnflags": "1"}, [vol],
                  outputs=[Output("OnTrigger", "tank_ambush_spawner", "SpawnZombie", "tank", times=1)])
    return Preset("TANK_AMBUSH", "Tank Ambush",
                  "A Tank spawns at the cursor when a survivor walks into the trigger about 10 m behind it "
                  "(move the pieces apart as you like).",
                  [PresetPart("spawner", entity=spawner), PresetPart("trigger", entity=trig)])


def crescendo_button(name: str = "crescendo_1") -> Preset:
    btn = g.box_brush((-4, -16, 40), (4, 16, 72), "dev/dev_hazzardstripe01a", "crescendo_button")
    ent = Entity("func_button", None, (0, 0, 0), {"spawnflags": "1025", "wait": "-1"}, [btn],
                 outputs=[Output("OnPressed", "director", "ScriptedPanicEvent", name, times=1)])
    definition = Entity(CRESCENDO, (0, 0, 96), (0, 0, 0), {
        "name": name, "stages": "PANIC 1, DELAY 10, PANIC 1, DELAY 10, PANIC 2"})
    return Preset("CRESCENDO_BUTTON", "Crescendo Button",
                  "A button that starts a crescendo: a horde in waves (edit the stages on the "
                  "Crescendo Definition next to it). Like the lift and radio events in the campaigns.",
                  [PresetPart("button", entity=ent), PresetPart("definition", entity=definition)])


def ladder(height: float = 256.0) -> Preset:
    """Climbable from the +X side (the direction the preset arrow points)."""
    vol = g.box_brush((-4, -16, 0), (4, 16, height), "tools/toolsnodraw", "ladder")
    vol.faces[4].material = "tools/toolsinvisibleladder"   # +X face: the climbable side
    parts = [PresetPart("volume", entity=Entity("func_ladder", None, (0, 0, 0), {"team": "everyone"}, [vol]))]
    z = 0.0
    n = 0
    while z < height - 8:
        n += 1
        parts.append(PresetPart(f"model_{n}", entity=Entity(
            "prop_static", (5.0, 0.0, z), (0, 0, 0),
            {"model": "models/props_c17/metalladder001.mdl", "solid": "0"})))
        z += 128.0
    return Preset("LADDER", "Ladder",
                  "A 256-unit (about 5 m) climbable ladder with a visible metal ladder model. "
                  "Climb from the side the arrow points to. Scale it in Z for other heights.", parts)


def zombie_ladder(height: float = 256.0) -> Preset:
    """An invisible ladder only the infected can use: how Valve's maps let zombies up poles,
    fences, walls and onto roofs."""
    vol = g.box_brush((-4, -16, 0), (4, 16, height), "tools/toolsnodraw", "zombie_ladder")
    vol.faces[4].material = "tools/toolsinvisibleladder"   # +X face: the climbable side
    return Preset("ZOMBIE_LADDER", "Zombie Ladder",
                  "An invisible 256-unit ladder only zombies can climb. Put it against a pole, wall or "
                  "building with the arrow pointing away from the surface. Scale it in Z for other heights.",
                  [PresetPart("volume", entity=Entity("func_ladder", None, (0, 0, 0), {"team": "zombies"}, [vol]))])


def zombie_climb(name: str = "climb_1") -> Preset:
    return Preset("ZOMBIE_CLIMB", "Zombie Climb",
                  "Lets common infected climb a wall or ledge up to 160 units (about 3 m) high: put the bottom "
                  "point on the ground at the foot of the wall and the top point on the ledge. Taller "
                  "than that, use a Zombie Ladder.",
                  [PresetPart("bottom", entity=Entity(CLIMB, (0.0, 0.0, 1.0), (0, 0, 0),
                                                      {"end": "bottom", "climb": name})),
                   PresetPart("top", entity=Entity(CLIMB, (48.0, 0.0, 129.0), (0, 0, 0),
                                                   {"end": "top", "climb": name}))])


def gate_button(name: str = "gate_1") -> Preset:
    """A gate that slides down into the floor when a button is pressed, and starts a horde:
    a worked example of wiring a Mover to a button."""
    gate = g.box_brush((96, -64, 0), (104, 64, 128), "dev/dev_measuregeneric01b", "gate")
    button = g.box_brush((-4, -16, 40), (4, 16, 72), "dev/dev_hazzardstripe01a", "gate_button")
    return Preset("GATE_BUTTON", "Gate + Button",
                  "A gate that slides down into the floor over 4 seconds when survivors press the button, "
                  "and starts a horde. The gate is a Mover and the button's outputs tell it to Open: change "
                  "either, or copy the idea for lifts and sliding doors.",
                  [PresetPart("gate", entity=Entity("func_movelinear", None, (0, 0, 0), {
                      "targetname": name, "direction": "down", "movedistance": "auto", "move_time": "4"}, [gate])),
                   PresetPart("button", entity=Entity("func_button", None, (0, 0, 0),
                                                      {"spawnflags": "1025", "wait": "-1"}, [button], outputs=[
                       Output("OnPressed", name, "Open", times=1),
                       Output("OnPressed", "director", "ForcePanicEvent", times=1)]))])


def zombie_spawn_area() -> Preset:
    vol = g.box_brush((-512, -512, -64), (512, 512, 256), "tools/toolstrigger", "zombie_spawn_area")
    ent = Entity(NAV_REGION, None, (0, 0, 0), {"attributes": "OBSCURED"}, [vol])
    return Preset("ZOMBIE_SPAWN_AREA", "Zombie Spawn Area",
                  "Marks the ground inside as hidden (OBSCURED) so the Director may spawn common "
                  "infected there even if survivors can see it. Needed in open areas like fields.",
                  [PresetPart("nav_region", entity=ent)])


PRESET_BUILDERS = {"START_SAFE_ROOM": start_safe_room, "END_SAFE_ROOM": end_safe_room,
                   "HORDE_TRIGGER": horde_trigger, "HORDE_BUTTON": horde_button,
                   "TANK_AMBUSH": tank_ambush, "CRESCENDO_BUTTON": crescendo_button,
                   "ZOMBIE_SPAWN_AREA": zombie_spawn_area, "LADDER": ladder,
                   "ZOMBIE_LADDER": zombie_ladder, "ZOMBIE_CLIMB": zombie_climb, "GATE_BUTTON": gate_button}
PRESETS = {k: f() for k, f in PRESET_BUILDERS.items()}  # default instances (labels, tests)


@dataclass
class PresetField:
    """A setting shown on a preset's parent object. The value lives on its parts:
    `targets` are (part name, kind, key) with kind "kv" (keyvalue `key`), or
    "output_param" / "output_target" (the part's output whose input is `key`).
    The first target is the one shown; the rest are kept equal to it."""
    label: str
    description: str
    targets: tuple[tuple[str, str, str], ...]


PRESET_FIELDS: dict[str, list[PresetField]] = {
    "START_SAFE_ROOM": [
        PresetField("Landmark", "Links this room to the previous map's end room (same name there). "
                    "Must differ from this map's end room landmark",
                    (("landmark", "kv", "targetname"),)),
    ],
    "END_SAFE_ROOM": [
        PresetField("Next Map", "Map loaded when survivors close the door. Must be a different map, "
                    "or the Director has no start-to-end path and zombies won't wander",
                    (("changelevel", "kv", "map"),)),
        PresetField("Landmark", "Links this room to the next map's start room (same name there). "
                    "Must differ from this map's start room landmark",
                    (("landmark", "kv", "targetname"), ("changelevel", "kv", "landmark"))),
    ],
    "CRESCENDO_BUTTON": [
        PresetField("Stages", "Waves, e.g. PANIC 1, DELAY 10, PANIC 2, TANK 1",
                    (("definition", "kv", "stages"),)),
        PresetField("Name", "Crescendo name the button starts",
                    (("definition", "kv", "name"), ("button", "output_param", "ScriptedPanicEvent"))),
    ],
    "TANK_AMBUSH": [
        PresetField("Spawns", "What appears: tank, witch, hunter, boomer, smoker, charger, jockey, "
                    "spitter or common", (("trigger", "output_param", "SpawnZombie"),)),
        PresetField("Spawner Name", "Name of the spawn point the trigger talks to",
                    (("spawner", "kv", "targetname"), ("trigger", "output_target", "SpawnZombie"))),
    ],
    "LADDER": [
        PresetField("Who Climbs", "everyone, or zombies (then survivors can not use it)",
                    (("volume", "kv", "team"),)),
    ],
    "GATE_BUTTON": [
        PresetField("Gate Name", "Name the button's output sends Open to",
                    (("gate", "kv", "targetname"), ("button", "output_target", "Open"))),
        PresetField("Direction", "down, up, +x, -x, +y or -y", (("gate", "kv", "direction"),)),
        PresetField("Distance", "Units to slide (auto = the gate's own size that way)",
                    (("gate", "kv", "movedistance"),)),
        PresetField("Seconds", "How long the slide takes", (("gate", "kv", "move_time"),)),
    ],
    "ZOMBIE_CLIMB": [
        PresetField("Name", "Pairs the bottom and top points (each Zombie Climb needs its own name)",
                    (("bottom", "kv", "climb"), ("top", "kv", "climb"))),
    ],
    "ZOMBIE_SPAWN_AREA": [
        PresetField("Nav Marks", "Nav attributes set inside the box (OBSCURED = hidden spot for commons)",
                    (("nav_region", "kv", "attributes"),)),
    ],
}


# ---------------------------------------------------------------- preview models
# What the game shows for entities that have no "model" keyvalue. Used only for
# previews in Blender; never written to the map.
W = "models/w_models/weapons/"
PREVIEW_MODELS = {
    "info_player_start": "models/survivors/survivor_gambler.mdl",
    "info_survivor_rescue": "models/survivors/survivor_gambler.mdl",
    "weapon_first_aid_kit_spawn": W + "w_eq_medkit.mdl",
    "weapon_pain_pills_spawn": W + "w_eq_painpills.mdl",
    "weapon_adrenaline_spawn": W + "w_eq_adrenaline.mdl",
    "weapon_defibrillator_spawn": W + "w_eq_defibrillator.mdl",
    "weapon_molotov_spawn": W + "w_eq_molotov.mdl",
    "weapon_pipe_bomb_spawn": W + "w_eq_pipebomb.mdl",
    "weapon_vomitjar_spawn": W + "w_eq_bile_flask.mdl",
    "weapon_spawn": W + "w_rifle_ak47.mdl",
    "weapon_item_spawn": W + "w_eq_painpills.mdl",
    "weapon_melee_spawn": "models/weapons/melee/w_crowbar.mdl",
    "weapon_pistol_spawn": W + "w_pistol_b.mdl",
    "weapon_pistol_magnum_spawn": W + "w_desert_eagle.mdl",
    "weapon_smg_spawn": W + "w_smg_uzi.mdl",
    "weapon_pumpshotgun_spawn": W + "w_shotgun.mdl",
    "weapon_autoshotgun_spawn": W + "w_autoshot_m4super.mdl",
    "weapon_rifle_spawn": W + "w_rifle_m16a2.mdl",
    "weapon_hunting_rifle_spawn": W + "w_sniper_mini14.mdl",
    "commentary_zombie_spawner": "models/infected/common_male01.mdl",
}
SURVIVOR_BY_ORDER = {"1": "survivor_gambler", "2": "survivor_producer", "3": "survivor_coach", "4": "survivor_mechanic"}
INFECTED_BY_POPULATION = {
    "tank": "hulk", "witch": "witch", "witch_bride": "witch_bride", "hunter": "hunter", "boomer": "boomer",
    "smoker": "smoker", "charger": "charger", "jockey": "jockey", "spitter": "spitter",
}


def preview_model(classname: str, keyvalues: dict[str, str]) -> str:
    """Model to show in Blender for an entity ('' = use a box)."""
    if keyvalues.get("model", "").endswith(".mdl"):
        return keyvalues["model"]
    if classname == "info_survivor_position":
        return f"models/survivors/{SURVIVOR_BY_ORDER.get(keyvalues.get('Order', '1'), 'survivor_gambler')}.mdl"
    if classname == "info_zombie_spawn":
        pop = keyvalues.get("population", "default").lower()
        return f"models/infected/{INFECTED_BY_POPULATION.get(pop, 'common_male01')}.mdl"
    d = CATALOG.get(classname)
    default = next((k.default for k in d.keys if k.key == "model"), "") if d else ""
    return default or PREVIEW_MODELS.get(classname, "")
