// Results are fresh local snapshots; rules never share mutable per-bot state.
::HLCE <- {};
HLCE.Empty <- function() {
    return {found=false, count=0, examined=0, rejected=0, errors=0, truncated=false,
            item=null, items=[], score=0.0, index=-1, rank=-1, position=Vector(0,0,0), rows=[], kind="", error=""};
};
HLCE.Examine <- function(r,items,kind,rule,params,i) {
        ++r.examined;
        local p=HLLT.Position(items[i],kind);
        if (p==null) { ++r.rejected; return; }
        local v=null;
        try { v=rule(items[i],p,i,params); }
        catch(error) {
            ++r.errors;
            if(r.error=="") { local message=error.tostring(); r.error=message.len()>256 ? message.slice(0,256) : message; }
            return;
        }
        if (typeof v!="table" || !("keep" in v) || typeof v.keep!="bool" ||
            !("score" in v) || (typeof v.score!="integer" && typeof v.score!="float") ||
            v.score!=v.score || v.score>1.0e30 || v.score< -1.0e30) {
            ++r.errors;
            if(r.error=="") r.error="Rule must return boolean Keep and finite numeric Score between -1e30 and 1e30";
            return;
        }
        if (!v.keep) { ++r.rejected; return; }
        r.rows.append({item=items[i],position=Vector(p.x,p.y,p.z),index=i,score=v.score});
};
HLCE.Finish <- function(r,limit) {
    r.rows.sort(function(a,b) { return a.score>b.score ? -1 : (a.score<b.score ? 1 : (a.index<b.index ? -1 : (a.index>b.index ? 1 : 0))); });
    if (r.rows.len()>limit) { r.truncated=true; r.rows.resize(limit); }
    foreach(row in r.rows) r.items.append(row.item);
    r.count=r.rows.len(); r.found=r.count>0;
    if(r.found) { local v=r.rows[0]; r.item=v.item; r.score=v.score; r.position=v.position; r.index=v.index; }
    return r;
};
HLCE.Evaluate <- function(items, kind, rule, params, limit) {
    local r=Empty(); r.kind=kind;
    if (typeof items != "array") return r;
    local n=items.len()<256 ? items.len() : 256;
    r.truncated=items.len()>n;
    limit=HLLT.Number(limit,0,128).tointeger();
    for (local i=0;i<n;++i) {
        ++r.examined;
        local p=HLLT.Position(items[i],kind);
        if (p==null) { ++r.rejected; continue; }
        local v=null;
        try { v=rule(items[i],p,i,params); }
        catch(error) {
            ++r.errors;
            if(r.error=="") { local message=error.tostring(); r.error=message.len()>256 ? message.slice(0,256) : message; }
            continue;
        }
        if (typeof v!="table" || !("keep" in v) || typeof v.keep!="bool" ||
            !("score" in v) || (typeof v.score!="integer" && typeof v.score!="float") ||
            v.score!=v.score || v.score>1.0e30 || v.score< -1.0e30) {
            ++r.errors;
            if(r.error=="") r.error="Rule must return boolean Keep and finite numeric Score between -1e30 and 1e30";
            continue;
        }
        if (!v.keep) { ++r.rejected; continue; }
        r.rows.append({item=items[i],position=p,index=i,score=v.score});
    }
    r.rows.sort(function(a,b) { return a.score>b.score ? -1 : (a.score<b.score ? 1 : (a.index<b.index ? -1 : (a.index>b.index ? 1 : 0))); });
    if (r.rows.len()>limit) { r.truncated=true; r.rows.resize(limit); }
    foreach(row in r.rows) r.items.append(row.item);
    r.count=r.rows.len(); r.found=r.count>0;
    if(r.found) { local v=r.rows[0]; r.item=v.item; r.score=v.score; r.position=v.position; r.index=v.index; }
    return r;
};
HLCE.Item <- function(result,index,kind) {
    local r=Empty();
    if(typeof result!="table" || !("kind" in result) || result.kind!=kind || !("rows" in result) ||
       (typeof index!="integer" && typeof index!="float") || index!=index || index<0 || index>=result.rows.len() || index!=floor(index)) return r;
    local v=result.rows[index.tointeger()];
    if(HLLT.Position(v.item,kind)==null) return r;
    r.found=true; r.item=v.item; r.score=v.score; r.position=v.position; r.index=v.index;
    return r;
};
// Absence means absent from this snapshot, not necessarily invalid in the world.
HLCE.Find <- function(result,item,kind) {
    local r=Empty();
    if(typeof result!="table" || !("kind" in result) || result.kind!=kind ||
       !("rows" in result) || typeof result.rows!="array") return r;
    r.truncated=("truncated" in result) && result.truncated;
    if(HLLT.Position(item,kind)==null) return r;
    local count=result.rows.len()<128 ? result.rows.len() : 128;
    for(local i=0;i<count;++i) {
        local row=result.rows[i];
        if(row.item!=item) continue;
        r.found=true; r.item=item; r.score=row.score; r.index=row.index;
        r.position=row.position; r.rank=i; return r;
    }
    return r;
};

