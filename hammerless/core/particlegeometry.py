"""CPU geometry shared by viewport drawing and headless preview checks."""
import numpy as np

RENDERERS={'render_animated_sprites','render_sprite_trail','render_rope'}
CORNERS=np.array([[-1,-1],[1,-1],[1,1],[-1,-1],[1,1],[-1,1]])


def trail_quad(head,tail,radius,eye,right,up):
    direction=head-tail
    length=np.linalg.norm(direction)
    if length<1e-9 or radius<=0:return None
    direction=direction/length
    side=np.cross(direction,eye-(head+tail)*.5)
    if np.linalg.norm(side)<1e-9:side=np.cross(direction,up)
    if np.linalg.norm(side)<1e-9:side=np.cross(direction,right)
    side=side/max(np.linalg.norm(side),1e-9)*radius
    return np.array([tail-side,tail+side,head+side,tail-side,head+side,head-side])


def sprite_basis(sim,renderer,transform,center,right,up,eye,gaps):
    mode=int(renderer.get('orientation_type',0))
    if mode==0:return right,up
    if mode not in (1,2):
        gaps.add(f'unsupported sprite orientation {mode}');return right,up
    from .particlecontrol import rigid
    index=int(renderer.get('orientation_control_point',0))
    cp=sim.control_points.get(index,gaps)
    rotation=rigid(transform)[:3,:3]@(cp[:3,:3] if cp is not None else np.eye(3))
    if mode==2:return rotation[:,0],rotation[:,1]
    vertical=rotation[:,2]
    side=np.cross(vertical,eye-center)
    if np.linalg.norm(side)<1e-9:side=rotation[:,0]
    return side/max(np.linalg.norm(side),1e-9),vertical


def sprite_geometry(sim,renderer,sequences,transform,centers,right,up,eye,scale,material):
    """Batch cards and atlas coordinates; no Python loop over individual particles."""
    from .particlecontrol import rigid
    gaps=set();dual=material.get('dual_mode',-1)>=0
    order=np.argsort(-np.sum((centers-eye)**2,axis=1),kind='stable')
    valid=np.ones(len(order),dtype=bool)
    for values,secondary in ((sim.sequence,False),(sim.sequence2,True)):
        if secondary and not dual:continue
        if sequences or secondary:
            for ident in np.unique(values[order]):
                if int(ident) not in sequences:
                    gaps.add(f'missing {"secondary " if secondary else ""}sheet sequence {ident}')
                    valid &= values[order]!=ident
    ids=order[valid];n=len(ids)
    def sheet_uv(values,rate):
        rect=np.tile([0.,0.,1.,1.],(n,1));following=rect.copy();weight=np.zeros(n)
        for ident in np.unique(values[ids]):
            sheet=sequences.get(int(ident))
            if sheet is None:continue
            mask=values[ids]==ident;age=sim.age[ids][mask];life=sim.life[ids][mask]
            durations=sheet.durations;count=len(durations);cumulative=np.cumsum(durations);total=cumulative[-1]
            fit=bool(renderer.get('animation_fit_lifetime',False))
            if renderer.get('use animation rate as FPS',False) and not fit:
                t=np.maximum(0,age*rate);base=np.floor(t).astype(int)
                current=base%count;w=t-base
                terminal=(base>=count-1) if sheet.clamp else np.zeros(len(base),dtype=bool)
                current=np.where(terminal,count-1,current)
            else:
                t=np.clip(age/np.maximum(life,1e-9),0,np.nextafter(1.,0))*total if fit else np.maximum(0,age*rate)
                if not fit:t=np.minimum(t,np.nextafter(total,0)) if sheet.clamp else t%total
                current=np.minimum(np.searchsorted(cumulative,t,side='right'),count-1)
                start=np.where(current==0,0,cumulative[np.maximum(0,current-1)])
                w=np.clip((t-start)/durations[current],0,1)
                terminal=(current==count-1) if (sheet.clamp or fit) else np.zeros(len(current),dtype=bool)
            nxt=np.where(terminal,current,(current+1)%count);w=np.where(terminal,0,w)
            rect[mask]=sheet.rectangles[current,0];following[mask]=sheet.rectangles[nxt,0];weight[mask]=w
        return rect,following,weight
    rate=float(renderer.get('animation rate',1))
    a,an,w=sheet_uv(sim.sequence,rate)
    b,bn,w2=sheet_uv(sim.sequence2,float(renderer.get('second sequence animation rate',renderer.get('animation rate 2',rate)))) if dual else (a,a,np.zeros(n))
    mode=int(renderer.get('orientation_type',0));side=np.broadcast_to(right,(n,3));vertical=np.broadcast_to(up,(n,3))
    if mode in (1,2):
        cp=sim.control_points.get(int(renderer.get('orientation_control_point',0)),gaps)
        rotation=rigid(transform)[:3,:3]@(cp[:3,:3] if cp is not None else np.eye(3))
        if mode==2:side=np.broadcast_to(rotation[:,0],(n,3));vertical=np.broadcast_to(rotation[:,1],(n,3))
        else:
            vertical=np.broadcast_to(rotation[:,2],(n,3));side=np.cross(vertical,eye-centers[ids]);length=np.linalg.norm(side,axis=1)
            side=np.where((length<1e-9)[:,None],rotation[:,0],side/np.maximum(length[:,None],1e-9))
    elif mode!=0:gaps.add(f'unsupported sprite orientation {mode}')
    yaw=sim.yaw[ids,None];side=side*np.cos(yaw)+np.cross(side,vertical)*np.sin(yaw)
    corners=CORNERS*np.asarray(material.get('crop',(1.,1.)))
    co=np.cos(sim.rotation[ids,None]);si=np.sin(sim.rotation[ids,None]);x=corners[:,0];y=corners[:,1]
    radius=np.maximum(0,sim.radius[ids,None,None])/scale
    positions=centers[ids,None,:]+((x*co-y*si)[:,:,None]*side[:,None,:]+(x*si+y*co)[:,:,None]*vertical[:,None,:])*radius
    def uv(bounds,zoom):
        coords=(corners[None,:,:]/zoom[:,None,None]+1)/2
        u0=np.where(sim.flip[ids],bounds[:,2],bounds[:,0]);u1=np.where(sim.flip[ids],bounds[:,0],bounds[:,2])
        return np.stack((u0[:,None]+(u1-u0)[:,None]*coords[:,:,0],1-(bounds[:,1,None]+(bounds[:,3]-bounds[:,1])[:,None]*(1-coords[:,:,1]))),axis=-1)
    zoom=1+(max(1,float(material.get('zoom2',1)))-1)*np.clip(sim.age[ids]/np.maximum(sim.life[ids],1e-9),0,1)
    tint=sim.color[ids].copy();tint[:,3]*=sim.alpha2[ids]
    weights=np.column_stack((w,w2)) if material.get('blend_frames',False) else np.zeros((n,2))
    result={'pos':positions,'uv':uv(a,np.ones(n)),'uv2':uv(b,zoom),'uv_next':uv(an,np.ones(n)),'uv2_next':uv(bn,zoom),'tint':np.repeat(tint[:,None,:],6,axis=1),'frame_mix':np.repeat(weights[:,None,:],6,axis=1)}
    return {key:np.ascontiguousarray(value.reshape(-1,value.shape[-1]),dtype=np.float32) for key,value in result.items()},gaps


