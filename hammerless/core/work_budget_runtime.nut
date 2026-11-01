// Opt-in entry credits, scoped to one bot-action scheduler cycle, not a frame.
::HLWB <- {
    pools={},claims={},cycle=0,nextId=1,active=false,
    Number=function(v){return (typeof v=="integer" || typeof v=="float") && v==v && v>=0 && v<=1e30;},
    Empty=function(error="") {return {grant=null,granted=0,remaining=0,waiting=false,queued=false,error=error,cycle=cycle};},
    Remove=function(id) {
        if(!(id in claims))return;
        local c=claims[id];
        if(c.pool in pools) {
            local pool=pools[c.pool],index=pool.order.find(id);
            if(index!=null) {
                pool.order.remove(index);
                if(pool.next==id)pool.next=pool.order.len()>0?pool.order[index%pool.order.len()]:-1;
            }
        }
        if("_workBudgetClaims" in c.ctx && c.key in c.ctx._workBudgetClaims && c.ctx._workBudgetClaims[c.key]==id)
            delete c.ctx._workBudgetClaims[c.key];
        delete claims[id];
    },
    Cleanup=function(ctx) {
        if(!("_workBudgetClaims" in ctx))return;
        local ids=[];foreach(key,id in ctx._workBudgetClaims)ids.append(id);
        foreach(id in ids)Remove(id);
        delete ctx._workBudgetClaims;
    },
    Alive=function(ctx) {
        return !("_releasing" in ctx && ctx._releasing) && "status" in ctx && ctx.status=="RUNNING" && HLBA.Valid(ctx.bot);
    },
    Snapshot=function(c) {
        return {grant=c.receipt,granted=c.granted,remaining=c.remaining,waiting=c.remaining==0,
                queued=true,error="",cycle=cycle};
    },
    Begin=function(due,now) {
        ++cycle;active=true;
        local removed=[];
        foreach(id,c in claims) {
            if(!Alive(c.ctx) || c.cancelled){removed.append(id);continue;}
            c.eligible=false;
            foreach(loop in due)if(loop.id==c.ctx.loopid && !loop.stopped){c.eligible=true;break;}
            c.granted=0;c.remaining=0;c.receipt={id=id,cycle=cycle};
        }
        foreach(id in removed)Remove(id);
        foreach(name,pool in pools) {
            local order=[];foreach(id in pool.order)if(id in claims)order.append(id);
            pool.order=order;
            if(order.len()==0)continue;
            local start=order.find(pool.next),eligible=[],positions={};
            foreach(i,id in order)positions[id] <- i;
            if(start==null)start=0;
            for(local i=0;i<order.len();++i) {
                local id=order[(start+i)%order.len()];
                if(claims[id].eligible)eligible.append(id);
            }
            local credit=pool.limit,index=0,pending=eligible.len();
            while(credit>0 && pending>0) {
                local c=claims[eligible[index]];
                if(c.granted<c.wanted) {
                    ++c.granted;++c.remaining;--credit;
                    if(c.granted==c.wanted)--pending;
                    local original=positions[c.id];
                    pool.next=order[(original+1)%order.len()];
                }
                index=(index+1)%eligible.len();
            }
        }
    },
    End=function() {
        local removed=[];
        foreach(id,c in claims) {
            if(c.cancelled || (c.eligible && c.renewed!=cycle))removed.append(id);
            c.remaining=0;c.receipt=null;
        }
        foreach(id in removed)Remove(id);
        active=false;
    },
    Request=function(ctx,key,pool,wanted,enabled) {
        if(!active)return Empty("Work budget requests require an active scheduler cycle");
        if(!Alive(ctx))return Empty("Work budget requests require a running action outside cleanup");
        local c=null;
        if("_workBudgetClaims" in ctx && key in ctx._workBudgetClaims) {
            local id=ctx._workBudgetClaims[key];if(id in claims)c=claims[id];
        }
        // First request per cycle wins; cancellation is latched too.
        if(c!=null && c.renewed==cycle)return c.cancelled?Empty():Snapshot(c);
        if(!enabled || (Number(wanted) && wanted<1)) {
            if(c==null) {
                if(claims.len()>=512)return Empty("At most 512 queued work budget requests");
                local id=nextId++;
                c={id=id,ctx=ctx,key=key,pool="",wanted=0,granted=0,remaining=0,
                   eligible=true,renewed=cycle,cancelled=true,receipt=null};
                claims[id] <- c;
                if(!("_workBudgetClaims" in ctx))ctx._workBudgetClaims <- {};
                ctx._workBudgetClaims[key] <- id;
            }
            c.cancelled=true;c.wanted=0;c.granted=0;c.remaining=0;c.receipt=null;c.renewed=cycle;
            return Empty();
        }
        if(!Number(wanted))return Empty("Requested entries must be finite and nonnegative");
        wanted=wanted>256?256:wanted.tointeger();
        if(typeof pool!="table" || !("id" in pool) || typeof pool.id!="string" || pool.id.len()==0 || pool.id.len()>128 ||
           !("limit" in pool) || !Number(pool.limit) || pool.limit<1 || pool.limit>4096 || pool.limit!=floor(pool.limit))
            return Empty("Invalid shared work budget descriptor");
        if(pool.id in pools && pools[pool.id].limit!=pool.limit)return Empty("Shared work budget ID has conflicting limits");
        if(!(pool.id in pools)) {
            if(pools.len()>=32)return Empty("At most 32 shared work budget pools");
            pools[pool.id] <- {limit=pool.limit,order=[],next=-1};
        }
        if(c!=null && c.pool!=pool.id){Remove(c.id);c=null;}
        if(c==null) {
            if(claims.len()>=512)return Empty("At most 512 queued work budget requests");
            local id=nextId++;
            c={id=id,ctx=ctx,key=key,pool=pool.id,wanted=wanted,granted=0,remaining=0,
               eligible=true,renewed=cycle,cancelled=false,receipt={id=id,cycle=cycle}};
            claims[id] <- c;pools[pool.id].order.append(id);
            if(!("_workBudgetClaims" in ctx))ctx._workBudgetClaims <- {};
            ctx._workBudgetClaims[key] <- id;
        }
        c.wanted=wanted;c.renewed=cycle;c.cancelled=false;
        if(c.receipt==null)c.receipt={id=c.id,cycle=cycle};
        return Snapshot(c);
    },
    Inspect=function(ctx,receipt) {
        local bad={valid=false,remaining=0,granted=0,cycle=cycle,error="Invalid, expired or foreign work grant"};
        if(!active || typeof receipt!="table" || !("id" in receipt) || typeof receipt.id!="integer" ||
           !("cycle" in receipt) || receipt.cycle!=cycle || !(receipt.id in claims))return bad;
        local c=claims[receipt.id];
        if(c.ctx!=ctx || c.receipt!=receipt || c.cancelled || !Alive(ctx))return bad;
        return {valid=true,remaining=c.remaining,granted=c.granted,cycle=cycle,error=""};
    },
    Take=function(ctx,receipt,wanted) {
        local state=Inspect(ctx,receipt);
        if(!state.valid)return {valid=false,taken=0,remaining=0,error=state.error};
        if(!Number(wanted))return {valid=false,taken=0,remaining=state.remaining,error="Invalid grant consumption"};
        wanted=wanted>256?256:wanted.tointeger();
        local taken=wanted<state.remaining?wanted:state.remaining;
        claims[receipt.id].remaining-=taken;
        return {valid=true,taken=taken,remaining=state.remaining-taken,error=""};
    }
};
