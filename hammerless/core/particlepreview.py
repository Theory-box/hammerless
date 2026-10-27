"""Independent PCF preview runtime. No Valve binaries or third-party simulator code.

Support is deliberately conservative: implemented rules are preview approximations,
not a claim of Source parity. Unknown rules and parameters remain in the audit.
"""
from dataclasses import dataclass, field
import math
import struct
import numpy as np
from .particles import read_dmx
from . import particlefields as fields
from . import particlecontrol as control
from .particleoscillation import offsets as oscillation_offsets, PARAMETERS as OSCILLATION_PARAMETERS

CATEGORIES = ('emitters', 'initializers', 'operators', 'forces', 'constraints', 'renderers')
RULES = {}
SYSTEM_PARAMETERS = {'max_particles','radius','color','rotation','rotation_speed','sequence_number','sequence_number 1','material'}
OSCILLATION_FIELDS = {'oscillate scalar': (7, {3,4,7,12,16}), 'oscillate vector': (0, {0,6})}


def rule(category, name, *parameters):
    def register(fn):
        RULES[category, name.casefold()] = (fn, set(parameters))
        return fn
    return register


@dataclass
class Definition:
    name: str
    file: str
    attrs: dict
    rules: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


def load_library(content):
    """Manifest order matches game_effects. Keep failures instead of hiding them."""
    definitions, errors = {}, []
    manifest = (content.read('particles/particles_manifest.txt') or b'').decode('latin1')
    for line in manifest.splitlines():
        tokens = line.replace('"', ' ').split()
        if len(tokens) < 2 or tokens[0].lower() != 'file':
            continue
        path = tokens[1].lstrip('!').replace('\\', '/')
        try:
            data = content.read(path)
            if data is None:
                raise ValueError('file missing')
            elements = read_dmx(data)
            def ref(value):
                if not isinstance(value, tuple) or value[0] != 'elem' or not 0 <= value[1] < len(elements):
                    raise ValueError('invalid element reference')
                return elements[value[1]]
            for el in elements:
                if el.type != 'DmeParticleSystemDefinition':
                    continue
                rules = {c: [dict(ref(r).attrs) for r in el.attrs.get(c, [])] for c in CATEGORIES}
                children = []
                for r in el.attrs.get('children', []):
                    child = ref(r).attrs
                    children.append((ref(child['child']).name.casefold(), float(child.get('delay', 0))))
                definitions[el.name.casefold()] = Definition(el.name, path, el.attrs, rules, children)
        except (ValueError, IndexError, KeyError, TypeError, struct.error) as exc:
            errors.append({'file': path, 'error': str(exc)})
    return definitions, errors


def audit(definitions):
    result = {}
    for key, definition in definitions.items():
        missing, parameters = [], []
        for category, rules in definition.rules.items():
            for spec in rules:
                name = spec.get('functionName', '')
                entry = RULES.get((category, name.casefold()))
                if entry is None:
                    missing.append(category + ': ' + name)
                else:
                    parameters.extend(name + ': ' + p for p in spec if p != 'functionName' and p not in entry[1])
                    parameters.extend(name+': '+gap for gap in fields.field_gaps(name.casefold(),spec))
                    if name.casefold() in OSCILLATION_FIELDS:
                        default, supported = OSCILLATION_FIELDS[name.casefold()]
                        value = int(spec.get('oscillation field',default))
                        if value not in supported:
                            parameters.append(f'{name}: unsupported oscillation field {value}')
        if 'sequence_number 1' in definition.attrs or any(p.get('functionName','').casefold()=='sequence two random' for p in definition.rules['initializers']):
            parameters.append('material: dual-sequence blending unsupported')
        system_parameters = sorted(set(definition.attrs)-set(CATEGORIES)-{'children'}-SYSTEM_PARAMETERS)
        result[key] = {'missing_rules': sorted(set(missing)), 'unmapped_parameters': sorted(set(parameters)),
                       'unmapped_system_parameters': system_parameters,
                       'children': [c for c, _ in definition.children], 'status': 'approximate'}
    def collect(key, path):
        if key in path:
            return {'child cycle: ' + key}
        if key not in result:
            return {'missing child: ' + key}
        r = result[key]
        gaps = set(r['missing_rules']) | set(r['unmapped_parameters'])
        gaps.update('system: '+p for p in r['unmapped_system_parameters'])
        for child in r['children']:
            gaps |= collect(child, path | {key})
        return gaps
    for key in result:
        result[key]['recursive_gaps'] = sorted(collect(key, set()))
    return result


