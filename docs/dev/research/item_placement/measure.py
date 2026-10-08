"""Measure where Valve puts items and weapons along each campaign map's route."""
import heapq
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import valve  # noqa: E402

PLAYER_START, CHECKPOINT, FINALE, RESCUE_VEHICLE, ESCAPE_ROUTE = 128, 2048, 64, 32768, 131072
ITEM_FLAGS = {"item1": "ammo", "item2": "kit", "item3": "molotov", "item4": "pills", "item5": "pipe",
              "item6": "oxygen", "item7": "propane", "item8": "gascan", "item11": "adrenaline", "item12": "defib",
              "item13": "bile", "item16": "chainsaw", "item17": "launcher", "item18": "m60"}
FIXED = {"weapon_first_aid_kit_spawn": "kit", "weapon_pain_pills_spawn": "pills", "weapon_adrenaline_spawn": "adrenaline",
         "weapon_molotov_spawn": "molotov", "weapon_pipe_bomb_spawn": "pipe", "weapon_vomitjar_spawn": "bile",
         "weapon_defibrillator_spawn": "defib", "weapon_ammo_spawn": "ammo",
         "weapon_upgradepack_incendiary_spawn": "upgradepack", "weapon_upgradepack_explosive_spawn": "upgradepack",
         "upgrade_spawn": "laser/upgrade", "weapon_melee_spawn": "melee", "weapon_chainsaw_spawn": "chainsaw",
         "weapon_pistol_spawn": "pistol", "weapon_pistol_magnum_spawn": "magnum",
         "weapon_grenade_launcher_spawn": "launcher", "weapon_rifle_m60_spawn": "m60"}
TIER1 = {"weapon_smg_spawn", "weapon_smg_silenced_spawn", "weapon_pumpshotgun_spawn", "weapon_shotgun_chrome_spawn",
         "weapon_smg_mp5_spawn"}
TIER2 = {"weapon_rifle_spawn", "weapon_rifle_desert_spawn", "weapon_rifle_ak47_spawn", "weapon_autoshotgun_spawn",
         "weapon_shotgun_spas_spawn", "weapon_hunting_rifle_spawn", "weapon_sniper_military_spawn",
         "weapon_rifle_sg552_spawn", "weapon_sniper_awp_spawn", "weapon_sniper_scout_spawn"}


def kind(e):
    c = e.get("classname", "")
    if c == "weapon_item_spawn":
        allowed = [v for k, v in ITEM_FLAGS.items() if e.get(k, "0").strip() not in ("", "0")]
        return "item:" + "+".join(sorted(allowed)) if allowed else None
    if c == "weapon_spawn":
        return "weapon:" + e.get("weapon_selection", "any_primary")
    if c in TIER1:
        return "weapon:tier1_fixed"
    if c in TIER2:
        return "weapon:tier2_fixed"
    return FIXED.get(c)


def vec(s):
    try:
        return np.array([float(x) for x in s.split()[:3]])
    except ValueError:
        return None


