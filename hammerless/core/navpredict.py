"""Predict the nav mesh the game will generate for a map, before compiling it.

Runs our reimplementation of the game's generator (navgen / navareas) on the map's
VMF text and marks the result like Build & Play does in-game (start room, end room,
Zombie Spawn Areas: see nav.py), so navanalysis can report the path from start to
end, islands and drops exactly as it does for the game's own .nav file.
"""
from __future__ import annotations

from .collision import CollisionWorld
from .navareas import NAV_MESH_JUMP, Generator
from .navfile import NavArea, NavMesh
from .navgen import NAV_MESH_NO_MERGE
from .vmf import parse

# L4D2's nav_generate grows from landmarks and item/weapon spawns, not player spawns
# (verified in-game: a map with only survivor spawns fails with "No valid walkable seed
# positions"; build.py adds a landmark when a map has none).
SEED_CLASSES = {"info_landmark"}


def seed_positions(vmf_text: str) -> list[tuple[float, float, float]]:
    seeds = []
    for e in (b for b in parse(vmf_text) if b.name == "entity"):
        cls = e.get("classname", "")
        if cls in SEED_CLASSES or (cls.startswith("weapon_") and cls.endswith("_spawn")):
            origin = e.get("origin")
            if origin:
                seeds.append(tuple(float(v) for v in origin.split()))
    return seeds


def to_navmesh(areas, regions) -> NavMesh:
    """Our generator's areas as a NavMesh, with the spawn attributes Build & Play's marking
    script would set (every area whose centre lies in a region gets its bits)."""
    ids = {id(a): i + 1 for i, a in enumerate(areas)}
    mesh = NavMesh(analyzed=False)
    for a in areas:
        n = NavArea(ids[id(a)], a.attributes & ~(NAV_MESH_JUMP | NAV_MESH_NO_MERGE), a.nw, a.se, a.ne_z, a.sw_z)
        n.connections = [[ids[id(b)] for b in a.connect[d] if id(b) in ids] for d in range(4)]
        c = n.centre
        for r in regions:
            if all(r.mins[i] <= c[i] <= r.maxs[i] for i in range(3)):
                n.spawn_attributes |= r.bits
        mesh.areas.append(n)
    return mesh


def predict(vmf_text: str, regions, progress=None) -> NavMesh:
    """progress(stage: str, count: int) is called between stages (from any thread)."""
    gen = Generator(CollisionWorld.from_vmf(vmf_text))
    for p in seed_positions(vmf_text):
        gen.add_seed(p)
    steps = (("Sampling walkable space", gen.sample), ("Building areas", gen.create_areas),
             ("Connecting areas", gen.connect_areas), ("Marking jump areas", gen.mark_jump_areas),
             ("Merging areas", gen.merge_areas), ("Splitting areas under overhangs", gen.split_areas_under_overhangs),
             ("Squaring up areas", gen.square_up_areas), ("Marking stairs", gen.mark_stair_areas),
             ("Removing jump areas", gen.stitch_and_remove_jump_areas))
    for label, step in steps:
        if progress:
            progress(label, len(gen.nodes))
        step()
    return to_navmesh(gen.areas, regions)
