"""Independent PCF preview runtime. No Valve binaries or third-party simulator code.

Support is deliberately conservative: implemented rules are preview approximations,
not a claim of Source parity. Unknown rules and parameters remain in the audit.
"""
from dataclasses import dataclass, field
import math
import struct
import numpy as np
from .particles import read_dmx

CATEGORIES = ('emitters', 'initializers', 'operators', 'forces', 'constraints', 'renderers')
RULES = {}


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
        result[key] = {'missing_rules': sorted(set(missing)), 'unmapped_parameters': sorted(set(parameters)),
                       'children': [c for c, _ in definition.children], 'status': 'approximate'}
    def collect(key, path):
        if key in path:
            return {'child cycle: ' + key}
        if key not in result:
            return {'missing child: ' + key}
        r = result[key]
        gaps = set(r['missing_rules']) | set(r['unmapped_parameters'])
        for child in r['children']:
            gaps |= collect(child, path | {key})
        return gaps
    for key in result:
        result[key]['recursive_gaps'] = sorted(collect(key, set()))
    return result


class Simulation:
    """Fixed-step, bounded, seeded array simulation of one definition in Source units."""
    def __init__(self, definition, seed=0, limit=2048):
        self.definition = definition
        self.rng = np.random.default_rng(seed)
        self.limit = max(0, min(limit, int(definition.attrs.get('max_particles', limit))))
        self.time = 0.0
        self.accumulator = 0.0
        self.emission = {}
        self.seed = seed
        self.next_id = 0
        self.ids = np.zeros(0,dtype=np.uint64)
        self.rotation = np.zeros(0)
        self.sequence = np.zeros(0,dtype=np.int32)
        self.operator_index = 0
        self.position = np.zeros((0, 3))
        self.velocity = np.zeros((0, 3))
        self.color = np.zeros((0, 4))
        self.initial_color = np.zeros((0, 4))
        self.radius = np.zeros(0)
        self.initial_radius = np.zeros(0)
        self.age = np.zeros(0)
        self.life = np.zeros(0)

    def spawn(self, count):
        count = max(0, min(int(count), self.limit-len(self.age)))
        if not count:
            return
        start = len(self.age)
        self.ids = np.concatenate((self.ids,np.arange(self.next_id,self.next_id+count,dtype=np.uint64)))
        self.next_id += count
        self.rotation = np.concatenate((self.rotation,np.full(count,math.radians(float(self.definition.attrs.get('rotation',0))))))
        self.sequence = np.concatenate((self.sequence,np.full(count,int(self.definition.attrs.get('sequence_number',0)),dtype=np.int32)))
        attrs = self.definition.attrs
        for name, values in [('position', np.zeros((count, 3))), ('velocity', np.zeros((count, 3))),
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

    def advance(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('advance needs finite nonnegative time')
        self.accumulator += seconds
        dt = 1 / 60
        while self.accumulator + 1e-10 >= dt:
            self.accumulator -= dt
            for i, spec in enumerate(self.definition.rules['emitters']):
                entry = RULES.get(('emitters', spec.get('functionName', '').casefold()))
                if entry:
                    entry[0](self, spec, (i, dt))
            self.age += dt
            self.color = self.initial_color.copy()
            self.radius = self.initial_radius.copy()
            for self.operator_index, spec in enumerate(self.definition.rules['operators']):
                entry = RULES.get(('operators', spec.get('functionName', '').casefold()))
                if entry:
                    entry[0](self, spec, dt)
            self.time += dt

    def retain(self, mask):
        for name in ('position','velocity','color','initial_color','radius','initial_radius','age','life','ids','rotation','sequence'):
            setattr(self, name, getattr(self, name)[mask])

    def stable_random(self):
        """Stable per-particle/operator samples, unaffected by particle death or frame rate."""
        x=(self.ids+np.uint64((self.seed+self.operator_index*65537)&0xffffffff)) & np.uint64(0xffffffff)
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


def random_scalar(target, lo, hi, default, scale=1):
    def apply(s, p, sl):
        a, b = float(p.get(lo, default)), float(p.get(hi, default))
        getattr(s, target)[sl] = s.rng.uniform(min(a,b), max(a,b), len(s.age[sl]))*scale
    return apply


for name, target, lo, hi, default in [('Lifetime Random','life','lifetime_min','lifetime_max',1),
                                    ('Radius Random','radius','radius_min','radius_max',5)]:
    rule('initializers', name, lo, hi)(random_scalar(target,lo,hi,default))


@rule('initializers', 'Color Random', 'color1', 'color2')
def color(s, p, sl):
    a=np.asarray(p.get('color1',(255,255,255,255)))[:3]/255
    b=np.asarray(p.get('color2',(255,255,255,255)))[:3]/255
    s.color[sl,:3] = a+(b-a)*s.rng.random((len(s.age[sl]),1))


@rule('initializers', 'Alpha Random', 'alpha_min', 'alpha_max')
def alpha(s,p,sl):
    a,b=float(p.get('alpha_min',255)),float(p.get('alpha_max',255))
    s.color[sl,3]=s.rng.uniform(min(a,b),max(a,b),len(s.age[sl]))/255


@rule('initializers', 'Position Within Sphere Random', 'distance_min', 'distance_max', 'speed_min', 'speed_max')
def sphere(s,p,sl):
    count=len(s.age[sl]);direction=s.rng.normal(size=(count,3))
    direction/=np.maximum(np.linalg.norm(direction,axis=1,keepdims=True),1e-12)
    lo,hi=sorted((max(0,float(p.get('distance_min',0))),max(0,float(p.get('distance_max',0)))))
    radius=np.cbrt(s.rng.uniform(lo**3,hi**3,count))
    s.position[sl]+=direction*radius[:,None]
    lo,hi=sorted((float(p.get('speed_min',0)),float(p.get('speed_max',0))))
    s.velocity[sl]+=direction*s.rng.uniform(lo,hi,count)[:,None]


@rule('initializers', 'Position Within Box Random', 'min', 'max')
def box(s,p,sl):
    a=np.array(p.get('min',(0,0,0)));b=np.array(p.get('max',(0,0,0)))
    s.position[sl]+=s.rng.uniform(np.minimum(a,b),np.maximum(a,b),(len(s.age[sl]),3))


@rule('initializers', 'Position Modify Offset Random', 'offset min', 'offset max')
def offset(s,p,sl):
    box(s,{'min':p.get('offset min',(0,0,0)),'max':p.get('offset max',(0,0,0))},sl)


@rule('operators', 'Movement Basic', 'gravity', 'drag')
def movement(s,p,dt):
    s.velocity+=np.array(p.get('gravity',(0,0,0)))*dt
    s.velocity*=max(0,1-float(p.get('drag',0)))**(dt*30)
    s.position+=s.velocity*dt


@rule('operators', 'Lifespan Decay')
def decay(s,p,dt):
    s.retain(s.age < s.life)


@rule('operators', 'Radius Scale', 'start_time', 'end_time', 'radius_start_scale', 'radius_end_scale')
def radius_scale(s,p,dt):
    start=float(p.get('start_time',0));end=float(p.get('end_time',1))
    t=np.clip((s.age/np.maximum(s.life,1e-9)-start)/max(end-start,1e-9),0,1)
    s.radius*=float(p.get('radius_start_scale',1))*(1-t)+float(p.get('radius_end_scale',1))*t


@rule('renderers', 'render_animated_sprites', 'animation rate', 'animation_fit_lifetime', 'use animation rate as FPS')
def sprites(s,p,dt):
    pass


@rule('initializers','Sequence Random','sequence_min','sequence_max')
def sequence(s,p,sl):
    lo,hi=sorted((int(p.get('sequence_min',0)),int(p.get('sequence_max',0))))
    s.sequence[sl]=s.rng.integers(lo,hi+1,len(s.age[sl]))


@rule('initializers','Rotation Random','rotation_initial','rotation_offset_min','rotation_offset_max')
def rotation(s,p,sl):
    lo,hi=sorted((float(p.get('rotation_offset_min',0)),float(p.get('rotation_offset_max',360))))
    s.rotation[sl]=np.deg2rad(float(p.get('rotation_initial',0))+s.rng.uniform(lo,hi,len(s.age[sl])))


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
        factor=smooth((end-age)/np.maximum(end-boundary,1e-9))
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
