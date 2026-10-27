"""Synchronised parent-first simulation of an effect tree and external CP motion."""
import math
import numpy as np
from .particlecontrol import rigid,blend
from .particlepreview import Simulation


class EffectSimulation:
    def __init__(self,library,name,asset_loader=None,limit=256,max_systems=16,control_points=None):
        self.systems=[];self.delays=[];self.warnings=set()
        self.time=0.;self.accumulator=0.
        self.inputs={0:np.eye(4)}
        for index,value in (control_points or {}).items():
            if not 0<=int(index)<64:raise ValueError('control point index must be 0..63')
            self.inputs[int(index)]=rigid(value)
        self.targets={k:v.copy() for k,v in self.inputs.items()}
        def add(key,delay,parent,path):
            if key in path:self.warnings.add('child cycle: '+key);return
            if key not in library:self.warnings.add('missing child: '+key);return
            if len(self.systems)>=max_systems:self.warnings.add('child system limit reached');return
            definition=library[key]
            sequences=asset_loader(definition) if asset_loader else {}
            sim=Simulation(definition,seed=len(self.systems),limit=limit,sequences=sequences,parent=parent)
            sim.control_points.begin(self.inputs)
            sim.control_points.previous={k:v.copy() for k,v in sim.control_points.current.items()}
            self.systems.append(sim);self.delays.append(delay)
            if parent is not None:parent.children.append(sim)
            for child,wait in definition.children:add(child,delay+max(0,wait),sim,path|{key})
        add(name.casefold(),0,None,set())

    def advance(self,seconds,control_points=None):
        if not math.isfinite(seconds) or seconds<0:raise ValueError('advance needs finite nonnegative time')
        for index,value in (control_points or {}).items():
            if not 0<=int(index)<64:raise ValueError('control point index must be 0..63')
            self.targets[int(index)]=rigid(value)
        self.accumulator+=seconds;dt=1/60
        count=int((self.accumulator+1e-10)/dt)
        if not count:return
        start={k:v.copy() for k,v in self.inputs.items()}
        for step in range(count):
            external={k:blend(start.get(k,v),v,(step+1)/count) for k,v in self.targets.items()}
            for sim,delay in zip(self.systems,self.delays):
                inherited=external if sim.parent is None else sim.parent.control_points.current
                sim.control_points.begin(inherited)
                for index,value in sim.incoming_points.items():sim.control_points.set(index,value)
                sim.incoming_points.clear()
                if self.time+dt>delay+1e-10:sim.step(dt)
            self.time+=dt;self.accumulator-=dt
        self.inputs={k:v.copy() for k,v in self.targets.items()}


    def finished(self):
        """No living particles and no supported emitter can produce more."""
        for sim,delay in zip(self.systems,self.delays):
            if len(sim.age) or self.time<=delay:return False
            for i,p in enumerate(sim.definition.rules['emitters']):
                kind=p.get('functionName','').casefold()
                start=float(p.get('emission_start_time',0))
                if kind=='emit_instantaneously':
                    if i not in sim.emission:return False
                elif kind=='emit_continuously':
                    duration=float(p.get('emission_duration',0))
                    if float(p.get('emission_rate',100))>0 and (duration<=0 or sim.time<start+duration):return False
                else:return False  # Unknown emission is not evidence of completion.
        return True
