"""What blocks sight in the compiled map: the world the game's nav analysis traces against
(MASK_NAV_VISION = solid | moveable | blocklos, hitting every entity except players and NPCs).

Brush contents follow vbsp: FindMiptex (contents from the material's compile flags and opacity),
the per-side defaults in map.cpp (a side with no visible contents and no clip is solid) and
BrushContents (a window / grate / water / slime side makes the whole brush translucent and not
solid). So a chain-link fence ($alphatest) stops players but not sight, a clip brush stops
players but not sight, and a nodraw wall blocks it.
"""
from __future__ import annotations

from .collision import (CollisionBrush, CollisionWorld, brush_from_vmf_sides, displacement_brushes)

CONTENTS_SOLID = 0x1
CONTENTS_WINDOW = 0x2
CONTENTS_GRATE = 0x8
CONTENTS_SLIME = 0x10
CONTENTS_WATER = 0x20
CONTENTS_BLOCKLOS = 0x40
CONTENTS_OPAQUE = 0x80
ALL_VISIBLE_CONTENTS = 0xFF
CONTENTS_MOVEABLE = 0x4000
CONTENTS_PLAYERCLIP = 0x10000
CONTENTS_MONSTERCLIP = 0x20000
CONTENTS_ORIGIN = 0x1000000
CONTENTS_DETAIL = 0x8000000
CONTENTS_TRANSLUCENT = 0x10000000
CONTENTS_LADDER = 0x20000000
MASK_BLOCKLOS = CONTENTS_SOLID | CONTENTS_MOVEABLE | CONTENTS_BLOCKLOS

# brush entities that are solid while the game analyzes the nav (they sit at their start position)
SOLID_BRUSH_CLASSES = {"func_wall", "func_wall_toggle", "func_brush", "func_movelinear", "func_door",
                       "func_door_rotating", "func_button", "func_rot_button", "func_breakable",
                       "func_breakable_surf", "func_physbox", "func_tracktrain", "func_rotating",
                       "func_platrot", "func_elevator"}
WORLD_CLASSES = {"func_detail"}            # merged into the world by vbsp
# solid brush entities the game traces through their physics model (SOLID_VPHYSICS) rather than
# their BSP model: a trace that starts inside one is stuck (fraction 0) instead of passing
PHYSICS_BRUSH_CLASSES = {"func_movelinear", "func_brush", "func_door", "func_door_rotating", "func_physbox",
                         "func_breakable", "func_tracktrain", "func_rotating", "func_wall_toggle"}


def _true(params: dict, key: str) -> bool:
    """vbsp StringIsTrue: 'true' or '1' (any case)."""
    v = params.get(key)
    return v is not None and v.strip().lower() in ("true", "1")


def _opacity(params: dict) -> str:
    """UTILMATLIB_OPACITY: translucent, alpha tested or opaque, from the material's parameters."""
    if _true(params, "$translucent") or _true(params, "$additive"):
        return "translucent"
    alpha = params.get("$alpha")
    if alpha is not None:
        try:
            if float(alpha.strip()) < 1.0:
                return "translucent"
        except ValueError:
            pass
    if _true(params, "$alphatest"):
        return "alphatest"
    if params.get("shader", "") in ("water", "refract", "sprite", "spritecard"):
        return "translucent"
    return "opaque"


def material_contents(params: dict) -> tuple[int, bool]:
    """(contents, hint_or_skip) for a material's parameters, like vbsp's FindMiptex."""
    if not params:
        return 0, False
    c = 0
    if _true(params, "%compilesky") or _true(params, "%compile2dsky"):
        return 0, False
    if _true(params, "%compilehint") or _true(params, "%compileskip"):
        return 0, True
    if _true(params, "%compileorigin"):
        return CONTENTS_ORIGIN | CONTENTS_DETAIL, False
    if _true(params, "%compileclip"):
        return CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP, False
    if _true(params, "%playerclip"):
        return CONTENTS_PLAYERCLIP, False
    if _true(params, "%compilenpcclip"):
        return CONTENTS_MONSTERCLIP, False
    if _true(params, "%compilenochop") or _true(params, "%compiletrigger"):
        return 0, False
    if _true(params, "%compilenolight") and not _true(params, "%compilewater"):
        return 0, False
    if _true(params, "%compileladder"):
        c |= CONTENTS_LADDER
    if _true(params, "%compilepassbullets"):
        c = (c & ~CONTENTS_SOLID) | CONTENTS_GRATE
    if _true(params, "%compileinvisible"):
        c = (c & ~CONTENTS_SOLID) | CONTENTS_GRATE
    check_window = True
    if _true(params, "%compilenonsolid"):
        c = CONTENTS_OPAQUE
        check_window = False
    if _true(params, "%compileblocklos"):
        c = CONTENTS_BLOCKLOS
        check_window = False
    if _true(params, "%compiledetail"):
        c |= CONTENTS_DETAIL
    if _true(params, "%compilewater"):
        c = (c & ~(CONTENTS_SOLID | CONTENTS_DETAIL)) | CONTENTS_WATER
    if _true(params, "%compileslime"):
        c = (c & ~(CONTENTS_SOLID | CONTENTS_DETAIL)) | CONTENTS_SLIME
    if check_window and _opacity(params) != "opaque":
        if not c & (CONTENTS_GRATE | CONTENTS_WATER):
            c |= CONTENTS_WINDOW
        c &= ~CONTENTS_SOLID
    return c, False


