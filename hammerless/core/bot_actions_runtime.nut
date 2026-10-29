// Owned bot actions. Included once by the generated map script.
::HLBA <- {
    loops = {}, errors = [], collecting = null, scope = null, ticking = false,
    Report = function(message) {
        if (errors.find(message) != null) return;
        errors.append(message); if (errors.len() > 32) errors.remove(0);
        printl("[Hammerless Bot Actions] " + message);
    },
    Valid = function(bot) {
        try { return bot != null && bot.IsValid() && bot.GetHealth() > 0; }
        catch (e) { return false; }
    },
    IsBot = function(bot) {
        if (!Valid(bot)) return false;
        try {
            local c = bot.GetClassname();
            return c == "infected" || c == "witch" || (c == "player" && IsPlayerABot(bot));
        } catch (e) { return false; }
    },
    Call = function(def, name, ctx) {
        if (name in def && def[name] != null) def[name](ctx);
    },
    Snapshot = function(value, depth = 0) {
        if (depth > 16) throw "Action parameter nesting is too deep or cyclic";
        local kind=typeof value;
        if (kind == "table") {
            local copy={}; foreach(k,v in value) copy[k] <- Snapshot(v,depth+1); return copy;
        }
        if (kind == "array") {
            local copy=[]; foreach(v in value) copy.append(Snapshot(v,depth+1)); return copy;
        }
        if (kind == "Vector") return Vector(value.x,value.y,value.z);
        // Engine entity handles intentionally preserve identity.
        return value;
    },
    Start = function(id, interval, selectFn, collectFn, afterFn = null) {
        if (id in loops) return; // Repeated Start is idempotent.
        if ((typeof interval != "integer" && typeof interval != "float") ||
            interval != interval || interval < 0.05 || interval > 1.0e30) {
            Report("Bot loop interval must be a finite number of at least 0.05 seconds"); return;
        }
        loops[id] <- { id=id, interval=interval < 0.05 ? 0.05 : interval,
            selectFn=selectFn, collectFn=collectFn, afterFn=afterFn,
            next=0.0, stopped=false, active=[], requests=[] };
    },
    Stop = function(id) {
        if (!(id in loops)) return;
        local loop=loops[id]; if (loop.stopped) return; loop.stopped=true;
        if (!ticking) { ReleaseLoop(loop); delete loops[id]; }
    },
    StopAll = function() {
        local ids=[]; foreach(id,loop in loops) ids.append(id);
        foreach(id in ids) Stop(id);
    },
    Request = function(scope, applyid, bot, def, enabled, params = null) {
        local loop=collecting;
        if (loop == null || loop.stopped || scope == null || scope != this.scope || scope.loop != loop || scope.bot != bot) return;
        if (!enabled || !IsBot(bot)) return;
        if (typeof def != "table" || !("id" in def)) { Report("Invalid action definition"); return; }
        if ("channels" in def && typeof def.channels != "array") { Report("Action channels must be an array"); return; }
        foreach (r in loop.requests) {
            if (r.applyid == applyid && r.bot == bot) {
                r.rejected=true; Report("Action requested twice for the same bot and node"); return;
            }
        }
        local r={bot=bot, applyid=applyid, def=def, params=params == null ? {} : Snapshot(params), rejected=false};
        try {
            if ("supportsFn" in def && def.supportsFn != null && !def.supportsFn(bot)) {
                Report("Unsupported bot for action " + def.id); return;
            }
        } catch(e) { Report("Action support check failed: " + e); return; }
        loop.requests.append(r);
    },
    Same = function(a,b) { return a.bot == b.bot && a.applyid == b.applyid && a.def == b.def; },
    Clashes = function(a,b) {
        if (a.bot != b.bot) return false;
        if (!("channels" in a.def) || !("channels" in b.def)) return false;
        foreach (x in a.def.channels) foreach (y in b.def.channels) if (x == y) return true;
        return false;
    },
    Read = function(bot,key,kind) {
        if (kind == "float") return NetProps.GetPropFloat(bot,key);
        if (kind == "vector") return NetProps.GetPropVector(bot,key);
        return NetProps.GetPropInt(bot,key);
    },
    Write = function(bot,key,kind,value) {
        if (kind == "float") NetProps.SetPropFloat(bot,key,value);
        else if (kind == "vector") NetProps.SetPropVector(bot,key,value);
        else NetProps.SetPropInt(bot,key,value);
    },
    Equal = function(a,b,kind) {
        if (kind == "vector") return a.x == b.x && a.y == b.y && a.z == b.z;
        return a == b;
    },
    Override = function(ctx,key,kind,value,mask = null) {
        if (!Valid(ctx.bot)) return;
        if (kind != "int" && kind != "float" && kind != "vector" && kind != "flags") throw "Invalid override kind";
        if (kind == "flags" && mask == null) throw "Flags override requires a mask";
        if (!NetProps.HasProp(ctx.bot,key)) throw "Missing property: " + key;
        // Defense in depth: custom definitions must not bypass ownership by
        // forgetting a property channel. Reject before reading/writing state.
        foreach (id,loop in loops) foreach (a in loop.active) {
            if (a.ctx == ctx || a.bot != ctx.bot) continue;
            foreach (o in a.ctx.overrides) if (o.key == key) throw "Property already owned: " + key;
        }
        local current=Read(ctx.bot,key,kind), entry=null;
        foreach (o in ctx.overrides) if (o.key == key) { entry=o; break; }
        if (entry == null) {
            entry={key=key, kind=kind, original=current, owned=current, mask=mask};
            ctx.overrides.append(entry);
        } else if (entry.kind != kind || entry.mask != mask) throw "Override property kind/mask changed";
        local result=kind == "flags" ? ((current & ~mask) | (value & mask)) : value;
        Write(ctx.bot,key,kind,result); entry.owned=result;
    },
    Release = function(instance) {
        local ctx=instance.ctx;
        if (!Valid(ctx.bot)) return;
        try { Call(instance.def,"stopFn",ctx); } catch(e) { Report("Action stop failed: " + e); }
        for (local i=ctx.overrides.len()-1; i>=0; --i) {
            local o=ctx.overrides[i];
            try {
                local current=Read(ctx.bot,o.key,o.kind);
                if (o.kind == "flags") {
                    // Restore only bits we still own; preserve unrelated/external changes.
                    local unchanged=~(current ^ o.owned) & o.mask;
                    Write(ctx.bot,o.key,o.kind,(current & ~unchanged) | (o.original & unchanged));
                } else if (Equal(current,o.owned,o.kind)) Write(ctx.bot,o.key,o.kind,o.original);
            } catch(e) { Report("Override cleanup failed: " + e); }
        }
    },
    ReleaseLoop = function(loop) {
        local old=loop.active; loop.active=[];
        foreach (a in old) Release(a);
    },
    TickAll = function(now = null) {
        if (ticking) return;
        ticking=true; if (now == null) now=Time();
        local due=[], snapshot=[];
        foreach (id,loop in loops) snapshot.append(loop);
        foreach (loop in snapshot) if (!loop.stopped && now >= loop.next) due.append(loop);
        // Collect first. No action effects occur during selection/evaluation.
        foreach (loop in due) {
            loop.requests=[]; loop.next=now+loop.interval; collecting=loop;
            try {
                local selected=loop.selectFn(), seen={};
                foreach (bot in selected) if (IsBot(bot) && !(bot in seen)) {
                    seen[bot] <- true;
                    if (!loop.stopped) {
                        scope={bot=bot,loopid=loop.id,loop=loop};
                        loop.collectFn(scope);
                        scope=null;
                    }
                }
            } catch(e) { loop.requests=[]; loop.stopped=true; Report("Bot loop stopped: collection failed: " + e); }
            collecting=null; scope=null;
        }
        // Build a global desired set, including still-active non-due loops.
        local desired=[];
        foreach (loop in snapshot) {
            if (loop.stopped) continue;
            local isDue=due.find(loop) != null;
            foreach (r in (isDue ? loop.requests : loop.active)) desired.append({loop=loop,r=r,due=isDue});
        }
        for (local i=0;i<desired.len();++i) for(local j=i+1;j<desired.len();++j) {
            local a=desired[i],b=desired[j];
            if (Clashes(a.r,b.r)) {
                if (a.due) a.r.rejected=true;
                if (b.due) b.r.rejected=true;
                Report("Conflicting bot actions: " + a.r.def.id + " / " + b.r.def.id);
            }
        }
        // Release every removed action before any newly requested action starts.
        foreach (loop in snapshot) {
            local retained=[];
            foreach (a in loop.active) {
                local keep=false;
                if (!loop.stopped && Valid(a.bot)) {
                    if (due.find(loop) == null) keep=true;
                    else foreach (r in loop.requests) if (!r.rejected && Same(a,r)) { keep=true; break; }
                }
                if (keep) retained.append(a); else Release(a);
            }
            loop.active=retained;
        }
        foreach (loop in due) {
            if (loop.stopped) continue;
            foreach (r in loop.requests) {
                if (r.rejected || loop.stopped || !Valid(r.bot)) continue;
                local instance=null;
                foreach (a in loop.active) if (Same(a,r)) { instance=a; break; }
                local fresh=instance == null;
                if (fresh) {
                    local ctx={bot=r.bot,params=r.params,state={},overrides=[],loopid=loop.id,applyid=r.applyid};
                    instance={bot=r.bot,applyid=r.applyid,def=r.def,ctx=ctx};
                    loop.active.append(instance);
                } else instance.ctx.params=r.params;
                try { Call(r.def,fresh ? "startFn" : "updateFn",instance.ctx); }
                catch(e) {
                    loop.stopped=true;
                    Report("Action callback failed: " + e);
                    local index=loop.active.find(instance);
                    if (index != null) loop.active.remove(index);
                    Release(instance);
                }
            }
            if (!loop.stopped && loop.afterFn != null) {
                try { loop.afterFn(); } catch(e) { loop.stopped=true; Report("Bot loop stopped: After Update failed: " + e); }
            }
        }
        // Stops from lifecycle callbacks win over all other work this tick.
        local removed=[];
        foreach (loop in snapshot) if (loop.stopped) { ReleaseLoop(loop); removed.append(loop.id); }
        foreach (id in removed) delete loops[id];
        collecting=null; ticking=false;
    }
};
