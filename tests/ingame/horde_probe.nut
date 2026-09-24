local d = null;
while ((d = Entities.FindByClassname(d, "prop_door_rotating_checkpoint")) != null)
    printl("HLHORDE door " + d.GetName() + " spawnflags=" + NetProps.GetPropInt(d, "m_spawnflags"));
printl("HLHORDE commons_before=" + Director.GetCommonInfectedCount());
local i = 0; local p = null;
while ((p = Entities.FindByClassname(p, "player")) != null)
    if (p.IsSurvivor()) { p.SetOrigin(Vector(700 + i * 40, -100 + i * 40, 60)); i++; }
printl("HLHORDE moved " + i + " into trigger area");
