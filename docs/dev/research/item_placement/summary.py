"""Valve's item system, measured: density settings, candidates per spawned item, where candidates go."""
import json
from collections import Counter

import numpy as np

D = json.load(open("measured.json"))
SQ = 3600.0 ** 2                 # (100 yards)^2 in square units

# item type -> (info_map_parameters key, default, how a spawner offers it)
TYPES = {
    "pills": ("PainPillDensity", 6.48), "adrenaline": ("AdrenalineDensity", 6.48),
    "molotov": ("MolotovDensity", 6.48), "pipe": ("PipeBombDensity", 6.48), "bile": ("VomitJarDensity", 6.48),
    "defib": ("DefibrillatorDensity", 3.0), "ammo": ("AmmoDensity", 6.48), "melee": ("MeleeWeaponDensity", 6.48),
    "pistol": ("PistolDensity", 6.48), "upgradepack": ("UpgradepackDensity", 1.0),
    "chainsaw": ("ChainsawDensity", 1.0),
}
CLASS_OFFERS = {"weapon_pain_pills_spawn": {"pills"}, "weapon_adrenaline_spawn": {"adrenaline"},
                "weapon_molotov_spawn": {"molotov"}, "weapon_pipe_bomb_spawn": {"pipe"},
                "weapon_vomitjar_spawn": {"bile"}, "weapon_defibrillator_spawn": {"defib"},
                "weapon_ammo_spawn": {"ammo"}, "weapon_melee_spawn": {"melee"}, "weapon_pistol_spawn": {"pistol"},
                "weapon_upgradepack_incendiary_spawn": {"upgradepack"}, "weapon_upgradepack_explosive_spawn": {"upgradepack"},
                "weapon_chainsaw_spawn": {"chainsaw"}, "weapon_grenade_launcher_spawn": {"chainsaw"},
                "weapon_rifle_m60_spawn": {"chainsaw"}, "weapon_first_aid_kit_spawn": {"kit"}}


def offers(r):
    if r["class"] == "weapon_item_spawn":
        s = set(r["kind"][5:].split("+"))
        return {("chainsaw" if x in ("launcher", "m60") else x) for x in s}
    return CLASS_OFFERS.get(r["class"], set())


def num(v, d):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def clusters(rs, rng):
    """Spawners within rng of each other (chained) form one cluster."""
    if not rs:
        return []
    P = np.array([r["pos"] for r in rs])
    left = set(range(len(rs)))
    out = []
    while left:
        i = left.pop()
        group, stack = [i], [i]
        while stack:
            j = stack.pop()
            near = [k for k in list(left) if np.linalg.norm(P[k] - P[j]) <= max(rng, 1)]
            for k in near:
                left.remove(k)
                group.append(k)
                stack.append(k)
        out.append([rs[k] for k in group])
    return out


print("== per-map density settings Valve uses (median, [25%-75%]) and what they mean for a map of median size")
areas = np.array([m["nav_area"] / SQ for m in D])
print(f"  walkable area: median {np.median(areas):.2f} x (100 yd)^2  [{np.percentile(areas, 25):.2f}-{np.percentile(areas, 75):.2f}]")
for t, (key, dflt) in TYPES.items():
    v = np.array([num(m["params"].get(key), dflt) for m in D if m["params"]])
    print(f"  {key:22s} median {np.median(v):5.2f} [{np.percentile(v, 25):.2f}-{np.percentile(v, 75):.2f}]   default {dflt}")
for key, dflt in (("ItemClusterRange", 50), ("ConfigurableWeaponDensity", -1), ("ConfigurableWeaponClusterRange", 100),
                  ("FinaleItemClusterCount", 3), ("MagnumDensity", -1)):
    print(f"  {key:30s}", Counter(m["params"].get(key, f"(default {dflt})") for m in D).most_common(4))

