// Per generated map script: latest signal per channel, not an event queue.
::HLCO <- {
    signals={}, claims={}, revision=0,
    Finite=function(v) {return (typeof v=="integer" || typeof v=="float") && v==v && v>-1e30 && v<1e30;},
    Key=function(k) {return typeof k=="string" && k.len()>0 && k.len()<=128;},
    TTL=function(t) {return Finite(t) && t>0;},
    Prune=function(entries) {
        local expired=[]; local now=Time();
        foreach(k,v in entries) if(v.until<=now) expired.append(k);
        foreach(k in expired) delete entries[k];
    },
    Publish=function(key,pos,ttl) {
        if(!Key(key) || !TTL(ttl)) return {success=false};
        try {if(!Finite(pos.x)||!Finite(pos.y)||!Finite(pos.z)) return {success=false};} catch(e) {return {success=false};}
        Prune(signals);
        if(!(key in signals) && signals.len()>=128) return {success=false};
        local now=Time(); revision++;
        signals[key] <- {position=Vector(pos.x,pos.y,pos.z),created=now,until=now+(ttl>300?300:ttl),revision=revision};
        return {success=true};
    },
    Read=function(key) {
        Prune(signals);
        if(!Key(key)||!(key in signals)) return {available=false,position=Vector(0,0,0),age=0.0,remaining=0.0,revision=0};
        local s=signals[key];
        return {available=true,position=Vector(s.position.x,s.position.y,s.position.z),age=Time()-s.created,remaining=s.until-Time(),revision=s.revision};
    },
    Clear=function(key) {
        Prune(signals); if(!Key(key)||!(key in signals)) return {success=false};
        delete signals[key]; return {success=true};
    },
    Claim=function(owner,key,ttl) {
        Prune(claims);
        if(owner==null||!Key(key)||!TTL(ttl)) return {acquired=false,remaining=0.0};
        if(key in claims && claims[key].owner!=owner) return {acquired=false,remaining=claims[key].until-Time()};
        if(!(key in claims)&&claims.len()>=128) return {acquired=false,remaining=0.0};
        local duration=ttl>300?300:ttl;
        claims[key] <- {owner=owner,until=Time()+duration};
        return {acquired=true,remaining=duration};
    },
    Release=function(owner,key) {
        Prune(claims);
        if(!Key(key)||!(key in claims)||claims[key].owner!=owner) return {released=false};
        delete claims[key]; return {released=true};
    },
    Cleanup=function(owner) {
        local keys=[];
        foreach(k,v in claims) if(v.owner==owner) keys.append(k);
        foreach(k in keys) delete claims[k];
    }
};