def analyse(name):
    ents = valve.entities(name)
    mesh = valve.nav(name)
    if mesh is None or not mesh.areas:
        return None
    areas = mesh.areas
    idx = {a.id: i for i, a in enumerate(areas)}
    lo = np.array([[min(a.nw[0], a.se[0]), min(a.nw[1], a.se[1])] for a in areas])
    hi = np.array([[max(a.nw[0], a.se[0]), max(a.nw[1], a.se[1])] for a in areas])
    z = np.array([(a.nw[2] + a.se[2] + a.ne_z + a.sw_z) / 4 for a in areas])
    centre = np.column_stack([(lo + hi) / 2, z])
    attr = np.array([a.spawn_attributes for a in areas])
    adj = [[] for _ in areas]
    for i, a in enumerate(areas):
        for d in a.connections:
            for j in d:
                if j in idx:
                    k = idx[j]
                    w = float(np.linalg.norm(centre[i] - centre[k]))
                    adj[i].append((k, w))
                    adj[k].append((i, w))
    for lad in mesh.ladders:                          # ladders join their top and bottom areas
        ends = [x for x in (lad.bottom_area, lad.top_forward, lad.top_left, lad.top_right, lad.top_behind) if x in idx]
        for a_ in ends:
            for b_ in ends:
                if a_ != b_:
                    w = float(np.linalg.norm(centre[idx[a_]] - centre[idx[b_]]))
                    adj[idx[a_]].append((idx[b_], w))

    def dijkstra(sources):
        dist = np.full(len(areas), np.inf)
        h = []
        for s in sources:
            dist[s] = 0
            h.append((0.0, s))
        heapq.heapify(h)
        prev = np.full(len(areas), -1)
        while h:
            d, i = heapq.heappop(h)
            if d > dist[i]:
                continue
            for k, w in adj[i]:
                if d + w < dist[k]:
                    dist[k] = d + w
                    prev[k] = i
                    heapq.heappush(h, (d + w, k))
        return dist, prev

    start = list(np.where(attr & PLAYER_START)[0])
    if not start:                                     # later chapters: the start saferoom under the player start
        spots = [vec(e.get("origin", "")) for e in ents if e.get("classname") == "info_player_start"]
        spots = [p for p in spots if p is not None]
        if not spots:                                 # the landmark the previous chapter's level change names
            names = set()
            for other in valve.maps():
                if other == name:
                    continue
                for e in valve.entities(other):
                    if e.get("classname") == "info_changelevel" and e.get("map", "").lower() == name:
                        names.add(e.get("landmark", "").lower())
            spots = [vec(e["origin"]) for e in ents if e.get("classname") == "info_landmark"
                     and e.get("targetname", "").lower() in names]
            spots = [p for p in spots if p is not None]
        seeds = set()
        for p in spots:
            d = np.linalg.norm(centre - p, axis=1)
            seeds.add(int(np.argmin(d)))
        stack = [s for s in seeds if attr[s] & CHECKPOINT] or list(seeds)
        seen = set(stack)
        while stack:                                  # the whole checkpoint room
            i = stack.pop()
            if not attr[i] & CHECKPOINT:
                continue
            for k, _w in adj[i]:
                if k not in seen and attr[k] & CHECKPOINT:
                    seen.add(k)
                    stack.append(k)
        start = sorted(seen | seeds)
    if not start:
        return None
    start_set = set(start)
    flow, prev = dijkstra(start)
    reach = np.isfinite(flow)
    finale = bool((attr & (FINALE | RESCUE_VEHICLE)).any())
    cp = [i for i in np.where(reach & ((attr & CHECKPOINT) > 0))[0] if i not in start_set]
    rooms, seen = [], set()
    for i in cp:                                      # checkpoint areas grouped into rooms
        if i in seen:
            continue
        room, stack = [], [i]
        seen.add(i)
        while stack:
            j = stack.pop()
            room.append(j)
            for k, _w in adj[j]:
                if k not in seen and k not in start_set and attr[k] & CHECKPOINT and reach[k]:
                    seen.add(k)
                    stack.append(k)
        rooms.append(room)
    rooms = [r for r in rooms if len(r) >= 2] or rooms
    if finale and (attr & RESCUE_VEHICLE).any():
        goal_set = np.where(reach & ((attr & RESCUE_VEHICLE) > 0))[0]
    elif rooms:
        goal_set = np.array(max(rooms, key=lambda r: flow[r].min()))   # the farthest saferoom
    else:
        goal_set = np.array([int(np.argmax(np.where(reach, flow, -1)))])
    goal = int(goal_set[np.argmin(flow[goal_set])])
    length = float(flow[goal])
    path = []
    k = goal
    while k >= 0:
        path.append(k)
        k = prev[k]
    off, _ = dijkstra(path)
    path_pts = centre[path]
    nav_area = float(((hi - lo).prod(1)).sum())
    params = next((e for e in ents if e.get("classname") == "info_map_parameters"), {})

    def area_of(p):
        inside = np.where((lo[:, 0] - 1 <= p[0]) & (p[0] <= hi[:, 0] + 1) & (lo[:, 1] - 1 <= p[1]) & (p[1] <= hi[:, 1] + 1)
                          & (z <= p[2] + 24) & (z >= p[2] - 160))[0]
        if len(inside):
            return int(inside[np.argmax(z[inside])])         # the floor right under it
        d = np.linalg.norm(centre - p, axis=1)
        return int(np.argmin(d)) if d.min() < 200 else -1

    items = []
    for e in ents:
        k = kind(e)
        p = vec(e.get("origin", ""))
        if k is None or p is None:
            continue
        a = area_of(p)
        if a < 0 or not np.isfinite(flow[a]):
            items.append({"kind": k, "unplaced": True})
            continue
        sf = int(e.get("spawnflags", "0") or 0)
        items.append({"kind": k, "pos": p.tolist(), "flow": float(flow[a]), "frac": float(flow[a] / length) if length else 0,
                      "off_path": float(off[a]), "height": float(p[2] - z[a]),
                      "path_dist": float(np.linalg.norm(path_pts - p, axis=1).min()),
                      "checkpoint": bool(attr[a] & CHECKPOINT), "start_room": a in start_set,
                      "must_exist": bool(sf & 2) if e["classname"] in ("weapon_item_spawn",) or e["classname"].endswith("_spawn") else False,
                      "count": e.get("count", ""), "no_director": e.get("spawn_without_director", "0"),
                      "class": e["classname"]})
    return {"map": name, "route_length": length, "finale": finale, "areas": len(areas), "items": items,
            "nav_area": nav_area, "params": params}


if __name__ == "__main__":
    out = []
    for m in valve.maps():
        try:
            r = analyse(m)
        except Exception as ex:                           # report and keep going
            print("!!", m, ex)
            continue
        if r is None:
            print("-- no nav/start:", m)
            continue
        print(f"{m:28s} route {r['route_length']:8.0f}  items {len(r['items'])}  finale {r['finale']}")
        out.append(r)
    json.dump(out, open("measured.json", "w"))
