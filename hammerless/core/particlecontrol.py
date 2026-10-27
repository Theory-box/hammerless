"""Bounded rigid control-point transforms for the independent preview runtime."""
import numpy as np


def rigid(matrix):
    matrix=np.asarray(matrix,dtype=float)
    if matrix.shape!=(4,4) or not np.isfinite(matrix).all():
        raise ValueError('control point needs a finite 4x4 transform')
    result=np.eye(4)
    u,_,v=np.linalg.svd(matrix[:3,:3])
    rotation=u@v
    if np.linalg.det(rotation)<0:
        u[:,-1]*=-1;rotation=u@v
    result[:3,:3]=rotation;result[:3,3]=matrix[:3,3]
    return result


def blend(a,b,t):
    # A bounded rigid approximation; exact 180-degree interpolation is not
    # claimed to match the engine. Endpoints are preserved exactly.
    if t<=0:return a.copy()
    if t>=1:return b.copy()
    return rigid(a*(1-t)+b*t)


class ControlPoints:
    def __init__(self):
        self.current={0:np.eye(4)}
        self.previous={0:np.eye(4)}

    def set(self,index,matrix):
        index=int(index)
        if not 0<=index<64:raise ValueError('control point index must be 0..63')
        value=rigid(matrix)
        if index not in self.current:self.previous[index]=value.copy()
        self.current[index]=value

    def begin(self,inherited):
        self.previous={k:v.copy() for k,v in self.current.items()}
        for index,matrix in inherited.items():self.set(index,matrix)

    def get(self,index,warnings=None):
        index=int(index)
        if index not in self.current:
            if warnings is not None:warnings.add(f'Missing control point {index}')
            return None
        return self.current[index]

    def velocity(self,index,dt,warnings=None):
        point=self.get(index,warnings)
        if point is None:return np.zeros(3)
        previous=self.previous.get(int(index),point)
        return (point[:3,3]-previous[:3,3])/max(dt,1e-9)


def point(sim,index):
    return sim.control_points.get(index,sim.warnings)


def local_vector(sim,value,index=0):
    transform=point(sim,index)
    return np.asarray(value) if transform is None else np.asarray(value)@transform[:3,:3].T


def lock(sim,spec,dt):
    index=int(spec.get('control_point_number',0))
    current=point(sim,index)
    if current is None or not len(sim.age):return
    previous=sim.control_points.previous.get(index,current)
    def time(prefix,default,stream):
        lo=float(spec.get(prefix+'_min',default));hi=float(spec.get(prefix+'_max',default))
        return lo+(hi-lo)*sim.stable_random(stream)**max(.001,float(spec.get(prefix+'_exponent',1)))
    start=time('start_fadeout',1,40);end=time('end_fadeout',1,41)
    age=sim.age/np.maximum(sim.life,1e-9)
    weight=np.where(end>start,np.clip((end-age)/np.maximum(end-start,1e-9),0,1),(age<end).astype(float))
    if spec.get('lock rotation',False):
        rotation=current[:3,:3]@previous[:3,:3].T
        moved=(sim.position-previous[:3,3])@rotation.T+current[:3,3]
    else:moved=sim.position+(current[:3,3]-previous[:3,3])
    # Newly born particles already start in the current control-point frame.
    weight=np.where(sim.ids>=sim.step_first_id,0,weight)
    sim.position+=(moved-sim.position)*weight[:,None]


def set_positions(sim,spec):
    if spec.get('Set positions in world space',False):
        sim.warnings.add('Set Control Point Positions: world-space placement is unsupported');return
    base=point(sim,int(spec.get('Control Point to offset positions from',0)))
    if base is None:return
    base=base.copy()
    for label,default in [('First',1),('Second',2),('Third',3),('Fourth',4)]:
        # Only authored slots are changed; avoid inventing unmentioned points.
        if not any(k.startswith(label+' Control Point') for k in spec):continue
        index=int(spec.get(label+' Control Point Number',default))
        if not 0<=index<64:
            sim.warnings.add(f'Invalid control point {index}');continue
        value=base.copy()
        value[:3,3]+=base[:3,:3]@np.asarray(spec.get(label+' Control Point Location',(0,0,0)))
        sim.control_points.set(index,value)


def parent_position(sim,spec,selection):
    parent=sim.parent
    if parent is None or not len(parent.age):return
    count=len(sim.age[selection])
    if spec.get('Random Parent Particle Distribution',False):
        indices=sim.rng.integers(0,len(parent.age),count)
    else:indices=(sim.ids[selection]%len(parent.age)).astype(int)
    sim.position[selection]=parent.position[indices]
    motion=(parent.position-parent.previous_position)/sim.step_dt
    speed=np.linalg.norm(motion,axis=1)
    motion=np.where((speed<1e-9)[:,None],parent.velocity,motion)
    sim.velocity[selection]+=motion[indices]*float(spec.get('Inherited Velocity Scale',0))
