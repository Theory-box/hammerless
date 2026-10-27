"""Shared preview fields. Public PCF attribute IDs; internal angles are radians."""
import numpy as np

SCALARS = {1:'life',3:'radius',4:'rotation',5:'rotation_speed',8:'birth',
           9:'sequence',10:'trail',12:'yaw',16:'alpha2'}
SCALAR_READ = set(SCALARS) | {7,11}
SCALAR_WRITE = SCALAR_READ - {11}
VECTOR_FIELDS = {0,6}
ANGLE_FIELDS = {4,5,12}


def read(sim, field, selection=slice(None), initial=False):
    if initial and field in sim.initial_fields:
        return sim.initial_fields[field][selection]
    if field in SCALARS:return getattr(sim,SCALARS[field])[selection]
    if field==7:return sim.color[selection,3]
    if field==11:return sim.ids[selection].astype(float)
    if field==0:return sim.position[selection]
    if field==6:return sim.color[selection,:3]
    raise ValueError(f'unsupported particle field {field}')


def write(sim, field, values, selection=slice(None)):
    values=np.asarray(values)
    if not np.isfinite(values).all():raise ValueError('nonfinite particle field value')
    if field in (7,16):values=np.clip(values,0,1)
    if field in (3,10):values=np.maximum(values,0)
    if field==1:values=np.maximum(values,1e-6)
    if field==9:values=np.clip(np.rint(values),0,2147483647).astype(np.int32)
    if field in SCALARS:
        getattr(sim,SCALARS[field])[selection]=values
        if field==8:sim.age[selection]=sim.evaluation_time-values
    elif field==7:sim.color[selection,3]=values
    elif field==0:sim.position[selection]=values
    elif field==6:sim.color[selection,:3]=values
    else:raise ValueError(f'unsupported writable particle field {field}')


def remap_fraction(values, low, high):
    if high==low:return (values>=high).astype(float)
    return np.clip((values-low)/(high-low),0,1)


def remap(sim,spec,selection=slice(None),vector=False,initializing=False):
    source=int(spec.get('input field',8));target=int(spec.get('output field',0 if vector else 3))
    if source not in SCALAR_READ or target not in (VECTOR_FIELDS if vector else SCALAR_WRITE):return
    start=float(spec.get('emitter lifetime start time (seconds)',-1))
    end=float(spec.get('emitter lifetime end time (seconds)',-1))
    if (start>=0 and sim.time<start) or (end>=0 and sim.time>end):return
    value=read(sim,source,selection).copy()
    low=float(spec.get('input minimum',0));high=float(spec.get('input maximum',1))
    t=remap_fraction(value,low,high)
    out_low=np.asarray(spec.get('output minimum',(0,0,0) if vector else 0))
    out_high=np.asarray(spec.get('output maximum',(1,1,1) if vector else 1))
    output=out_low+(out_high-out_low)*(t[:,None] if vector else t)
    if spec.get('output is scalar of initial random range',False):
        output=output*read(sim,target,selection,initial=not initializing)
    elif target in ANGLE_FIELDS:
        output=np.deg2rad(output)
    if spec.get('only active within specified input range',False):
        active=(value>=min(low,high)) & (value<=max(low,high))
        output=np.where(active[:,None] if vector else active,output,read(sim,target,selection))
    write(sim,target,output,selection)


def field_gaps(name,spec):
    if name in ('remap initial scalar','remap scalar','remap scalar to vector'):
        vector=name=='remap scalar to vector'
        checks=[('input field',8,SCALAR_READ),('output field',0 if vector else 3,VECTOR_FIELDS if vector else SCALAR_WRITE)]
    elif name=='remap noise to scalar':checks=[('output field',3,SCALAR_WRITE)]
    else:return []
    return [f'unsupported {key} {spec.get(key,default)}' for key,default,supported in checks
            if int(spec.get(key,default)) not in supported]


REMAP_PARAMETERS=('input field','output field','input minimum','input maximum','output minimum','output maximum',
    'only active within specified input range','output is scalar of initial random range',
    'emitter lifetime start time (seconds)','emitter lifetime end time (seconds)')