def side_contents(mat: tuple[int, bool]) -> int:
    """map.cpp: clips are detail; no visible contents and no clip means solid; hint/skip are empty."""
    c, hint = mat
    if hint:
        return 0
    if c & (CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP):
        c |= CONTENTS_DETAIL
    if not c & (ALL_VISIBLE_CONTENTS | CONTENTS_PLAYERCLIP | CONTENTS_MONSTERCLIP):
        c |= CONTENTS_SOLID
    return c


def brush_contents(sides: list[int]) -> int:
    """BrushContents: the first side's contents, made translucent and not solid when any side is
    a window, grate, water or slime."""
    if not sides:
        return 0
    contents, union = sides[0], 0
    for s in sides:
        union |= s
    transparent = union & (CONTENTS_WINDOW | CONTENTS_GRATE | CONTENTS_WATER | CONTENTS_SLIME)
    if transparent:
        contents |= transparent | CONTENTS_TRANSLUCENT
        contents &= ~CONTENTS_SOLID
    return contents


class MaterialContents:
    """Material name -> vbsp contents, read from the game's VMTs (cached)."""

    def __init__(self, content=None, game_dir: str | None = None):
        self.content, self.game_dir = content, game_dir
        self.cache: dict[str, tuple[int, bool]] = {}
        self.missing: set[str] = set()

    def __call__(self, material: str) -> tuple[int, bool]:
        key = material.lower().replace("\\", "/")
        if key not in self.cache:
            from .gamematerials import read_vmt
            params = read_vmt(self.content, key, self.game_dir) if (self.content or self.game_dir) else {}
            if not params:
                self.missing.add(key)       # vbsp: a missing material gives contents 0 -> solid
            self.cache[key] = material_contents(params)
        return self.cache[key]


def blocks_sight(solid_block, materials: MaterialContents, mask: int = MASK_BLOCKLOS) -> bool:
    """Does a VMF solid's compiled contents meet 'mask' (by default: does it block sight)?"""
    sides = [side_contents(materials(s.get("material", ""))) for s in solid_block.blocks("side")]
    return bool(brush_contents(sides) & mask)


def vision_world(vmf_text: str, materials: MaterialContents, split: bool = False):
    """The brushes that block the nav analysis' sight lines (world, detail, and solid brush
    entities at their start position). Displacements block sight like the floor they replace."""
    from .vmf import parse
    top = parse(vmf_text)
    brushes: list[CollisionBrush] = []

    def add_solids(block, owner):
        for solid in block.blocks("solid"):
            vmf_sides = solid.blocks("side")
            disps = [(s, s.blocks("dispinfo")[0]) for s in vmf_sides if s.blocks("dispinfo")]
            if disps:
                for side, disp in disps:
                    brushes.extend(displacement_brushes(side.get("plane"), disp))
                continue
            if not blocks_sight(solid, materials):
                continue
            b = brush_from_vmf_sides([(s.get("plane"), s.get("material", "")) for s in vmf_sides], owner)
            if b:
                brushes.append(b)

    for world in (b for b in top if b.name == "world"):
        add_solids(world, "world")
    physics: list[CollisionBrush] = []
    for ent in (b for b in top if b.name == "entity"):
        cls = (ent.get("classname") or "").lower()
        if cls in WORLD_CLASSES:
            add_solids(ent, cls)
        elif cls in SOLID_BRUSH_CLASSES:
            if cls == "func_brush" and (ent.get("Solidity") or "0").strip() == "1":
                continue                    # Solidity: Never Solid
            if split and cls in PHYSICS_BRUSH_CLASSES:
                n = len(brushes)
                add_solids(ent, cls)
                physics.extend(brushes[n:])
                del brushes[n:]
            else:
                add_solids(ent, cls)
    if split:
        world = [b for b in brushes if b.source in ("world", "func_detail")]
        bsp_ents = [b for b in brushes if b.source not in ("world", "func_detail")]
        return CollisionWorld(world), CollisionWorld(physics), CollisionWorld(bsp_ents)
    return CollisionWorld(brushes)
