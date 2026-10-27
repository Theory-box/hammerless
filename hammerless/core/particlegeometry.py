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
        from .particlerope import geometry
        batch,gaps=geometry(sim,renderer,centers,right,up,eye,scale,texture_height)
        batch['uv2']=batch['uv'].copy()
        batch['uv_next']=batch['uv'].copy();batch['uv2_next']=batch['uv'].copy()
        batch['frame_mix']=np.zeros((len(batch['uv']),2),dtype=np.float32)
        if dual:gaps.add('Dual-sequence rope rendering unsupported')
        return batch,gaps
    order=np.argsort(-np.sum((centers-eye)**2,axis=1),kind='stable')
    normal=np.cross(right,up)
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
                rate=float(renderer.get('animation rate 2',renderer.get('animation rate',1))),
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
            yaw=sim.yaw[i]
            side=right*np.cos(yaw)+normal*np.sin(yaw)
            co=np.cos(sim.rotation[i]);si=np.sin(sim.rotation[i])
            x=CORNERS[:,0];y=CORNERS[:,1]
            points=centers[i]+(x*co-y*si)[:,None]*side*radius+(x*si+y*co)[:,None]*up*radius
        def uv_for(bounds,zoom=1):
            u0,v0,u1,v1=bounds
            if sim.flip[i]:u0,u1=u1,u0
            coords=(CORNERS/zoom+1)/2
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
