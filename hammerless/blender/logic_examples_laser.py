"""Laser example assembled from existing Timer, Self, settings, table and Script nodes."""
from .logic_examples import N

# Kept in the scene's Script node: the example has no hidden gameplay controller.
TICK = r'''
if (a == null || !a.IsValid()) return;
a.ValidateScriptScope();
local min = function(x,y) { return x < y ? x : y; };
local max = function(x,y) { return x > y ? x : y; };
local scope = a.GetScriptScope();
local now = Time();
if (!("laser" in scope)) {
    scope.laser <- {remaining = max(0.0, b.budget.tofloat()), last = now, beam = null, start = null, finish = null};
}
local state = scope.laser;
local dt = min(0.5, max(0.0, now - state.last));
state.last = now;
if (state.remaining <= 0) {
    foreach (e in [state.beam, state.start, state.finish]) if (e != null && e.IsValid()) e.Kill();
    state.beam = null; state.start = null; state.finish = null;
    return;
}
local start = a.GetOrigin();
local limit = min(8192.0, max(1.0, b.range.tofloat()));
local finish = start + a.GetForwardVector() * limit;
// MASK_NPCSOLID_BRUSHONLY: walls/solid brushes stop the beam, infected do not.
local trace = {start = start, end = finish, ignore = a, mask = 16395};
TraceLine(trace);
if ("startsolid" in trace && trace.startsolid) {
    if (state.beam != null && state.beam.IsValid()) DoEntFire("!self", "TurnOff", "", 0.0, null, state.beam);
    return;
}
if ("hit" in trace && trace.hit) finish = trace.pos;
if (state.beam == null || !state.beam.IsValid()) {
    foreach (e in [state.start, state.finish]) if (e != null && e.IsValid()) e.Kill();
    local name = UniqueString("hl_laser_");
    PrecacheModel("sprites/laserbeam.vmt");
    state.start = SpawnEntityFromTable("info_target", {targetname = name + "_a", origin = start});
    state.finish = SpawnEntityFromTable("info_target", {targetname = name + "_b", origin = finish});
    state.beam = SpawnEntityFromTable("env_beam", {
        targetname = name, origin = start, LightningStart = name + "_a", LightningEnd = name + "_b",
        texture = "sprites/laserbeam.vmt", rendercolor = "255 40 40", renderamt = 255,
        BoltWidth = 2, life = 0, damage = 0, spawnflags = 1
    });
}
if (state.start == null || state.finish == null || state.beam == null) return;
state.start.SetOrigin(start); state.finish.SetOrigin(finish);
DoEntFire("!self", "TurnOn", "", 0.0, null, state.beam);
local delta = finish - start;
if (delta.LengthSqr() < 0.01) return;
local radius = min(32.0, max(1.0, b.radius.tofloat()));
local perHit = min(10000.0, max(0.0, b.dps.tofloat())) * dt;
if (perHit <= 0) return;
// Slab intersection against EVERY damageable entity; characters use upright collision bounds.
local hits = []; local requested = 0.0;
for (local ent = Entities.First(); ent != null; ent = Entities.Next(ent)) {
    if (!ent.IsValid() || ent == a || ent.GetHealth() <= 0) continue;
    if (!NetProps.HasProp(ent, "m_Collision.m_vecMins") || !NetProps.HasProp(ent, "m_Collision.m_vecMaxs")) continue;
    if (NetProps.HasProp(ent, "m_takedamage") && NetProps.GetPropInt(ent, "m_takedamage") == 0) continue;
    local boundsMin = NetProps.GetPropVector(ent, "m_Collision.m_vecMins");
    local boundsMax = NetProps.GetPropVector(ent, "m_Collision.m_vecMaxs");
    local localStart = start - ent.GetOrigin(); local localDelta = delta;
    local cls = ent.GetClassname();
    // SOLID_BBOX is upright; brush/physics props use their rotated local box.
    if (cls != "player" && cls != "infected" && cls != "witch" &&
        (!NetProps.HasProp(ent, "m_Collision.m_nSolidType") || NetProps.GetPropInt(ent, "m_Collision.m_nSolidType") != 2)) {
        local ang = ent.GetAngles(); local rad = 0.0174532925199433;
        local sp = sin(ang.x*rad); local cp = cos(ang.x*rad);
        local sy = sin(ang.y*rad); local cy = cos(ang.y*rad);
        local sr = sin(ang.z*rad); local cr = cos(ang.z*rad);
        local forward = Vector(cp*cy, cp*sy, -sp);
        local left = Vector(sr*sp*cy-cr*sy, sr*sp*sy+cr*cy, sr*cp);
        local up = Vector(cr*sp*cy+sr*sy, cr*sp*sy-sr*cy, cr*cp);
        localStart = Vector(localStart.Dot(forward), localStart.Dot(left), localStart.Dot(up));
        localDelta = Vector(delta.Dot(forward), delta.Dot(left), delta.Dot(up));
    }
    local lo = boundsMin - Vector(radius, radius, radius);
    local hi = boundsMax + Vector(radius, radius, radius);
    local enter = 0.0; local leave = 1.0; local intersects = true;
    foreach (axis in ["x", "y", "z"]) {
        local direction = localDelta[axis]; local origin = localStart[axis];
        if (fabs(direction) < 0.00001) {
            if (origin < lo[axis] || origin > hi[axis]) { intersects = false; break; }
        } else {
            local t0 = (lo[axis] - origin) / direction;
            local t1 = (hi[axis] - origin) / direction;
            enter = max(enter, min(t0, t1)); leave = min(leave, max(t0, t1));
            if (enter > leave) { intersects = false; break; }
        }
    }
    if (!intersects) continue;
    // Do not let the beam's thickness reach through a thin wall beside it.
    local center = ent.GetCenter();
    local t = max(0.0, min(1.0, (center-start).Dot(delta) / delta.LengthSqr()));
    local sight = {start = start + delta*t, end = center, mask = 16395, ignore = a};
    TraceLine(sight);
    local hitsTarget = "enthit" in sight && sight.enthit == ent;
    if (!hitsTarget && (("startsolid" in sight && sight.startsolid) || ("hit" in sight && sight.hit))) continue;
    local amount = min(perHit, ent.GetHealth().tofloat());
    hits.append({entity = ent, amount = amount}); requested += amount;
}
// Share the final partial charge fairly between all targets in this tick.
local share = requested > state.remaining ? state.remaining / requested : 1.0;
foreach (hit in hits) {
    local target = hit.entity;
    if (!target.IsValid() || target.GetHealth() <= 0) continue;
    local amount = min(state.remaining, hit.amount * share);
    target.TakeDamage(amount, 8, a);
    state.remaining = max(0.0, state.remaining - amount);
    if (state.remaining < 0.0001) state.remaining = 0.0;
}
if (state.remaining <= 0 && state.beam.IsValid()) DoEntFire("!self", "TurnOff", "", 0.0, null, state.beam);
result = state.remaining;
'''


