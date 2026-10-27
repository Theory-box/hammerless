"""Independent smooth value noise for previews; not Valve's noise algorithm.

Four coordinates permit spatial and birth-time variation without frame-dependent
random draws. Integer lattice hashing and smooth interpolation need only NumPy.
"""
import itertools
import numpy as np


def noise4(coordinates, seed=0, channels=3):
    coordinates=np.asarray(coordinates,dtype=float)
    if coordinates.ndim!=2 or coordinates.shape[1]!=4 or not np.isfinite(coordinates).all():
        raise ValueError('noise requires finite Nx4 coordinates')
    if not 1<=channels<=4:
        raise ValueError('noise supports one to four channels')
    floor=np.floor(coordinates)
    fraction=coordinates-floor
    weight=fraction*fraction*fraction*(fraction*(fraction*6-15)+10)
    # Wrap before integer conversion so large authored offsets stay bounded.
    lattice=np.remainder(floor,4294967296).astype(np.uint64)
    result=np.zeros((len(coordinates),channels))
    salts=(np.arange(channels,dtype=np.uint64)+1)*np.uint64(0x9e3779b9)
    mask=np.uint64(0xffffffff)
    primes=np.array([0x8da6b343,0xd8163841,0xcb1ab31f,0x165667b1],dtype=np.uint64)
    for corner in itertools.product((0,1),repeat=4):
        corner=np.asarray(corner,dtype=np.uint64)
        h=np.bitwise_xor.reduce(((lattice+corner)*primes)&mask,axis=1)[:,None]
        h=(h ^ salts ^ np.uint64(int(seed)&0xffffffff))&mask
        h=((h ^ (h>>16))*np.uint64(0x45d9f3b))&mask
        h=((h ^ (h>>16))*np.uint64(0x45d9f3b))&mask
        values=(h ^ (h>>16)).astype(float)/4294967295
        blend=np.prod(np.where(corner,weight,1-weight),axis=1)
        result+=blend[:,None]*values
    return np.clip(result,0,1)