HLCE.Job <- function(serial,now,status) {
    return {serial=serial,start=now,status=status,inputs=[],params=null,rule=null,
            kind="",limit=0,total=0,processed=0,result=Empty(),waiting=false,pendingCapture=true};
};
HLCE.JobError <- function(job,message) {
    job.status="ERROR";job.inputs=[];job.params=null;job.rule=null;job.waiting=false;job.pendingCapture=false;
    job.result=Empty();job.result.errors=1;job.result.error=message;
};
HLCE.CopyResults <- function(source) {
    local r=Empty();
    foreach(k,v in source) if(k!="rows" && k!="items" && k!="position") r[k]=v;
    r.position=Vector(source.position.x,source.position.y,source.position.z);
    foreach(row in source.rows) r.rows.append({item=row.item,index=row.index,score=row.score,
        position=Vector(row.position.x,row.position.y,row.position.z)});
    foreach(item in source.items)r.items.append(item);
    return r;
};
HLCE.JobSnapshot <- function(job,now) {
    local complete=job.status=="COMPLETE", r=complete?CopyResults(job.result):Empty();
    if(!complete) {
        r.examined=job.result.examined;r.rejected=job.result.rejected;
        r.errors=job.result.errors;r.error=job.result.error;r.truncated=job.result.truncated;
    }
    // Separate copies: modifying public Ranked Results cannot mutate public
    // convenience outputs either, and neither snapshot aliases private state.
    r.results <- complete?CopyResults(job.result):null;
    r.status <- job.status;r.busy <- job.status=="RUNNING";r.complete <- complete;
    r.cancelled <- job.status=="CANCELLED";r.failed <- job.status=="ERROR";
    r.waiting <- job.waiting;
    r.processed <- job.processed;r.total <- job.total;
    r.progress <- job.total>0?job.processed.tofloat()/job.total:(complete?1.0:0.0);
    r.age <- now>=job.start?now-job.start:0.0;
    return r;
};
HLCE.Step <- function(ctx,key,capture,budget,reset,enabled,grant=null,useGrant=false) {
    local now=Time(),serial=ctx._stepSerial;
    if("_releasing" in ctx && ctx._releasing) {
        local denied=Job(serial,now,"ERROR");JobError(denied,"Cannot step candidates during action cleanup");
        return JobSnapshot(denied,now);
    }
    if(!("_candidateJobs" in ctx))ctx._candidateJobs <- {};
    local store=ctx._candidateJobs,job=(key in store)?store[key]:null;
    // First invocation per action update wins, including reset/cancel fanout.
    if(job!=null && job.serial==serial)return JobSnapshot(job,now);
    if(job==null && store.len()>=32) {
        local denied=Job(serial,now,"ERROR");JobError(denied,"At most 32 stepped candidate nodes per action instance");
        return JobSnapshot(denied,now);
    }
    if(!enabled) {
        job=Job(serial,now,"CANCELLED");store[key] <- job;
        return JobSnapshot(job,now);
    }
    if(job!=null && !reset && job.status!="RUNNING" && job.status!="CANCELLED") {
        job.serial=serial;return JobSnapshot(job,now);
    }
    if((typeof budget!="integer" && typeof budget!="float") || budget!=budget || budget<0 || budget>1e30) {
        if(job==null || reset)job=Job(serial,now,"ERROR");
        job.serial=serial;store[key] <- job;
        JobError(job,"Budget must be a finite nonnegative number (clamped to 1–256)");
        return JobSnapshot(job,now);
    }
    if(job==null || reset || job.status=="CANCELLED") {
        job=Job(serial,now,"RUNNING");store[key] <- job;
    }
    job.serial=serial;job.waiting=false;
    if(useGrant) {
        if(!("HLWB" in getroottable())) {
            JobError(job,"Shared work grant runtime is unavailable");return JobSnapshot(job,now);
        }
        local credit=HLWB.Inspect(ctx,grant);
        if(!credit.valid){JobError(job,credit.error);return JobSnapshot(job,now);}
        if(credit.remaining==0){job.waiting=true;return JobSnapshot(job,now);}
    }
    if(job.pendingCapture) {
        try {
            local source=capture();
            if(typeof source.items!="array")throw "Items must be a Bot/Nav Area list";
            if(source.kind!="bots" && source.kind!="navareas")throw "Candidate type must be Bots or Nav Areas";
            if(typeof source.rule!="function")throw "Missing candidate rule";
            job.kind=source.kind;job.rule=source.rule;job.params=HLBA.Snapshot(source.params);
            job.limit=HLLT.Number(source.limit,0,128).tointeger();
            job.total=source.items.len()<256?source.items.len():256;
            for(local i=0;i<job.total;++i)job.inputs.append(source.items[i]);
            job.result.kind=job.kind;job.result.truncated=source.items.len()>256;
            job.pendingCapture=false;
        } catch(e) {
            local message=e.tostring();JobError(job,message.len()>256?message.slice(0,256):message);
            return JobSnapshot(job,now);
        }
    }
    if(job.status!="RUNNING")return JobSnapshot(job,now);
    budget=budget<1?1:(budget>256?256:budget.tointeger());
    if(useGrant) {
        local wanted=job.total-job.processed;if(wanted>budget)wanted=budget;
        local credit=HLWB.Take(ctx,grant,wanted);
        if(!credit.valid){JobError(job,credit.error);return JobSnapshot(job,now);}
        budget=credit.taken;
    }
    local end=job.processed+budget;if(end>job.total)end=job.total;
    while(job.processed<end) {
        Examine(job.result,job.inputs,job.kind,job.rule,job.params,job.processed);
        ++job.processed;
    }
    if(job.processed==job.total) {
        Finish(job.result,job.limit);job.status="COMPLETE";
        job.inputs=[];job.params=null;job.rule=null;
    }
    return JobSnapshot(job,now);
};
