"""Inspect compiled lightmap bases for rotated/painted reference brush faces."""
from pathlib import Path
import sys,json
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.core.lightbake import read_faces
from hammerless.core.previewgrid import lightmap_vectors
from hammerless.core.vmf import parse
import re
folder=ROOT.parents[1]/'outputs/l4d2-preview/grid-probe-reference'
world=next(b for b in parse((folder/'room.vmf').read_text()) if b.name=='world')
predictions=[]
for solid in world.blocks('solid'):
    for side in solid.blocks('side'):
        axes=[]
        for key in ('uaxis','vaxis'):
            numbers=list(map(float,re.findall(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?',side.get(key))))
            axes.append((numbers[:3],numbers[3],numbers[4]))
        predictions.append(lightmap_vectors(axes,float(side.get('lightmapscale'))))
faces,_=read_faces((folder/'room.bsp').read_bytes());rows=[]
for f in faces:
    if not (.1<abs(f.normal[0])<.99 and .1<abs(f.normal[2])<.99):continue
    assert any(np.allclose(f.lvecs,p,atol=1e-7) for p in predictions), f.lvecs
    o=f.to_world(np.array([0.,0.]));u=f.to_world(np.array([1.,0.]))-o;v=f.to_world(np.array([0.,1.]))-o
    # Invert the affine grid by intersecting its two coordinate planes with the face plane.
    matrix=np.vstack((f.lvecs[:,:3],f.normal))
    rhs=np.r_[np.asarray(f.mins)-f.lvecs[:,3],np.dot(f.positions[0],f.normal)]
    origin=np.linalg.solve(matrix,rhs)
    basis=np.linalg.inv(matrix)[:,:2]
    assert np.allclose(origin,o,atol=.001),(origin,o)
    assert np.allclose(basis,np.stack((u,v),axis=1),atol=.001)
    coords=np.array([[.37,.82],[1.23,2.71],[-.5,3.5]])
    world=origin+coords@basis.T
    assert np.allclose(f.to_luxel(world),coords,atol=.0001)
    rows.append({'face':f.index,'normal':f.normal.tolist(),'lightmap_vectors':f.lvecs.tolist(),
                 'world_step_lengths':[float(np.linalg.norm(u)),float(np.linalg.norm(v))],
                 'step_cosine':float(np.dot(u,v)/np.linalg.norm(u)/np.linalg.norm(v)),
                 'origin_error':float(np.max(abs(origin-o)))})
assert len(rows)>=8, len(rows)
scales={round(1/np.linalg.norm(np.array(r['lightmap_vectors'])[0,:3])) for r in rows}
assert {8,16}<=scales, scales
result={'ok':True,'vmf_grid_prediction_verified':True,'faces':rows,'scope':'Compiled slanted brushes with world and explicit texture axes. Affine plane inverse matches BakeFace mapping. VMF-derived vectors match compiled vectors for these fixtures. Material scale overrides, entity origins, and boundary filtering remain unverified.'}
(folder/'grid-probe.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))

