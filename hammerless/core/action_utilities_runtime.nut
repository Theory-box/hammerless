::HLBAU <- {};
HLBAU.UtilityStore <- function(ctx) {
    if (!("_utilities" in ctx)) ctx._utilities <- {};
    return ctx._utilities;
};
HLBAU.UtilityNumber <- function(value) {
    local t=typeof value;
    if ((t!="float" && t!="integer") || value!=value || value<0 || value>1000000000)
        throw "Action timing and distance inputs must be finite, nonnegative numbers";
    return value;
};
HLBAU.Cooldown <- function(ctx,key,seconds,reset) {
    UtilityNumber(seconds);
    local now=UtilityNumber(Time()), store=UtilityStore(ctx);
    if (reset || !(key in store)) store[key] <- {until=now};
    local s=store[key], remaining=s.until-now;
    if (remaining<=0) { s.until=now+seconds; return {passed=true,blocked=false,remaining=seconds}; }
    return {passed=false,blocked=true,remaining=remaining};
};
HLBAU.Changed <- function(ctx,key,value,kind,initial,reset) {
    local store=UtilityStore(ctx), first=reset || !(key in store), different=initial;
    if (!first) {
        local old=store[key];
        different=kind=="vec" ? (old.x!=value.x || old.y!=value.y || old.z!=value.z) : old!=value;
    }
    // Copy vectors: engine/script callers can mutate the same vector object.
    store[key] <- kind=="vec" ? Vector(value.x,value.y,value.z) : value;
    return {changed=different,unchanged=!different};
};
HLBAU.Movement <- function(ctx,key,goal,radius,min_progress,stall_seconds,timeout,reset) {
    UtilityNumber(radius); UtilityNumber(min_progress); UtilityNumber(stall_seconds); UtilityNumber(timeout);
    local now=UtilityNumber(Time()), distance=(ctx.bot.GetOrigin()-goal).Length();
    UtilityNumber(distance);
    local store=UtilityStore(ctx);
    if (reset || !(key in store) || (store[key].goal-goal).LengthSqr()>0.0001)
        store[key] <- {goal=Vector(goal.x,goal.y,goal.z),start=now,last=now,best=distance};
    local s=store[key], progress=s.best-distance;
    if (progress>0 && progress>=min_progress) { s.best=distance; s.last=now; }
    local arrived=distance<=radius;
    return {distance=distance,arrived=arrived,progress=progress>0 ? progress : 0,
        stalled=!arrived && stall_seconds>0 && now-s.last>=stall_seconds,
        timed_out=!arrived && timeout>0 && now-s.start>=timeout,elapsed=now-s.start};
};

