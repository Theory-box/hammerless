"""Independent analytic sine-rate integration for the approximate preview.

Use cumulative offsets for attributes reset each tick, and step differences for
persistent position/rotation. Interactions can differ from the game engine.
"""
import numpy as np

PARAMETERS = ('oscillation field','oscillation rate min','oscillation rate max',
    'oscillation frequency min','oscillation frequency max','proportional 0/1',
    'start/end proportional','start time min','start time max','end time min',
    'end time max','oscillation multiplier','oscillation start phase')


def offsets(sim, spec, dt, channels):
    def sample(name, default, stream, count=1):
        low=np.broadcast_to(spec.get(name+' min',default),(count,))
        high=np.broadcast_to(spec.get(name+' max',default),(count,))
        random=np.column_stack([sim.stable_random(stream+i) for i in range(count)])
        return low+(high-low)*random
    rate=sample('oscillation rate',0,10,channels)
    frequency=sample('oscillation frequency',1,20,channels)
    start=sample('start time',0,30)
    end=sample('end time',1,31)
    if spec.get('start/end proportional',True):
        start=start*sim.life[:,None];end=end*sim.life[:,None]
    start=np.maximum(start,0);end=np.maximum(end,start)
    omega=np.pi*float(spec.get('oscillation multiplier',2))*frequency
    if spec.get('proportional 0/1',True):
        omega=omega/np.maximum(sim.life[:,None],1e-9)
    phase=np.pi*float(spec.get('oscillation start phase',.5))
    def integral(age):
        upper=np.clip(age[:,None],start,end)
        span=upper-start
        midpoint=(upper+start)/2
        # sinc handles zero frequency and avoids cancellation near zero.
        return rate*span*np.sin(omega*midpoint+phase)*np.sinc(omega*span/(2*np.pi))
    current=integral(sim.age)
    return current,current-integral(np.maximum(0,sim.age-dt))