def examples():
    settings = [('dps', 'Damage Per Second', 40), ('budget', 'Total Damage Budget', 1000),
                ('range', 'Range (game units)', 1024), ('radius', 'Hit Radius (game units)', 8)]
    nodes = [N('self', 'HL_NodeSelf', 0, 0),
             N('timer', 'HL_NodeTimer', 1, 0, seconds=0.1),
             N('settings', 'HL_NodeMakeTable', 2, 2, fields=', '.join(k+': num' for k,_,_ in settings)),
             N('tick', 'HL_NodeScriptCode', 3, 0, label='Laser: all contacts and damage budget', code=TICK)]
    links = [('self.entity', 'tick.a'), ('timer.tick', 'tick.run'), ('settings.table', 'tick.b')]
    for i,(key,title,value) in enumerate(settings):
        nodes.append(N(key, 'HL_NodeExposedSetting', 0, i+2, ui_name=title, value_type='INT', default_integer=value))
        links.append((key+'.value', 'settings.f_'+key))
    about = ('One emitter firing along its local +X (red) axis. Place slightly in front of the wall and rotate to aim. '
             'Fire-type damage to every damageable entity touching the beam, including survivors, infected and breakable props. '
             'Walls and solid brushes stop it. Damage spent drains its own budget; idle time costs nothing. '
             'Budget resets on map/round restart. Edit settings in Behaviours or edit the Timer and Script nodes. '
             'No points purchase or stagger in this first version.')
    return [dict(title='Laser Trap - Damage', level='Game Modes', about=about,
        add_menu=dict(id='laser_trap', label='Laser Trap', description=about),
        objects={'laser_emitter': ('ENTITY:info_target', (0,0,1))},
        behaviours={'laser_emitter': 'Laser Trap'},
        groups={'Laser Trap': dict(behaviour=True, about=about, nodes=nodes, links=links)},
        nodes=[], links=[])]