print("\n== candidate clusters Valve places per item the density asks for (outside saferooms, non-finale)")
print("   (expected = density x walkable area; a cluster = spawners offering it within ItemClusterRange)")
for t, (key, dflt) in TYPES.items():
    ratios, sizes, must = [], [], []
    for m in D:
        if m["finale"]:
            continue
        dens = num(m["params"].get(key), dflt)
        expected = dens * m["nav_area"] / SQ
        rs = [r for r in m["items"] if not r.get("unplaced") and not r["checkpoint"] and t in offers(r)]
        cl = clusters(rs, num(m["params"].get("ItemClusterRange"), 50))
        if expected > 0.5 and cl:
            ratios.append(len(cl) / expected)
        sizes += [len(c) for c in cl]
        must += [r["must_exist"] for r in rs]
    if ratios:
        print(f"  {t:12s} candidates per expected item: median {np.median(ratios):5.1f} [{np.percentile(ratios, 25):.1f}-"
              f"{np.percentile(ratios, 75):.1f}]   spawners per cluster {np.mean(sizes):.1f}   must-exist {np.mean(must):.0%}")

print("\n== where candidates sit (outside saferooms): straight distance to the main route, height above the floor")
groups = {"pills/adrenaline": {"pills", "adrenaline"}, "throwables": {"molotov", "pipe", "bile"}, "kit": {"kit"},
          "ammo": {"ammo"}, "melee": {"melee"}, "defib": {"defib"}, "chainsaw/launcher/m60": {"chainsaw"},
          "upgradepack": {"upgradepack"}}
allr = [r for m in D for r in m["items"] if not r.get("unplaced")]
for g, s in groups.items():
    rs = [r for r in allr if not r["checkpoint"] and offers(r) & s]
    d = np.array([r["path_dist"] for r in rs])
    h = np.array([r["height"] for r in rs])
    if len(rs) >= 20:
        q = np.percentile(d, [25, 50, 75, 90])
        print(f"  {g:22s} n {len(rs):5d}  route dist 25/50/75/90%: {q[0]:4.0f} {q[1]:4.0f} {q[2]:4.0f} {q[3]:5.0f}"
              f"   floor {np.mean(h < 8):3.0%}  table {np.mean((h >= 24) & (h < 48)):3.0%}  other raised {np.mean(((h >= 8) & (h < 24)) | (h >= 48)):3.0%}")
w = [r for r in allr if not r["checkpoint"] and r["kind"].startswith("weapon:")]
for tier in ("tier1", "tier2", "any"):
    rs = [r for r in w if (tier in r["kind"]) or (tier == "tier2" and ("rifle" in r["kind"] or "sniper" in r["kind"] or "tier2_shotgun" in r["kind"]))
          or (tier == "tier1" and ("smg" in r["kind"] or "tier1_shotgun" in r["kind"]))]
    if rs:
        d = np.array([r["path_dist"] for r in rs])
        print(f"  weapons {tier:14s} n {len(rs):5d}  route dist median {np.median(d):4.0f}  along the chapter (fifths): "
              + " ".join(f"{x:3.0%}" for x in np.histogram(np.clip([r['frac'] for r in rs], 0, .999), 5, (0, 1))[0] / len(rs)))

print("\n== saferooms (non-finale, per room, median [max])")
for where, test in (("start", lambda r: r["start_room"]), ("end", lambda r: r["checkpoint"] and not r["start_room"])):
    for g, s in {"kits": {"kit"}, "ammo": {"ammo"}, "melee": {"melee"}, "pistols": {"pistol"}}.items():
        c = [sum(1 for r in m["items"] if not r.get("unplaced") and test(r) and offers(r) & s) for m in D if not m["finale"]]
        print(f"  {where:5s} {g:8s} {np.median(c):3.0f} [{max(c)}]", end="   ")
    c = [sum(1 for r in m["items"] if not r.get("unplaced") and test(r) and r["kind"].startswith("weapon:")) for m in D if not m["finale"]]
    print(f"weapons {np.median(c):3.0f} [{max(c)}]")
