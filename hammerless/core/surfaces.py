"""Surface properties: friction, footstep/impact sounds, bullet decals.

In Source these come from a material's $surfaceprop. L4D2's list (with friction)
is read from the game's scripts/surfaceproperties_*.txt. A game material can get a
different surface without copying its texture, via a "patch" VMT that includes
the original and overrides $surfaceprop.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from .vmf import parse


@dataclass(frozen=True)
class Surface:
    name: str
    friction: float | None   # None = inherited / default (0.8)
    base: str | None


# Used when the game files can't be read. Values from L4D2's surfaceproperties.
FALLBACK = [
    Surface("concrete", 0.8, None), Surface("dirt", 0.8, None), Surface("grass", None, "dirt"),
    Surface("gravel", None, "dirt"), Surface("mud", 0.6, "dirt"), Surface("sand", None, "dirt"),
    Surface("snow", 0.35, "dirt"), Surface("ice", 0.1, None), Surface("metal", None, "solidmetal"),
    Surface("slipperymetal", 0.1, "metal"), Surface("wood", None, None), Surface("tile", 0.8, None),
    Surface("glass", 0.5, None), Surface("carpet", 0.8, "dirt"), Surface("rubber", 0.8, "dirt"),
    Surface("water", 0.8, None), Surface("slipperyslime", 0.1, "dirt"),
]

# The ones people usually want, listed first in menus.
COMMON = ["concrete", "dirt", "grass", "gravel", "mud", "sand", "snow", "ice", "metal", "slipperymetal",
          "wood", "tile", "glass", "carpet", "rubber", "water", "slipperyslime"]


def load_surfaces(content) -> list[Surface]:
    """content: vpk.GameContent (or None for the fallback list)."""
    if content is None:
        return list(FALLBACK)
    manifest = content.read("scripts/surfaceproperties_manifest.txt")
    files = re.findall(r'"file"\s+"([^"]+)"', manifest.decode("utf-8", "replace")) if manifest else []
    raw: dict[str, tuple[str | None, str | None]] = {}
    for f in files:
        data = content.read(f)
        if not data:
            continue
        for block in parse(data.decode("utf-8", "replace")):
            raw[block.name.lower()] = (block.get("base"), block.get("friction"))
    if not raw:
        return list(FALLBACK)

    def friction(name, depth=0):
        base, fr = raw.get(name, (None, None))
        if fr is not None:
            try:
                return float(fr)
            except ValueError:
                return None
        if base and depth < 8:
            return friction(base.lower(), depth + 1)
        return None

    out = [Surface(n, friction(n), b) for n, (b, _f) in raw.items()]
    order = {n: i for i, n in enumerate(COMMON)}
    out.sort(key=lambda s: (order.get(s.name, len(order)), s.name))
    return out


def friction_label(s: Surface) -> str:
    f = s.friction if s.friction is not None else 0.8
    feel = "icy" if f <= 0.15 else "slippery" if f < 0.5 else "grippy" if f >= 0.8 else "loose"
    return f"friction {f:g} ({feel})"


def patch_vmt(base_material: str, surfaceprop: str) -> str:
    return ('"patch"\n{\n'
            f'\t"include" "materials/{base_material}.vmt"\n'
            '\t"insert"\n\t{\n'
            f'\t\t"$surfaceprop" "{surfaceprop}"\n'
            '\t}\n}\n')


def write_patch_material(game_dir: str, patch_path: str, base_material: str, surfaceprop: str) -> str:
    full = os.path.join(game_dir, "materials", *patch_path.split("/")) + ".vmt"
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(patch_vmt(base_material, surfaceprop))
    return full


DEFAULT_DENSITY = 2000.0          # kg/m3: Source's "default" surface


def surface_density(content, name: str) -> float:
    """A surface property's density (kg/m3), following its base surfaces; the game's default if unknown."""
    if content is None:
        return DEFAULT_DENSITY
    manifest = content.read("scripts/surfaceproperties_manifest.txt")
    files = re.findall(r'"file"\s+"([^"]+)"', manifest.decode("utf-8", "replace")) if manifest else []
    raw: dict[str, tuple[str | None, str | None]] = {}
    for f in files:
        data = content.read(f)
        if data:
            for block in parse(data.decode("utf-8", "replace")):
                raw[block.name.lower()] = (block.get("base"), block.get("density"))
    name = (name or "default").lower()
    for _ in range(8):
        base, density = raw.get(name, (None, None))
        if density is not None:
            try:
                return float(density)
            except ValueError:
                break
        if not base:
            break
        name = base.lower()
    base, density = raw.get("default", (None, None))
    try:
        return float(density) if density is not None else DEFAULT_DENSITY
    except ValueError:
        return DEFAULT_DENSITY