class Simulation:
    """Fixed-step, bounded, seeded array simulation of one definition in Source units."""
    def __init__(self, definition, seed=0, limit=2048, sequences=None, parent=None):
        self.definition = definition
        self.rng = np.random.default_rng(seed)
        self.force_rng = np.random.default_rng(np.random.SeedSequence([seed, 1]))
        self.limit = max(0, min(limit, int(definition.attrs.get('max_particles', limit))))
        self.time = 0.0
        self.evaluation_time = 0.0
        self.sequences = sequences or {}
        self.warnings = set()
        self.initial_fields = {}
        self.control_points=control.ControlPoints()
        self.parent=parent;self.children=[];self.incoming_points={}
        self.step_dt=1/60
        self.step_first_id=0
        self.accumulator = 0.0
        self.emission = {}
        self.seed = seed
        self.next_id = 0
        self.ids = np.zeros(0,dtype=np.uint64)
        self.rotation = np.zeros(0)
        self.rotation_speed = np.zeros(0)
        self.yaw = np.zeros(0)
        self.trail = np.zeros(0)
        self.alpha2 = np.zeros(0)
        self.birth = np.zeros(0)
        self.sequence = np.zeros(0,dtype=np.int32)
        self.sequence2 = np.zeros(0,dtype=np.int32)
        self.flip = np.zeros(0,dtype=bool)
        self.operator_index = 0
        self.position = np.zeros((0, 3))
        self.previous_position = np.zeros((0, 3))
        self.velocity = np.zeros((0, 3))
        self.color = np.zeros((0, 4))
        self.initial_color = np.zeros((0, 4))
        self.radius = np.zeros(0)
        self.initial_radius = np.zeros(0)
        self.age = np.zeros(0)
        self.life = np.zeros(0)

    def spawn(self, count):
        if any(p.get('functionName','').casefold()=='position from parent particles' for p in self.definition.rules['initializers']):
            if self.parent is None:
                self.warnings.add('Position From Parent Particles: no parent system');return
            if not len(self.parent.age):return
        count = max(0, min(int(count), self.limit-len(self.age)))
        if not count:
            return
        start = len(self.age)
        self.ids = np.concatenate((self.ids,np.arange(self.next_id,self.next_id+count,dtype=np.uint64)))
        self.next_id += count
        self.flip = np.concatenate((self.flip,np.zeros(count,dtype=bool)))
        self.rotation = np.concatenate((self.rotation,np.full(count,math.radians(float(self.definition.attrs.get('rotation',0))))))
        self.sequence = np.concatenate((self.sequence,np.full(count,int(self.definition.attrs.get('sequence_number',0)),dtype=np.int32)))
        self.sequence2 = np.concatenate((self.sequence2,np.full(count,int(self.definition.attrs.get('sequence_number 1',0)),dtype=np.int32)))
        attrs = self.definition.attrs
        origin=self.control_points.current[0][:3,3]
        for name, values in [('position', np.tile(origin,(count,1))), ('velocity', np.zeros((count, 3))),
                             ('previous_position', np.zeros((count,3))),
                             ('rotation_speed', np.full(count,math.radians(float(attrs.get('rotation_speed',0))))),
                             ('yaw', np.zeros(count)), ('trail', np.full(count,.1)),
                             ('alpha2',np.ones(count)), ('birth',np.full(count,self.time)),
                             ('color', np.tile(np.array(attrs.get('color', (255,255,255,255)))/255, (count,1))),
                             ('radius', np.full(count, float(attrs.get('radius', 5)))),
                             ('age', np.zeros(count)), ('life', np.ones(count))]:
            setattr(self, name, np.concatenate((getattr(self, name), values)))
        for spec in self.definition.rules['initializers']:
            entry = RULES.get(('initializers', spec.get('functionName', '').casefold()))
            if entry:
                entry[0](self, spec, slice(start, None))
        self.initial_color = np.concatenate((self.initial_color, self.color[start:].copy()))
        self.initial_radius = np.concatenate((self.initial_radius, self.radius[start:].copy()))
        self.previous_position[start:]=self.position[start:]
        for field in fields.SCALAR_WRITE | fields.VECTOR_FIELDS:
            values=fields.read(self,field,slice(start,None)).copy()
            self.initial_fields[field]=np.concatenate((self.initial_fields.get(field,values[:0]),values))

    def advance(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('advance needs finite nonnegative time')
        self.accumulator += seconds
        dt = 1 / 60
        while self.accumulator + 1e-10 >= dt:
            self.accumulator -= dt
            self.step(dt)
            self.control_points.previous={k:v.copy() for k,v in self.control_points.current.items()}

    def step(self,dt):
        self.step_dt=dt;self.evaluation_time=self.time
        self.step_first_id=self.next_id
        # Definitions of CP positions must be ready before birth initializers.
        for spec in self.definition.rules['operators']:
            if spec.get('functionName','').casefold()=='set control point positions':control.set_positions(self,spec)
        for i,spec in enumerate(self.definition.rules['emitters']):
            entry=RULES.get(('emitters',spec.get('functionName','').casefold()))
            if entry:entry[0](self,spec,(i,dt))
        self.age+=dt;self.evaluation_time=self.time+dt
        self.previous_position=self.position.copy()
        self.color=self.initial_color.copy();self.radius=self.initial_radius.copy()
        self.alpha2=self.initial_fields.get(16,self.alpha2).copy()
        for self.operator_index,spec in enumerate(self.definition.rules['operators']):
            name=spec.get('functionName','').casefold()
            if name=='set control point positions':continue
            entry=RULES.get(('operators',name))
            if entry:entry[0](self,spec,dt)
        self.time+=dt

    def retain(self, mask):
        for name in ('position','previous_position','velocity','color','initial_color','radius','initial_radius','age','life','ids','rotation','sequence','sequence2','flip',
                     'rotation_speed','yaw','trail','alpha2','birth'):
            setattr(self, name, getattr(self, name)[mask])
        for field in self.initial_fields:self.initial_fields[field]=self.initial_fields[field][mask]

    def stable_random(self, stream=0):
        """Stable per-particle/operator samples, unaffected by particle death or frame rate."""
        x=(self.ids+np.uint64((self.seed+self.operator_index*65537+stream*104729)&0xffffffff)) & np.uint64(0xffffffff)
        x=((x ^ (x >> 16))*np.uint64(0x45d9f3b)) & np.uint64(0xffffffff)
        x=((x ^ (x >> 16))*np.uint64(0x45d9f3b)) & np.uint64(0xffffffff)
        return (x ^ (x >> 16)).astype(np.float64)/4294967296


@rule('emitters', 'emit_continuously', 'emission_rate', 'emission_start_time', 'emission_duration')
def continuous(s, p, tick):
    i, dt = tick
    start = float(p.get('emission_start_time', 0)); duration = float(p.get('emission_duration', 0))
    end = start+duration if duration > 0 else float('inf')
    span = max(0, min(s.time+dt, end)-max(s.time, start))
    credit = s.emission.get(i, 0)+span*max(0, float(p.get('emission_rate', 100)))
    count = int(credit+1e-9)
    s.emission[i] = credit-count
    s.spawn(count)


@rule('emitters', 'emit_instantaneously', 'num_to_emit', 'emission_start_time')
def burst(s, p, tick):
    i, dt = tick
    if i not in s.emission and s.time+dt >= float(p.get('emission_start_time', 0)):
        s.spawn(p.get('num_to_emit', 100)); s.emission[i] = True


def random_scalar(target, lo, hi, default, exponent, scale=1):
    def apply(s, p, sl):
        a, b = float(p.get(lo, default)), float(p.get(hi, default))
        t=s.rng.random(len(s.age[sl]))**max(.001,float(p.get(exponent,1)))
        getattr(s, target)[sl] = (a+(b-a)*t)*scale
    return apply


for name, target, lo, hi, default, exponent in [('Lifetime Random','life','lifetime_min','lifetime_max',1,'lifetime_random_exponent'),
                                    ('Radius Random','radius','radius_min','radius_max',5,'radius_random_exponent')]:
    rule('initializers', name, lo, hi, exponent)(random_scalar(target,lo,hi,default,exponent))


@rule('initializers', 'Color Random', 'color1', 'color2')
def color(s, p, sl):
    a=np.asarray(p.get('color1',(255,255,255,255)))[:3]/255
    b=np.asarray(p.get('color2',(255,255,255,255)))[:3]/255
    s.color[sl,:3] = a+(b-a)*s.rng.random((len(s.age[sl]),1))


@rule('initializers', 'Alpha Random', 'alpha_min', 'alpha_max','alpha_random_exponent')
def alpha(s,p,sl):
    a,b=float(p.get('alpha_min',255)),float(p.get('alpha_max',255))
    t=s.rng.random(len(s.age[sl]))**max(.001,float(p.get('alpha_random_exponent',1)))
    s.color[sl,3]=np.clip((a+(b-a)*t)/255,0,1)


@rule('initializers', 'Position Within Sphere Random', 'distance_min', 'distance_max', 'speed_min', 'speed_max',
      'distance_bias','distance_bias_absolute_value','speed_random_exponent',
      'speed_in_local_coordinate_system_min','speed_in_local_coordinate_system_max','control_point_number')
def sphere(s,p,sl):
    count=len(s.age[sl]);direction=s.rng.normal(size=(count,3))
    direction/=np.maximum(np.linalg.norm(direction,axis=1,keepdims=True),1e-12)
    direction=np.where(np.asarray(p.get('distance_bias_absolute_value',(0,0,0)))!=0,np.abs(direction),direction)
    direction*=np.asarray(p.get('distance_bias',(1,1,1)))
    direction/=np.maximum(np.linalg.norm(direction,axis=1,keepdims=True),1e-12)
    lo,hi=sorted((max(0,float(p.get('distance_min',0))),max(0,float(p.get('distance_max',0)))))
    radius=np.cbrt(s.rng.uniform(lo**3,hi**3,count))
    cp=int(p.get('control_point_number',0));point=control.point(s,cp)
    if point is not None:s.position[sl]=point[:3,3]+control.local_vector(s,direction*radius[:,None],cp)
    lo,hi=sorted((float(p.get('speed_min',0)),float(p.get('speed_max',0))))
    speed=lo+(hi-lo)*s.rng.random(count)**max(.001,float(p.get('speed_random_exponent',1)))
    s.velocity[sl]+=control.local_vector(s,direction*speed[:,None],cp)
    a=np.asarray(p.get('speed_in_local_coordinate_system_min',(0,0,0)))
    b=np.asarray(p.get('speed_in_local_coordinate_system_max',(0,0,0)))
    s.velocity[sl]+=control.local_vector(s,s.rng.uniform(np.minimum(a,b),np.maximum(a,b),(count,3)),cp)


@rule('initializers', 'Position Within Box Random', 'min', 'max')
def box(s,p,sl):
    a=np.array(p.get('min',(0,0,0)));b=np.array(p.get('max',(0,0,0)))
    s.position[sl]+=control.local_vector(s,s.rng.uniform(np.minimum(a,b),np.maximum(a,b),(len(s.age[sl]),3)))


@rule('initializers', 'Position Modify Offset Random', 'offset min', 'offset max')
def offset(s,p,sl):
    box(s,{'min':p.get('offset min',(0,0,0)),'max':p.get('offset max',(0,0,0))},sl)


@rule('operators', 'Movement Basic', 'gravity', 'drag')
def movement(s,p,dt):
    acceleration=np.broadcast_to(p.get('gravity',(0,0,0)),s.velocity.shape).astype(float).copy()
    for spec in s.definition.rules['forces']:
        entry=RULES.get(('forces',spec.get('functionName','').casefold()))
        if entry:
            acceleration+=entry[0](s,spec,dt)
    s.velocity+=acceleration*dt
    s.velocity*=max(0,1-float(p.get('drag',0)))**(dt*30)
    s.position+=s.velocity*dt


@rule('operators', 'Lifespan Decay')
def decay(s,p,dt):
    s.retain(s.age < s.life)


@rule('operators', 'Radius Scale', 'start_time', 'end_time', 'radius_start_scale', 'radius_end_scale','scale_bias','ease_in_and_out')
def radius_scale(s,p,dt):
    start=float(p.get('start_time',0));end=float(p.get('end_time',1))
    t=np.clip((s.age/np.maximum(s.life,1e-9)-start)/max(end-start,1e-9),0,1)
    bias=np.clip(float(p.get('scale_bias',.5)),1e-6,1-1e-6)
    t=t/((1/bias-2)*(1-t)+1)
    if p.get('ease_in_and_out',False):t=smooth(t)
    s.radius*=float(p.get('radius_start_scale',1))*(1-t)+float(p.get('radius_end_scale',1))*t


@rule('renderers', 'render_animated_sprites', 'animation rate', 'animation_fit_lifetime', 'use animation rate as FPS')
def sprites(s,p,dt):
    pass


@rule('initializers','Sequence Random','sequence_min','sequence_max')
def sequence(s,p,sl):
    lo,hi=sorted((int(p.get('sequence_min',0)),int(p.get('sequence_max',0))))
    s.sequence[sl]=s.rng.integers(lo,hi+1,len(s.age[sl]))


@rule('initializers','Rotation Random','rotation_initial','rotation_offset_min','rotation_offset_max','rotation_random_exponent')
def rotation(s,p,sl):
    lo,hi=sorted((float(p.get('rotation_offset_min',0)),float(p.get('rotation_offset_max',360))))
    t=s.rng.random(len(s.age[sl]))**max(.001,float(p.get('rotation_random_exponent',1)))
    s.rotation[sl]=np.deg2rad(float(p.get('rotation_initial',0))+lo+(hi-lo)*t)


def smooth(values):
    t=np.clip(values,0,1)
    return t*t*(3-2*t)


def fade_random(s,p,fade_in):
    prefix='fade in time' if fade_in else 'fade out time'
    lo=float(p.get(prefix+' min',.25));hi=float(p.get(prefix+' max',.25))
    exponent=max(.001,float(p.get(prefix+' exponent',1)))
    boundary=lo+(hi-lo)*s.stable_random()**exponent
    proportional=bool(p.get('proportional 0/1',True))
    age=s.age/np.maximum(s.life,1e-9) if proportional else s.age
    if fade_in:
        factor=smooth(age/np.maximum(boundary,1e-9))
    else:
        end=np.ones(len(age)) if proportional else s.life
        factor=smooth((end-age)/np.maximum(boundary,1e-9))
    s.color[:,3]*=factor


@rule('operators','Alpha Fade In Random','fade in time min','fade in time max','fade in time exponent','proportional 0/1')
def fade_in(s,p,dt):fade_random(s,p,True)


@rule('operators','Alpha Fade Out Random','fade out time min','fade out time max','fade out time exponent','proportional 0/1')
def fade_out(s,p,dt):fade_random(s,p,False)


@rule('operators','Color Fade','color_fade','fade_start_time','fade_end_time','ease_in_and_out')
def color_fade(s,p,dt):
    lo=float(p.get('fade_start_time',0));hi=float(p.get('fade_end_time',1))
    t=np.clip((s.age/np.maximum(s.life,1e-9)-lo)/max(hi-lo,1e-9),0,1)
    if p.get('ease_in_and_out',True):t=smooth(t)
    target=np.array(p.get('color_fade',(255,255,255,255)))[:3]/255
    s.color[:,:3]=s.color[:,:3]*(1-t[:,None])+target*t[:,None]


@rule('initializers','Rotation Yaw Flip Random','Flip Percentage')
def yaw_flip(s,p,sl):
    # For camera-facing cards a 180-degree yaw is a horizontal UV mirror.
    # Arbitrary yaw/orientation modes remain unsupported.
    s.flip[sl]=s.rng.random(len(s.age[sl])) < np.clip(float(p.get('Flip Percentage',.5)),0,1)


@rule('initializers','Velocity Noise','Spatial Coordinate Offset','Spatial Noise Coordinate Scale',
      'Time Coordinate Offset','Time Noise Coordinate Scale','output minimum','output maximum',
      'Absolute Value','Invert Abs Value')
def velocity_noise(s,p,sl):
    from .particlenoise import noise4
    count=len(s.age[sl])
    coords=np.zeros((count,4))
    coords[:,:3]=(s.position[sl]+np.asarray(p.get('Spatial Coordinate Offset',(0,0,0))))*float(p.get('Spatial Noise Coordinate Scale',.01))
    coords[:,3]=(s.time+float(p.get('Time Coordinate Offset',0)))*float(p.get('Time Noise Coordinate Scale',1))
    values=noise4(coords,s.seed)
    absolute=np.asarray(p.get('Absolute Value',(0,0,0)))!=0
    inverted=np.asarray(p.get('Invert Abs Value',(0,0,0)))!=0
    values=np.where(absolute,np.abs(values*2-1),values)
    values=np.where(absolute & inverted,1-values,values)
    lo=np.asarray(p.get('output minimum',(0,0,0)))
    hi=np.asarray(p.get('output maximum',(1,1,1)))
    s.velocity[sl]+=lo+(hi-lo)*values
    # CP transforms/local-space selection are deliberately not claimed by the
    # registry: the preview currently simulates in the emitter's local frame.


@rule('initializers','Velocity Random','random_speed_min','random_speed_max',
      'speed_in_local_coordinate_system_min','speed_in_local_coordinate_system_max')
def velocity_random(s,p,sl):
    count=len(s.age[sl])
    direction=s.rng.normal(size=(count,3))
    direction/=np.maximum(np.linalg.norm(direction,axis=1,keepdims=True),1e-12)
    lo,hi=sorted((float(p.get('random_speed_min',0)),float(p.get('random_speed_max',0))))
    s.velocity[sl]+=direction*s.rng.uniform(lo,hi,count)[:,None]
    lo=np.asarray(p.get('speed_in_local_coordinate_system_min',(0,0,0)))
    hi=np.asarray(p.get('speed_in_local_coordinate_system_max',(0,0,0)))
    s.velocity[sl]+=control.local_vector(s,s.rng.uniform(np.minimum(lo,hi),np.maximum(lo,hi),(count,3)))


@rule('forces','random force','min force','max force')
def random_force(s,p,dt):
    lo=np.asarray(p.get('min force',(0,0,0)),dtype=float)
    hi=np.asarray(p.get('max force',(0,0,0)),dtype=float)
    return s.force_rng.uniform(np.minimum(lo,hi),np.maximum(lo,hi),s.velocity.shape)


@rule('operators','Rotation Spin Roll','spin_rate_degrees','spin_rate_min','spin_stop_time')
def spin_roll(s,p,dt):
    rate=float(p.get('spin_rate_degrees',0))
    minimum=float(p.get('spin_rate_min',0))
    stop=float(p.get('spin_stop_time',0))
    # Independent approximation: integrate a linear spin-down in seconds,
    # including the partial step that crosses the stop time.
    if stop>0:
        a=np.maximum(0,s.age-dt);b=s.age
        def integral(t):
            ramp=np.minimum(t,stop)
            return rate*ramp+(minimum-rate)*ramp*ramp/(2*stop)+minimum*np.maximum(t-stop,0)
        degrees=integral(b)-integral(a)
    else:
        degrees=rate*dt
    s.rotation+=np.deg2rad(degrees)


@rule('operators','Alpha Fade and Decay','start_alpha','end_alpha',
      'start_fade_in_time','end_fade_in_time','start_fade_out_time','end_fade_out_time')
def fade_and_decay(s,p,dt):
    s.retain(s.age < s.life)
    age=s.age/np.maximum(s.life,1e-9)
    start=float(p.get('start_fade_in_time',0));end=float(p.get('end_fade_in_time',.5))
    out_start=float(p.get('start_fade_out_time',.5));out_end=float(p.get('end_fade_out_time',1))
    alpha=s.initial_color[:,3].copy()
    # Linear preview interpolation; no claim of exact engine easing/defaults.
    if end>start:
        t=np.clip((age-start)/(end-start),0,1)
        alpha=float(p.get('start_alpha',0))*(1-t)+alpha*t
    if out_end>out_start:
        t=np.clip((age-out_start)/(out_end-out_start),0,1)
        alpha=alpha*(1-t)+float(p.get('end_alpha',0))*t
    else:
        alpha=np.where(age>=out_end,float(p.get('end_alpha',0)),alpha)
    s.color[:,3]=np.clip(alpha,0,1)


@rule('operators','Oscillate Scalar',*OSCILLATION_PARAMETERS)
def oscillate_scalar(s,p,dt):
    if not len(s.age):return
    field=int(p.get('oscillation field',7))
    if field not in OSCILLATION_FIELDS['oscillate scalar'][1]:return
    total,delta=oscillation_offsets(s,p,dt,1)
    if field==3:s.radius=np.maximum(0,s.radius+total[:,0])
    elif field==4:s.rotation+=delta[:,0]
    elif field==7:s.color[:,3]=np.clip(s.color[:,3]+total[:,0],0,1)
    elif field==12:s.yaw+=delta[:,0]
    elif field==16:s.alpha2=np.clip(s.alpha2+total[:,0],0,1)


@rule('operators','Oscillate Vector',*OSCILLATION_PARAMETERS)
def oscillate_vector(s,p,dt):
    if not len(s.age):return
    field=int(p.get('oscillation field',0))
    if field not in OSCILLATION_FIELDS['oscillate vector'][1]:return
    total,delta=oscillation_offsets(s,p,dt,3)
    if field==0:s.position+=delta
    elif field==6:s.color[:,:3]=np.clip(s.color[:,:3]+total,0,1)


@rule('initializers','Remap Initial Scalar',*fields.REMAP_PARAMETERS)
def remap_initial(s,p,sl):fields.remap(s,p,sl,initializing=True)


@rule('operators','Remap Scalar',*fields.REMAP_PARAMETERS)
def remap_scalar(s,p,dt):fields.remap(s,p)


@rule('initializers','Remap Scalar to Vector',*fields.REMAP_PARAMETERS)
def remap_vector(s,p,sl):fields.remap(s,p,sl,vector=True,initializing=True)


@rule('initializers','Remap Noise to Scalar','output field','output minimum','output maximum',
      'time noise coordinate scale','spatial noise coordinate scale','time coordinate offset',
      'spatial coordinate offset','absolute value','invert absolute value')
def remap_noise(s,p,sl):
    from .particlenoise import noise4
    target=int(p.get('output field',3))
    if target not in fields.SCALAR_WRITE:return
    coords=np.zeros((len(s.age[sl]),4))
    coords[:,:3]=(s.position[sl]+np.asarray(p.get('spatial coordinate offset',(0,0,0))))*float(p.get('spatial noise coordinate scale',.01))
    coords[:,3]=(s.time+float(p.get('time coordinate offset',0)))*float(p.get('time noise coordinate scale',1))
    value=noise4(coords,s.seed,channels=1)[:,0]
    if p.get('absolute value',False):
        value=np.abs(value*2-1)
        if p.get('invert absolute value',False):value=1-value
    lo=float(p.get('output minimum',0));hi=float(p.get('output maximum',1))
    value=lo+(hi-lo)*value
    if target in fields.ANGLE_FIELDS:value=np.deg2rad(value)
    fields.write(s,target,value,sl)


@rule('initializers','Trail Length Random','length_min','length_max','length_random_exponent')
def trail_length(s,p,sl):
    lo=float(p.get('length_min',.1));hi=float(p.get('length_max',.1))
    exponent=max(.001,float(p.get('length_random_exponent',1)))
    s.trail[sl]=np.maximum(0,lo+(hi-lo)*s.rng.random(len(s.age[sl]))**exponent)


@rule('initializers','Lifetime From Sequence','Frames Per Second')
def sequence_lifetime(s,p,sl):
    fps=float(p.get('Frames Per Second',30))
    if not math.isfinite(fps) or fps<=0:
        s.warnings.add('Lifetime From Sequence: positive FPS required');return
    values=s.life[sl].copy()
    for ident in np.unique(s.sequence[sl]):
        sequence=s.sequences.get(int(ident))
        if sequence is None:
            s.warnings.add(f'Lifetime From Sequence: missing sheet sequence {ident}');continue
        values[s.sequence[sl]==ident]=len(sequence.durations)/fps
    s.life[sl]=values


@rule('initializers','Rotation Speed Random','rotation_speed_constant','rotation_speed_random_min',
      'rotation_speed_random_max','rotation_speed_random_exponent','randomly_flip_direction')
def rotation_speed(s,p,sl):
    lo=float(p.get('rotation_speed_random_min',0));hi=float(p.get('rotation_speed_random_max',0))
    value=float(p.get('rotation_speed_constant',0))+lo+(hi-lo)*s.rng.random(len(s.age[sl]))**max(.001,float(p.get('rotation_speed_random_exponent',1)))
    if p.get('randomly_flip_direction',False):value*=s.rng.choice((-1,1),len(value))
    s.rotation_speed[sl]=np.deg2rad(value)


@rule('operators','Rotation Basic')
def rotation_basic(s,p,dt):s.rotation+=s.rotation_speed*dt


@rule('renderers','render_sprite_trail','animation rate','min length','max length','length fade in time','constrain radius to length')
def sprite_trail(s,p,dt):pass


@rule('operators','Movement Lock to Control Point','control_point_number','lock rotation',
      'start_fadeout_min','start_fadeout_max','start_fadeout_exponent',
      'end_fadeout_min','end_fadeout_max','end_fadeout_exponent')
def lock_control_point(s,p,dt):control.lock(s,p,dt)


@rule('initializers','Velocity Inherit from Control Point','control point number','velocity scale')
def inherit_cp_velocity(s,p,sl):
    s.velocity[sl]+=s.control_points.velocity(int(p.get('control point number',0)),s.step_dt,s.warnings)*float(p.get('velocity scale',1))


@rule('initializers','Velocity Set from Control Point','control point number','velocity scale')
def set_cp_velocity(s,p,sl):
    cp=control.point(s,int(p.get('control point number',0)))
    if cp is not None:s.velocity[sl]=cp[:3,3]*float(p.get('velocity scale',1))


@rule('initializers','Position From Parent Particles','Inherited Velocity Scale','Random Parent Particle Distribution')
def from_parent(s,p,sl):control.parent_position(s,p,sl)


@rule('operators','Set Control Point Positions','Control Point to offset positions from',
      *[f'{label} Control Point {suffix}' for label in ('First','Second','Third','Fourth') for suffix in ('Number','Location')])
def set_cp_positions(s,p,dt):control.set_positions(s,p)


@rule('operators',"Set Control Point To Particles' Center",'Control Point Number to Set','Center Offset')
def center_cp(s,p,dt):
    if not len(s.age):return
    index=int(p.get('Control Point Number to Set',1))
    if not 0<=index<64:s.warnings.add(f'Invalid control point {index}');return
    value=np.eye(4);value[:3,3]=np.mean(s.position,axis=0)+np.asarray(p.get('Center Offset',(0,0,0)))
    s.control_points.set(index,value)


@rule('operators','Set child control points from particle positions','# of control points to set',
      'First control point to set','first particle to copy')
def child_points(s,p,dt):
    first=int(p.get('First control point to set',0));start=max(0,int(p.get('first particle to copy',0)))
    count=max(0,min(int(p.get('# of control points to set',1)),64-first,len(s.age)-start))
    if first<0:return
    for child in s.children:
        for i in range(count):
            value=np.eye(4);value[:3,3]=s.position[start+i]
            child.incoming_points[first+i]=value


@rule('forces','Pull towards control point','control point number','amount of force','falloff power')
def pull_cp(s,p,dt):
    cp=control.point(s,int(p.get('control point number',0)))
    if cp is None:return np.zeros_like(s.velocity)
    delta=cp[:3,3]-s.position;distance=np.linalg.norm(delta,axis=1)
    # Independent inverse-power falloff with a bounded denominator at the CP.
    power=np.clip(float(p.get('falloff power',0)),-8,8)
    magnitude=float(p.get('amount of force',0))*np.maximum(distance,1)**(-power)
    return delta/np.maximum(distance[:,None],1e-9)*magnitude[:,None]


@rule('initializers','Position Along Path Sequential','start control point number','end control point number',
      'particles to map from start to end','restart behavior (0 = bounce, 1 = loop )','maximum distance')
def along_path(s,p,sl):
    start=control.point(s,int(p.get('start control point number',0)))
    end=control.point(s,int(p.get('end control point number',1)))
    if start is None or end is None:return
    count=max(2,int(float(p.get('particles to map from start to end',100))))
    ids=s.ids[sl]
    if p.get('restart behavior (0 = bounce, 1 = loop )',False):t=(ids%count)/(count-1)
    else:
        phase=(ids%(2*(count-1)))/(count-1)
        t=np.where(phase<=1,phase,2-phase)
    s.position[sl]=start[:3,3]*(1-t[:,None])+end[:3,3]*t[:,None]
    distance=max(0,float(p.get('maximum distance',0)))
    if distance:
        offset=s.rng.normal(size=(len(t),3));offset/=np.maximum(np.linalg.norm(offset,axis=1,keepdims=True),1e-9)
        s.position[sl]+=offset*(distance*np.cbrt(s.rng.random(len(t))))[:,None]


@rule('initializers','Sequence Two Random','sequence_min','sequence_max')
def sequence_two(s,p,sl):
    lo,hi=sorted((int(p.get('sequence_min',0)),int(p.get('sequence_max',0))))
    s.sequence2[sl]=s.rng.integers(lo,hi+1,len(s.age[sl]))
    s.warnings.add('Secondary sequence stored; dual-sequence material blending is unsupported')


@rule('renderers','render_rope','subdivision_count','texel_size','texture_scroll_rate')
def rope_renderer(s,p,dt):pass
