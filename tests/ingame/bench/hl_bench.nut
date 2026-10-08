// Hammerless test bench, the game side. Installed as scripts/vscripts/mapspawn.nut only while tests run
// (bench.py removes it): the game runs mapspawn.nut on every map load.
//
// Every 0.1 s it reads ems/hl_bench_in ("<id>\n<Squirrel code>"); a new id runs the code and writes
// ems/hl_bench_out ("<id>\nok\n<JSON of what it returned>" or "<id>\nerror\n<message>").
// ems/hl_bench_state says which map is loaded and how many times the bench has started.

::HLB <- { last = -1 };

// any value as JSON: entities and players as { ent, class, name }, vectors as [x, y, z]
::HLB_Json <- function(v) {
    local t = typeof v;
    if (v == null) return "null";
    if (t == "bool") return v ? "true" : "false";
    if (t == "integer" || t == "float") return v.tostring();
    if (t == "string") {
        local s = "";
        foreach (c in v) {
            if (c == '"') s += "\\\"";
            else if (c == '\\') s += "\\\\";
            else if (c == '\n') s += "\\n";
            else if (c < 32) s += " ";
            else s += c.tochar();
        }
        return "\"" + s + "\"";
    }
    if (t == "array") {
        local parts = [];
        foreach (x in v) parts.append(::HLB_Json(x));
        return "[" + ::HLB_Join(parts) + "]";
    }
    if (t == "table") {
        local parts = [];
        foreach (k, x in v) parts.append(::HLB_Json(k.tostring()) + ":" + ::HLB_Json(x));
        return "{" + ::HLB_Join(parts) + "}";
    }
    if (t == "Vector" || t == "QAngle") return "[" + v.x + "," + v.y + "," + v.z + "]";
    if (t == "instance") {
        try {
            if (!v.IsValid()) return "null";
            return "{\"ent\":" + v.GetEntityIndex() + ",\"class\":" + ::HLB_Json(v.GetClassname())
                 + ",\"name\":" + ::HLB_Json(v.GetName()) + "}";
        } catch (e) {}
    }
    return ::HLB_Json(v.tostring());
}
::HLB_Join <- function(parts) {
    local s = "";
    foreach (i, p in parts) s += (i ? "," : "") + p;
    return s;
}

::HLB_Tick <- function() {
    local s = FileToString("hl_bench_in");
    if (s == null) return 0.1;
    local nl = s.find("\n");
    if (nl == null) return 0.1;
    local id = s.slice(0, nl).tointeger();
    if (id == ::HLB.last) return 0.1;
    ::HLB.last = id;
    local out = "";
    try {
        out = "ok\n" + ::HLB_Json(compilestring(s.slice(nl + 1)).call(getroottable()));
    } catch (e) {
        out = "error\n" + e;
    }
    StringToFile("hl_bench_out", id + "\n" + out);
    return 0.1;
}

::HLB_Start <- function() {
    local ent = Entities.FindByName(null, "hl_bench");
    if (ent == null) ent = SpawnEntityFromTable("info_target", { targetname = "hl_bench" });
    ent.ValidateScriptScope();
    ent.GetScriptScope().Tick <- ::HLB_Tick;
    AddThinkToEnt(ent, "Tick");
    // a request already answered before this map loaded isn't run again
    local s = FileToString("hl_bench_in");
    if (s != null && s.find("\n") != null) ::HLB.last = s.slice(0, s.find("\n")).tointeger();
    local starts = 1;
    local old = FileToString("hl_bench_state");
    if (old != null) { local p = split(old, " "); if (p.len() > 1) starts = p[1].tointeger() + 1; }
    StringToFile("hl_bench_state", Director.GetMapName() + " " + starts);
    printl("HLBENCH ready " + Director.GetMapName() + " " + starts);
}
::HLB_Start();
