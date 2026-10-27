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


def build_geometry(sim,renderer,sequences,transform,right,up,eye,scale=1,texture_height=1):
    """Return triangles and diagnostic gaps. Positions are in Blender world units."""
    kind=renderer.get('functionName','').casefold()
    empty={'pos':np.zeros((0,3)),'uv':np.zeros((0,2)),'tint':np.zeros((0,4))}
    if kind not in RENDERERS:return empty,{f'unsupported renderer {kind}'}
    if not len(sim.age):return empty,set()
    transform=np.asarray(transform,dtype=float);right=np.asarray(right);up=np.asarray(up);eye=np.asarray(eye)
    scale=max(float(scale),.001)
    centers=(sim.position/scale)@transform[:3,:3].T+transform[:3,3]
    if kind=='render_rope':
        from .particlerope import geometry
        return geometry(sim,renderer,centers,right,up,eye,scale,texture_height)
    order=np.argsort(-np.sum((centers-eye)**2,axis=1),kind='stable')
    normal=np.cross(right,up)
    positions=[];uvs=[];colors=[];gaps=set()
    for i in order:
        sheet=sequences.get(int(sim.sequence[i]))
        if sequences and sheet is None:
            gaps.add(f'missing sheet sequence {sim.sequence[i]}');continue
        rect=(0,0,1,1)
        if sheet is not None:
            frame=sheet.frame(float(sim.age[i]),float(sim.life[i]),
                rate=float(renderer.get('animation rate',1)),
                fit=bool(renderer.get('animation_fit_lifetime',False)),
                fps=bool(renderer.get('use animation rate as FPS',False)))
            rect=sheet.rectangles[frame,0]
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
        u0,v0,u1,v1=rect
        if sim.flip[i]:u0,u1=u1,u0
        uv=np.column_stack((u0+(u1-u0)*(CORNERS[:,0]+1)/2,
                            1-(v0+(v1-v0)*(1-CORNERS[:,1])/2)))
        tint=sim.color[i].copy();tint[3]*=sim.alpha2[i]
        positions.extend(points);uvs.extend(uv);colors.extend([tint]*6)
    if not positions:return empty,gaps
    # GPU vertex attributes are float32. Blender's raw buffer path does not
    # reliably convert float64 NumPy arrays when filling a float vertex format.
    return {'pos':np.ascontiguousarray(positions,dtype=np.float32),
            'uv':np.ascontiguousarray(uvs,dtype=np.float32),
            'tint':np.ascontiguousarray(colors,dtype=np.float32)},gaps
