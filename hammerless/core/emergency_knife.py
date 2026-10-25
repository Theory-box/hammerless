"""Emergency knife inventory and native pin escape, independent of purchase prices."""

RUNTIME = r'''
::HL_KnifeUsers <- {};
::HL_KnifeNext <- 0.0;
::HL_KnifeState <- function(p) {
    if (HL_HumanKey(p) == "") return null;
    local id = p.GetPlayerUserId().tostring();
    if (!(id in HL_KnifeUsers)) HL_KnifeUsers[id] <- {
        count = 0, attacker = null, hold = -1.0, armed = false,
        pending = null, until = 0.0, hint = 0.0 };
    return HL_KnifeUsers[id];
};
::HL_KnifeCount <- function(p) {
    local s = HL_KnifeState(p);
    return s == null ? 0 : s.count;
};
::HL_KnifeGive <- function(p) {
    local s = HL_KnifeState(p);
    if (s == null || p.IsDead() || p.IsDying() || p.IsIncapacitated()
        || p.IsHangingFromLedge() || p.GetSpecialInfectedDominatingMe() != null
        || s.count >= 2) return false;
    s.count++;
    return true;
};
::HL_KnifeAttacker <- function(p) {
    local a = p.GetSpecialInfectedDominatingMe();
    if (a == null || !a.IsValid() || a.IsDead()) return null;
    local kind = a.GetZombieType();
    return kind == 1 || kind == 3 ? a : null;
};
::HL_KnifeThink <- function() {
    local now = Time();
    if (now < HL_KnifeNext) return;
    HL_KnifeNext = now + 0.05;
    local p = null, present = {};
    while ((p = Entities.FindByClassname(p, "player")) != null) {
        local s = HL_KnifeState(p);
        if (s == null) continue;
        local id = p.GetPlayerUserId().tostring();
        present[id] <- true;
        if (p.IsDead() || p.IsDying()) {
            s.count = 0; s.pending = null; s.attacker = null; s.hold = -1.0;
            s.armed = false;
            continue;
        }
        local a = HL_KnifeAttacker(p), down = (p.GetButtonMask() & 8192) != 0;
        if (s.pending != null) {
            if (p.GetSpecialInfectedDominatingMe() == null) {
                if (s.count > 0) s.count--;
                s.pending = null;
                ClientPrint(p, 4, "Emergency knife used | Remaining: " + s.count + "/2");
                s.hint = now + 2.0;
            } else if (now >= s.until) {
                s.pending = null;
                ClientPrint(p, 4, "Could not escape; knife kept. Release RELOAD to try again.");
                s.hint = now + 2.0;
            }
            s.hold = -1.0; s.armed = false;
            continue;
        }
        if (a != s.attacker) {
            s.attacker = a; s.hold = -1.0; s.armed = !down;
        }
        if (!down) { s.hold = -1.0; s.armed = true; }
        if (a == null || s.count <= 0 || p.IsHangingFromLedge()) {
            s.hold = -1.0;
            continue;
        }
        if (now >= s.hint) {
            ClientPrint(p, 4, "Emergency knives: " + s.count + "/2 | Hold RELOAD for 1 second to escape");
            s.hint = now + 1.5;
        }
        if (!down || !s.armed) continue;
        if (s.hold < 0) s.hold = now;
        if (now - s.hold < 1.0) continue;
        s.armed = false; s.hold = -1.0;
        // Recheck the exact attacker; never edit domination netprops directly.
        if (HL_KnifeAttacker(p) != a) continue;
        try {
            a.Stagger(p.GetOrigin());
            s.pending = a; s.until = now + 0.35;
        } catch (error) {
            printl("HAMMERLESS_KNIFE " + error);
        }
    }
    local gone = [];
    foreach (id, s in HL_KnifeUsers) if (!(id in present)) gone.append(id);
    foreach (id in gone) delete HL_KnifeUsers[id];
};
'''
