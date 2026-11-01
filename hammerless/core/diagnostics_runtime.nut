// No retained observed values or entity handles; only bounded timestamp maps.
::HLDBG <- {
    nodes={}, window=-1.0, printed=0,
    Disabled=function(){return {logged=false,suppressed=false,reason="disabled"};},
    Passed=function(){return {logged=false,suppressed=false,reason="condition passed",passed=true};},
    Text=function(value) {
        local s="<unprintable>";
        try {s=value==null ? "null" : value.tostring();} catch(e) {}
        if(s.len()>256) s=s.slice(0,256)+"...";
        local clean="";
        for(local i=0;i<s.len();++i) {
            local c=s.slice(i,i+1);
            clean += (s[i]<32 || s[i]==127) ? " " : c;
        }
        return clean;
    },
    Watch=function(ctx,key,label,value,interval) {
        local denied=function(reason){return {logged=false,suppressed=true,reason=reason};};
        if((typeof interval!="float" && typeof interval!="integer") || interval!=interval || interval<0 || interval>1e30)
            return denied("invalid interval");
        interval=interval<0.1 ? 0.1 : (interval>300 ? 300 : interval);
        local now=Time(), store=nodes, shared=ctx==null || "loop" in ctx;
        if(ctx!=null && "loop" in ctx) {
            // Collection contexts are transient: persist numeric identity only.
            local index=-1;
            try {index=ctx.bot.GetEntityIndex();} catch(e) {return denied("invalid bot");}
            local id=ctx.loopid.tostring();
            key="loop:"+id.len()+":"+id+":"+index+":"+key;
        } else if(ctx==null) key="graph:"+key;
        if(!shared) {
            if(!("_diagnostics" in ctx)) ctx._diagnostics <- {};
            store=ctx._diagnostics;
        }
        if(shared) {
            local expired=[];
            foreach(k,t in store) if(now<t || now-t>=301) expired.append(k);
            foreach(k in expired) delete store[k];
        }
        if(key in store && now>=store[key] && now-store[key]<interval) return denied("node interval");
        if(!(key in store) && store.len()>=(shared?256:128)) return denied("state capacity");
        if(now<window || now-window>=1) {window=now;printed=0;}
        if(printed>=20) return denied("global rate limit");
        local who="";
        if(ctx!=null && "bot" in ctx) {
            try {who=" bot="+ctx.bot.GetEntityIndex();} catch(e) {who=" bot=<invalid>";}
        }
        printl("[Hammerless Debug"+who+"] "+Text(label)+": "+Text(value));
        store[key] <- now; printed++;
        return {logged=true,suppressed=false,reason="logged"};
    },
    Check=function(ctx,key,condition,enabled,label,interval) {
        local result=enabled && !condition ? Watch(ctx,key,label,"condition failed",interval) : Disabled();
        if(condition) result.reason="condition passed";
        result.passed <- condition;
        return result;
    },
    Inspect=function(ctx) {
        local status=("status" in ctx ? ctx.status : "IDLE");
        return {status=status,running=status=="RUNNING",success=status=="SUCCESS",failed=status=="FAILED",
                stopping=("_releasing" in ctx && ctx._releasing),updates=("_stepSerial" in ctx ? ctx._stepSerial : 0)};
    }
};
