local p = null;
while ((p = Entities.FindByClassname(p, "player")) != null)
    printl("HLSTATUS " + p.GetPlayerName() + " " + p.GetOrigin() + " eyes=" + p.EyeAngles());
local n = 0; local z = null;
while ((z = Entities.FindByClassname(z, "infected")) != null) n++;
printl("HLSTATUS common_infected=" + n);
