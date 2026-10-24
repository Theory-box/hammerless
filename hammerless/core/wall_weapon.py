"""Wall purchases using the model as a display, never as a free pickup."""

import json
import re
from pathlib import Path

# Item/model identities from Valve's shipped weapon and melee scripts.
ITEMS = json.loads((Path(__file__).with_name("data") / "purchase_items.json").read_text(encoding="utf-8"))
WEAPONS = {v["model"]: (k, v["title"]) for k, v in ITEMS.items()}
WEAPONS["models/w_models/weapons/w_pistol_b.mdl"] = ("pistol", "Pistol")


def resolve(settings, model):
    choice = settings.get("item", "AUTO")
    if choice == "AUTO":
        if model not in WEAPONS:
            raise ValueError("Choose Item to Give explicitly, or use a supported weapon/item world model for Auto")
        key, _ = WEAPONS[model]
    elif choice == "CUSTOM":
        key = settings.get("custom_item", "").strip().lower().removeprefix("weapon_")
    else:
        key = choice
    if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
        raise ValueError("Item to Give must be an item ID such as rifle_ak47 or first_aid_kit")
    if key in ("health", "ammo") or key.endswith("_spawn"):
        raise ValueError("Use a carried item ID, not health/ammo commands or a spawn entity")
    data = dict(ITEMS.get(key, dict(title=key.replace("_", " ").title(), classname="weapon_" + key, match_model="")))
    if choice == "CUSTOM":
        data["classname"] = settings.get("inventory_class", "").strip() or data["classname"]
        data["match_model"] = settings.get("inventory_model", "").strip().replace("\\", "/").lower() or data["match_model"]
    if not re.fullmatch(r"weapon_[a-z0-9_]+", data["classname"]):
        raise ValueError("Inventory Class must be a weapon_ classname")
    data["title"] = settings.get("title", "").strip() or data["title"]
    return key, data


RUNTIME = r'''
::HL_WallStations <- [];
::HL_WallUsers <- {};
::HL_WallNext <- 0.0;
::HL_WallHasWeapon <- function(p, station) {
    local inventory = {};
    GetInvTable(p, inventory);
    foreach (_, item in inventory)
        if (item != null && item.IsValid() && item.GetClassname() == station.classname
            && (station.matchmodel == "" || item.GetModelName().tolower() == station.matchmodel)) return true;
    return false;
};
::HL_WallMessage <- function(p, state, text) {
    ClientPrint(p, 4, text);
    state.prompt = Time() + 2.0;
};
::HL_WallBuy <- function(p, state, station) {
    if (!HL_WallPointsEnabled) {
        HL_WallMessage(p, state, "Purchases unavailable: add Personal Points to this map");
        return;
    }
    local key = HL_PointsKey(p);
    if (key == null) return;
    if (HL_WallHasWeapon(p, station)) {
        HL_WallMessage(p, state, "Already carrying " + station.title);
        return;
    }
    local balance = HL_PointsGet(p);
    if (balance < station.price) {
        HL_WallMessage(p, state, "Not enough points: " + station.price + " required | You have " + balance);
        return;
    }
    // Reserve before delivery, then confirm inventory on subsequent ticks. Only one pending purchase per human.
    HL_PointsBalances[key] = balance - station.price;
    state.pending = { key = key, station = station, until = Time() + 0.5 };
    try { p.GiveItem(station.weapon); }
    catch (error) {
        HL_PointsBalances[key] += station.price;
        state.pending = null;
        HL_WallMessage(p, state, "Could not give item; no points charged");
        printl("HAMMERLESS_WALL_WEAPON " + error);
    }
};
::HL_WallThink <- function() {
    if (Time() < HL_WallNext) return;
    HL_WallNext = Time() + 0.1;
    local present = {}, p = null;
    while ((p = Entities.FindByClassname(p, "player")) != null) {
        if (!p.IsSurvivor() || IsPlayerABot(p)) continue;
        local id = p.GetPlayerUserId().tostring(), down = (p.GetButtonMask() & 32) != 0;
        present[id] <- true;
        if (!(id in HL_WallUsers)) HL_WallUsers[id] <- { down = down, prompt = 0.0, pending = null };
        local state = HL_WallUsers[id], pressed = down && !state.down;
        state.down = down;
        if (state.pending != null) {
            local order = state.pending;
            if (HL_WallHasWeapon(p, order.station)) {
                state.pending = null;
                HL_WallMessage(p, state, "Bought " + order.station.title + " | Points: " + HL_PointsGet(p));
            } else if (Time() >= order.until) {
                HL_PointsBalances[order.key] += order.station.price;
                state.pending = null;
                HL_WallMessage(p, state, "Could not give item; points refunded");
            }
            continue;
        }
        if (p.IsDead() || p.IsDying() || p.IsIncapacitated() || p.IsHangingFromLedge()
            || p.GetSpecialInfectedDominatingMe() != null) continue;
        local eye = p.EyePosition(), angles = p.EyeAngles();
        local pitch = angles.x * 0.01745329252, yaw = angles.y * 0.01745329252;
        local forward = Vector(cos(pitch) * cos(yaw), cos(pitch) * sin(yaw), -sin(pitch));
        local best = null, distance = 999999.0;
        foreach (station in HL_WallStations) {
            local model = Entities.FindByName(null, station.name);
            if (model == null || !model.IsValid()) continue;
            local delta = model.GetCenter() - eye, length = delta.Length();
            if (length < 1 || length > station.range || length >= distance) continue;
            if (delta.Dot(forward) / length < 0.97) continue;
            local trace = { start = eye, end = model.GetCenter(), ignore = p, mask = 24705 };
            TraceLine(trace);
            if ("hit" in trace && trace.hit && (!("enthit" in trace) || trace.enthit != model)) continue;
            best = station;
            distance = length;
        }
        if (best == null) continue;
        if (pressed) HL_WallBuy(p, state, best);
        else if (Time() >= state.prompt) {
            local balance = HL_WallPointsEnabled ? HL_PointsGet(p) : 0;
            ClientPrint(p, 4, best.title + " - " + best.price + " points | You: " + balance + " | Press USE to buy");
            state.prompt = Time() + 0.75;
        }
    }
    // Disconnect during delivery: cancel and refund the same wallet, never the next occupant of a slot.
    local removed = [];
    foreach (id, state in HL_WallUsers) if (!(id in present)) {
        if (state.pending != null && "HL_PointsBalances" in getroottable()) {
            local order = state.pending;
            if (order.key in HL_PointsBalances) HL_PointsBalances[order.key] += order.station.price;
        }
        removed.append(id);
    }
    foreach (id in removed) delete HL_WallUsers[id];
};
'''
