// Exact-query, bounded snapshots. Freshness is supplied by each reader.
::HLQC <- {
    pools={},count=0,touch=0,lastTime=null,
    Finite=function(v){return (typeof v=="integer" || typeof v=="float") && v==v && v>=-1e30 && v<=1e30;},
    Empty=function(error="") {
        return {available=false,found=false,area=null,areas=[],count=0,position=Vector(0,0,0),id=-1,
            blocked=false,underwater=false,exists=false,brightness=0.0,color=Vector(0,0,0),fallback=false,
            cache_hit=false,refreshed=false,age=0.0,error=error};
    },
    Clear=function(){pools={};count=0;touch=0;lastTime=null;},
    Clock=function() {
        local now=Time();
        if(!Finite(now))throw "Query cache clock is not finite";
        if(lastTime!=null && now<lastTime) {
            foreach(id,pool in pools)pool.entries=[];
            count=0;touch=0;
        }
        lastTime=now;return now;
    },
    Pool=function(descriptor,create=true) {
        if(typeof descriptor!="table" || !("id" in descriptor) || typeof descriptor.id!="string" ||
            descriptor.id.len()==0 || descriptor.id.len()>128 || !("capacity" in descriptor) ||
            !Finite(descriptor.capacity) || descriptor.capacity<1 || descriptor.capacity>128 || descriptor.capacity!=floor(descriptor.capacity))
            throw "Invalid query cache pool descriptor";
        if(descriptor.id in pools) {
            if(pools[descriptor.id].capacity!=descriptor.capacity)throw "Query cache pool ID has conflicting capacities";
            return pools[descriptor.id];
        }
        if(!create)return null;
        if(pools.len()>=16)throw "At most 16 query cache pools";
        local pool={capacity=descriptor.capacity,entries=[]};pools[descriptor.id] <- pool;
        return pool;
    },
    Same=function(a,b) {
        if(a.len()!=b.len())return false;
        for(local i=0;i<a.len();++i)if(a[i]!=b[i])return false;
        return true;
    },
    Find=function(pool,key) {
        foreach(i,e in pool.entries)if(Same(e.key,key))return i;
        return null;
    },
    Drop=function(pool,index){pool.entries.remove(index);--count;},
    Oldest=function(pool) {
        local index=0;
        for(local i=1;i<pool.entries.len();++i)if(pool.entries[i].used<pool.entries[index].used)index=i;
        return index;
    },
    Put=function(pool,key,result,now) {
        if(pool.entries.len()>=pool.capacity)Drop(pool,Oldest(pool));
        if(count>=512) {
            local chosen=null,index=0,age=null;
            foreach(id,p in pools)foreach(i,e in p.entries)if(age==null || e.used<age){chosen=p;index=i;age=e.used;}
            if(chosen!=null)Drop(chosen,index);
        }
        pool.entries.append({key=key,value=result,created=now,used=++touch});++count;
    },
    Copy=function(source,kind) {
        // Explicit schema keeps nav handles as identities, never copied tables.
        local r=Empty();r.available=source.available;
        if(kind=="nearby") {
            r.found=source.found;r.area=source.area;r.count=source.count;
            foreach(area in source.areas)r.areas.append(area);
            r.position=Vector(source.position.x,source.position.y,source.position.z);
            foreach(k in ["id","blocked","underwater","exists"])if(k in source)r[k]=source[k];
        } else {
            r.color=Vector(source.color.x,source.color.y,source.color.z);
            r.brightness=source.brightness;r.fallback=source.fallback;
        }
        return r;
    },
    Validate=function(result,kind) {
        if(typeof result!="table" || !("available" in result) || typeof result.available!="bool")return false;
        if(!result.available)return false;
        if(kind=="nearby") {
            if(!("areas" in result) || typeof result.areas!="array" || result.areas.len()>128 ||
                !("count" in result) || result.count!=result.areas.len() || !("found" in result) || typeof result.found!="bool" ||
                !("area" in result) || !("position" in result))return false;
            if(!Finite(result.position.x)||!Finite(result.position.y)||!Finite(result.position.z))return false;
            if(!HLWQ.Ready())return false;
            foreach(area in result.areas)if(HLWQ.Area(area)==null)return false;
            if(result.found && HLWQ.Area(result.area)==null)return false;
        } else {
            if(!("color" in result)||!("brightness" in result)||!("fallback" in result)||typeof result.fallback!="bool")return false;
            if(!Finite(result.color.x)||!Finite(result.color.y)||!Finite(result.color.z)||!Finite(result.brightness))return false;
        }
        return true;
    },
    Read=function(descriptor,position,radius,limit,ttl,refresh,kind) {
        local r=Empty(),pool=null,key=null,now=0.0,index=null;
        try {
            now=Clock();
            if(!Finite(position.x)||!Finite(position.y)||!Finite(position.z))throw "Query position must be finite";
            if(!Finite(ttl)||ttl<0||ttl>60)throw "Maximum cache age must be between 0 and 60 seconds";
            key=[kind,position.x,position.y,position.z];
            if(kind=="nearby") {
                if(!Finite(radius)||!Finite(limit))throw "Radius and result limit must be finite";
                // Match the existing query's clamping, then key exact effective numbers.
                radius=radius<1?1:(radius>4096?4096:radius);
                limit=limit<1?1:(limit>128?128:limit.tointeger());
                key.append(radius);key.append(limit);
            }
            pool=Pool(descriptor);index=Find(pool,key);
            if(index!=null) {
                local entry=pool.entries[index],age=now-entry.created,valid=false;
                if(!refresh && ttl>0 && age<ttl) {
                    try {valid=Validate(entry.value,kind);}catch(e){valid=false;}
                }
                if(valid) {
                    entry.used=++touch;r=Copy(entry.value,kind);r.cache_hit=true;r.age=age;return r;
                }
                // Invalidate before refresh; failed queries cannot resurrect old data.
                Drop(pool,index);
            }
            r.refreshed=true;
            local queried=kind=="nearby"?HLWQ.Nearby(Vector(position.x,position.y,position.z),radius,limit):HLWQ_Ambient(Vector(position.x,position.y,position.z));
            if(!Validate(queried,kind)) {
                r.error=kind=="nearby"?"Navigation query unavailable or invalid":"Baked ambient query unavailable or invalid";
                return r;
            }
            r=Copy(queried,kind);r.refreshed=true;
            if(ttl>0)Put(pool,key,Copy(queried,kind),now);
            return r;
        } catch(e) {
            local attempted=r.refreshed,message=e.tostring();
            r=Empty(message.len()>256?message.slice(0,256):message);r.refreshed=attempted;
            return r;
        }
    },
    ReadNearby=function(pool,position,radius,limit,age,refresh){return Read(pool,position,radius,limit,age,refresh,"nearby");},
    ReadAmbient=function(pool,position,age,refresh){return Read(pool,position,0,0,age,refresh,"ambient");},
    Invalidate=function(descriptor) {
        try {
            Clock();local pool=Pool(descriptor,false);
            if(pool==null)return {removed=0,error=""};
            local removed=pool.entries.len();pool.entries=[];count-=removed;
            return {removed=removed,error=""};
        } catch(e) {local message=e.tostring();return {removed=0,error=message.len()>256?message.slice(0,256):message};}
    }
};
