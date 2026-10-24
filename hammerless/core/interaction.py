"""Reusable interaction, identity and inventory primitives; no economy rules here."""

STORAGE = r'''
::HL_KeyVars <- {};
::HL_HumanKey <- function(p) {
    if (p == null || !p.IsValid() || !p.IsPlayer() || !p.IsSurvivor() || IsPlayerABot(p)) return "";
    local id = p.GetNetworkIDString();
    if (id != null && id != "" && id != "BOT" && id != "STEAM_ID_PENDING"
        && id != "STEAM_ID_LAN" && id != "VALVE_ID_LAN" && id != "UNKNOWN") return "steam:" + id;
    return "session:" + p.GetPlayerUserId();
};
::HL_KeyGet <- function(key, name, fallback) {
    if (key == null || key == "") return fallback;
    if (!(name in HL_KeyVars)) HL_KeyVars[name] <- {};
    if (!(key in HL_KeyVars[name])) HL_KeyVars[name][key] <- fallback;
    return HL_KeyVars[name][key];
};
::HL_KeySet <- function(key, name, value) {
    if (key == null || key == "") return;
    if (!(name in HL_KeyVars)) HL_KeyVars[name] <- {};
    HL_KeyVars[name][key] <- value;
};
'''

RUNTIME = r'''
::HL_Interactions <- [];
::HL_InteractionUsers <- {};
::HL_Deliveries <- {};
::HL_InteractionNext <- 0.0;
::HL_HasInventory <- function(p, item) {
    if (p == null || !p.IsValid() || item == null) return false;
    local inventory = {};
    GetInvTable(p, inventory);
    foreach (_, held in inventory)
        if (held != null && held.IsValid() && held.GetClassname() == item.classname
            && (item.matchmodel == "" || held.GetModelName().tolower() == item.matchmodel)) return true;
    return false;
};
// Deferred outputs retain the triggering graph context, not another player's latest event.
::HL_InContext <- function(ctx, results, loops, indices, callback) {
    local c = HL_Ctx, r = HL_R, l = HL_L, i = HL_I;
    HL_Ctx = clone ctx; HL_R = clone results; HL_L = clone loops; HL_I = clone indices;
    try { callback(); } catch (error) {
        HL_Ctx = c; HL_R = r; HL_L = l; HL_I = i; throw error;
    }
    HL_Ctx = c; HL_R = r; HL_L = l; HL_I = i;
};
::HL_GiveInventory <- function(p, item, success, failure) {
    local key = HL_HumanKey(p), c = clone HL_Ctx, r = clone HL_R, l = clone HL_L, i = clone HL_I;
    local fail = function() { HL_InContext(c, r, l, i, failure); };
    if (key == "" || item == null || key in HL_Deliveries) { fail(); return; }
    HL_Deliveries[key] <- { player = p, item = item, until = Time() + 0.5,
        success = function() { HL_InContext(c, r, l, i, success); }, failure = fail };
    try { p.GiveItem(item.id); }
    catch (error) { delete HL_Deliveries[key]; fail(); printl("HAMMERLESS_INVENTORY " + error); }
};
::HL_InteractionEvent <- function(target, p, key, used, lost = false) {
    local old = HL_Ctx;
    HL_Ctx = { player = p, key = key, used = used, lost = lost };
    try { target.event(); } catch (error) { HL_Ctx = old; throw error; }
    HL_Ctx = old;
};
::HL_InteractionThink <- function() {
    // Buttons every server tick; expensive targeting/delivery checks stay at 10 Hz.
    local now = Time(), scan = now >= HL_InteractionNext;
    if (scan) HL_InteractionNext = now + 0.1;
    local complete = [];
    if (scan) foreach (key, order in HL_Deliveries) {
        local valid = order.player != null && order.player.IsValid() && HL_HumanKey(order.player) == key;
        if (valid && HL_HasInventory(order.player, order.item)) complete.append([key, true]);
        else if (!valid || Time() >= order.until) complete.append([key, false]);
    }
    foreach (row in complete) {
        local order = HL_Deliveries[row[0]];
        delete HL_Deliveries[row[0]];
        if (row[1]) order.success(); else order.failure();
    }
    local present = {}, p = null;
    while ((p = Entities.FindByClassname(p, "player")) != null) {
        local key = HL_HumanKey(p);
        if (key == "") continue;
        local id = p.GetPlayerUserId().tostring(), down = (p.GetButtonMask() & 32) != 0;
        present[id] <- true;
        if (!(id in HL_InteractionUsers)) HL_InteractionUsers[id] <- {
            down = false, pending = false, until = 0.0, spent = false, next = 0.0, focus = null };
        local state = HL_InteractionUsers[id], fresh = down && !state.down;
        if (fresh) { state.pending = true; state.until = now + 0.2; state.spent = false; }
        if (!down) state.spent = false;
        if (!down && now > state.until) state.pending = false;
        state.down = down;
        if (key in HL_Deliveries || p.IsDead() || p.IsDying() || p.IsIncapacitated()
            || p.IsHangingFromLedge() || p.GetSpecialInfectedDominatingMe() != null) {
            state.pending = false; state.spent = down;
            if (state.focus != null) HL_InteractionEvent(state.focus, p, key, false, true);
            state.focus = null;
            continue;
        }
        if (!scan && !fresh) continue;
        local eye = p.EyePosition(), forward = HL_Fwd(p.EyeAngles()), best = null, distance = 1e30;
        local reach = 0.0;
        foreach (target in HL_Interactions) {
            local r = target.range();
            if (r > reach) reach = r;
        }
        local aimed = { start = eye, end = eye + forward * reach, ignore = p, mask = 1174421507 };
        TraceLine(aimed);
        // Surface hits take precedence; a wide brush's centre need not be in reach.
        if ("hit" in aimed && aimed.hit && "enthit" in aimed) {
            foreach (target in HL_Interactions) {
                local ent = target.entity();
                if (ent != null && ent.IsValid() && ent == aimed.enthit
                    && (aimed.pos - eye).Length() <= target.range()) { best = target; break; }
            }
        }
        // Non-solid display props cannot be hit by a trace: keep their forgiving aim cone.
        // Brush targets must be hit on a real surface (not across a gap or while hidden).
        if (best == null)
        foreach (target in HL_Interactions) {
            local ent = target.entity();
            if (ent == null || !ent.IsValid()) continue;
            if (ent.GetClassname().find("func_") == 0) continue;
            local delta = ent.GetCenter() - eye, length = delta.Length();
            if (length < 1 || length > target.range() || length >= distance) continue;
            if (delta.Dot(forward) / length < 0.97) continue;
            local trace = { start = eye, end = ent.GetCenter(), ignore = p, mask = 1174421507 };
            TraceLine(trace);
            if ("hit" in trace && trace.hit && (!("enthit" in trace) || trace.enthit != ent)) continue;
            best = target; distance = length;
        }
        if (best != state.focus) {
            if (state.focus != null) HL_InteractionEvent(state.focus, p, key, false, true);
            state.focus = best;
            state.next = 0.0;
        }
        if (best == null) continue;
        local pressed = state.pending || (down && !state.spent);
        if (pressed || now >= state.next) {
            if (pressed) {
                state.pending = false; state.spent = true;
            }
            HL_InteractionEvent(best, p, key, pressed);
            state.next = now + (pressed ? 2.0 : 0.75);
        }
    }
    local gone = [];
    foreach (id, state in HL_InteractionUsers) if (!(id in present)) gone.append(id);
    foreach (id in gone) delete HL_InteractionUsers[id];
};
'''