def build_geometry(sim,renderer,sequences,transform,right,up,eye,scale=1,texture_height=1,material=None):
    """Return triangles and diagnostic gaps. Positions are in Blender world units."""
    material=material or {}
    dual=material.get('dual_mode',-1)>=0
    kind=renderer.get('functionName','').casefold()
    empty={'pos':np.zeros((0,3)),'uv':np.zeros((0,2)),'uv2':np.zeros((0,2)),'tint':np.zeros((0,4)),'uv_next':np.zeros((0,2)),'uv2_next':np.zeros((0,2)),'frame_mix':np.zeros((0,2))}
    if kind not in RENDERERS:return empty,{f'unsupported renderer {kind}'}
    if not len(sim.age):return empty,set()
    transform=np.asarray(transform,dtype=float);right=np.asarray(right);up=np.asarray(up);eye=np.asarray(eye)
    scale=max(float(scale),.001)
    centers=(sim.position/scale)@transform[:3,:3].T+transform[:3,3]
    if kind=='render_rope':
        for spec in sim.definition.rules['operators']:
            if spec.get('functionName','').casefold()=='movement maintain position along path':
                if sim.control_points.get(int(spec.get('end control point number',1))) is None:
                    return empty,{'Rope hidden: required path endpoint is unavailable'}
        from .particlerope import geometry
        batch,gaps=geometry(sim,renderer,centers,right,up,eye,scale,material.get('mapping_height',texture_height))
        batch['uv2']=batch['uv'].copy()
        batch['uv_next']=batch['uv'].copy();batch['uv2_next']=batch['uv'].copy()
        batch['frame_mix']=np.zeros((len(batch['uv']),2),dtype=np.float32)
        if dual:gaps.add('Dual-sequence rope rendering unsupported')
        return batch,gaps
    if kind=='render_animated_sprites':
        return sprite_geometry(sim,renderer,sequences,transform,centers,right,up,eye,scale,material)
    order=np.argsort(-np.sum((centers-eye)**2,axis=1),kind='stable')
    positions=[];uvs=[];uvs2=[];uvnext=[];uv2next=[];mixes=[];colors=[];gaps=set()
    for i in order:
        sheet=sequences.get(int(sim.sequence[i]))
        if sequences and sheet is None:
            gaps.add(f'missing sheet sequence {sim.sequence[i]}');continue
        rect=rect_next=(0,0,1,1);weight=weight2=0.
        if sheet is not None:
            frame,next_frame,weight=sheet.sample(float(sim.age[i]),float(sim.life[i]),
                rate=float(renderer.get('animation rate',1)),
                fit=bool(renderer.get('animation_fit_lifetime',False)),
                fps=bool(renderer.get('use animation rate as FPS',False)))
            rect=sheet.rectangles[frame,0];rect_next=sheet.rectangles[next_frame,0]
        rect2=rect2_next=rect
        if dual:
            sheet2=sequences.get(int(sim.sequence2[i]))
            if sheet2 is None:
                gaps.add(f'missing secondary sheet sequence {sim.sequence2[i]}');continue
            frame2,next_frame2,weight2=sheet2.sample(float(sim.age[i]),float(sim.life[i]),
                rate=float(renderer.get('second sequence animation rate',renderer.get('animation rate 2',renderer.get('animation rate',1)))),
                fit=bool(renderer.get('animation_fit_lifetime',False)),
                fps=bool(renderer.get('use animation rate as FPS',False)))
            rect2=sheet2.rectangles[frame2,0];rect2_next=sheet2.rectangles[next_frame2,0]
        radius=max(0,float(sim.radius[i]))/scale
        if kind=='render_sprite_trail':
            motion=(sim.position[i]-sim.previous_position[i])*60
            speed=np.linalg.norm(motion)
            if speed<1e-9:
                # Some authored flashes stretch from initial velocity without
                # a Movement Basic operator that translates their position.
                motion=sim.velocity[i];speed=np.linalg.norm(motion)
            if speed<1e-9:continue
            lo=max(0,float(renderer.get('min length',0)))
            hi=max(lo,float(renderer.get('max length',2000)))
            length=np.clip(speed*sim.trail[i],lo,hi)
            fade=max(0,float(renderer.get('length fade in time',0)))
            if fade:length*=np.clip(sim.age[i]/fade,0,1)
            local_tail=(sim.position[i]-motion/speed*length)/scale
            tail=transform[:3,:3]@local_tail+transform[:3,3]
            if renderer.get('constrain radius to length',True):
                radius=min(radius,np.linalg.norm(centers[i]-tail)*.5)
            points=trail_quad(centers[i],tail,radius,eye,right,up)
            if points is None:continue
        else:
            basis_right,basis_up=sprite_basis(sim,renderer,transform,centers[i],right,up,eye,gaps)
            basis_normal=np.cross(basis_right,basis_up)
            yaw=sim.yaw[i]
            side=basis_right*np.cos(yaw)+basis_normal*np.sin(yaw)
            co=np.cos(sim.rotation[i]);si=np.sin(sim.rotation[i])
            x=CORNERS[:,0]*material.get('crop',(1,1))[0];y=CORNERS[:,1]*material.get('crop',(1,1))[1]
            points=centers[i]+(x*co-y*si)[:,None]*side*radius+(x*si+y*co)[:,None]*basis_up*radius
        def uv_for(bounds,zoom=1):
            u0,v0,u1,v1=bounds
            if sim.flip[i]:u0,u1=u1,u0
            coords=(CORNERS*np.asarray(material.get('crop',(1,1)))/zoom+1)/2
            return np.column_stack((u0+(u1-u0)*coords[:,0],1-(v0+(v1-v0)*(1-coords[:,1]))))
        zoom=1+(max(1,float(material.get('zoom2',1)))-1)*np.clip(sim.age[i]/max(sim.life[i],1e-9),0,1)
        uv=uv_for(rect);uv2=uv_for(rect2,zoom)
        uvs2.extend(uv2);uvnext.extend(uv_for(rect_next));uv2next.extend(uv_for(rect2_next,zoom))
        mixes.extend([(weight,weight2) if material.get('blend_frames',False) else (0,0)]*6)
        tint=sim.color[i].copy();tint[3]*=sim.alpha2[i]
        positions.extend(points);uvs.extend(uv);colors.extend([tint]*6)
    if not positions:return empty,gaps
    # GPU vertex attributes are float32. Blender's raw buffer path does not
    # reliably convert float64 NumPy arrays when filling a float vertex format.
    return {'pos':np.ascontiguousarray(positions,dtype=np.float32),
            'uv':np.ascontiguousarray(uvs,dtype=np.float32),
            'uv2':np.ascontiguousarray(uvs2,dtype=np.float32),
            'uv_next':np.ascontiguousarray(uvnext,dtype=np.float32),
            'uv2_next':np.ascontiguousarray(uv2next,dtype=np.float32),
            'frame_mix':np.ascontiguousarray(mixes,dtype=np.float32),
            'tint':np.ascontiguousarray(colors,dtype=np.float32)},gaps
