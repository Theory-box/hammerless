"""Inspect stored BSP shadow samples separately from game display filtering."""
from pathlib import Path
import json,sys
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.core.lightbake import read_faces
from hammerless.core.lightmap import decode_luxels
from hammerless.core.bspdecompile import Bsp
from hammerless.core.previewlighting import PointLight

folder=ROOT.parents[1]/'outputs/l4d2-preview/playable-shadow-reference'
data=(folder/'room.bsp').read_bytes()
faces,lump=read_faces(data);raw=Bsp(data).lump(lump)
light=PointLight.from_keys({'_light':'255 180 90 200','_quadratic_attn':'1','_linear_attn':'0','_constant_attn':'0'})
def direct(position):
    receiver=np.asarray(position)+np.array([0,0,1])
    delta=np.array([-64,0,128])-receiver
    distance=np.linalg.norm(delta)
    value=light.intensity[0]*delta[2]/distance**3/255
    enter,leave=0.,1.
    for axis,lo,hi in ((0,0,16),(1,-16,16),(2,24,40)):
        if abs(delta[axis])<1e-9:
            if receiver[axis]<lo or receiver[axis]>hi:return value
            continue
        a=(lo-receiver[axis])/delta[axis];b=(hi-receiver[axis])/delta[axis]
        enter=max(enter,min(a,b));leave=min(leave,max(a,b))
    return 0. if enter<=leave else value

rows=[]
surface_rows=[]
for face in faces:
    if face.normal[2]<.99 or np.max(abs(face.positions[:,2]))>.01:continue
    values=decode_luxels(raw[face.light_ofs:face.light_ofs+4*face.w*face.h]).reshape(face.h,face.w,3)
    for iy in range(face.h):
        for ix in range(face.w):
            p=face.to_world(np.array([ix,iy]))
            if 0<=p[0]<=80 and abs(p[1])<=48:
                predicted=float(np.mean([direct(p+np.array([dx,dy,0])) for dx in (-2,2) for dy in (-2,2)]))
                surface_rows.append({'position':p.tolist(),'stored':float(values[iy,ix,0]),'predicted':predicted})
            if abs(p[1])>.01 or not 0<=p[0]<=80:continue
            receiver=p+np.array([0,0,1])
            delta=np.array([-64,0,128])-receiver
            distance=np.linalg.norm(delta)
            unoccluded=light.intensity[0]*delta[2]/distance**3/255
            # Analytic slab intersection for this fixture's rectangular blocker.
            enter,leave=0.,1.
            for axis,lo,hi in ((0,0,16),(2,24,40)):
                a=(lo-receiver[axis])/delta[axis];b=(hi-receiver[axis])/delta[axis]
                enter=max(enter,min(a,b));leave=min(leave,max(a,b))
            blocked=enter<=leave
            red=float(values[iy,ix,0])
            rows.append({'x':float(p[0]),'stored_red':red,'unoccluded_red':float(unoccluded),
                'stored_fraction':float(red/unoccluded),'analytic_blocked':bool(blocked),
                'four_cell_reconstruction':float(np.mean([direct(p+np.array([dx,dy,0])) for dx in (-2,2) for dy in (-2,2)]))})
rows.sort(key=lambda r:r['x'])
partial=[r for r in rows if .05<r['stored_fraction']<.95]
assert rows and partial,'Fixture did not expose partially lit stored edge samples'
errors=[abs(r['stored_red']-r['four_cell_reconstruction']) for r in rows]
assert max(errors)<1/255, 'Regular-cell reconstruction diverged beyond one normalized 8-bit step'
assert len(rows)>=20, 'Insufficient edge samples'
# Shared BSP face boundaries may repeat the same world-space sample.
unique_positions=len({tuple(r['position']) for r in surface_rows})
surface_errors=np.array([abs(r['stored']-r['predicted']) for r in surface_rows])
worst=surface_rows[int(np.argmax(surface_errors))]
assert surface_errors.max()<1/255, 'Two-dimensional regular floor filter diverged'
assert unique_positions>=400, 'Insufficient two-dimensional coverage'
surface_check={'unique_positions':unique_positions,'sample_count':len(surface_rows),'max_error':float(surface_errors.max()),
    'mean_error':float(surface_errors.mean()),'worst':worst,
    'over_one_step':int(np.count_nonzero(surface_errors>1/255))}
# Compare the continuous profile after the same linear interpolation of samples.
# This is a lighting-only diagnostic; it does not simulate game texture/postprocessing.
profile={r['x']:r for r in rows}
x=np.array(sorted(profile))
stored=np.array([profile[v]['stored_red'] for v in x])
reconstructed=np.array([profile[v]['four_cell_reconstruction'] for v in x])
query=np.linspace(x.min(),x.max(),1601)
reference=np.interp(query,x,stored)
filtered=np.interp(query,x,reconstructed)
hard=np.array([direct((v,0,0)) for v in query])
profile_check={'sample_count':len(query),
    'hard_rms':float(np.sqrt(np.mean((hard-reference)**2))),
    'filtered_rms':float(np.sqrt(np.mean((filtered-reference)**2))),
    'scope':'One horizontal planar-floor profile; BSP samples interpolated linearly, no game postprocessing.'}
assert profile_check['filtered_rms']<profile_check['hard_rms']*.1
# Standalone vector artifact, with exact axes and a legend, for inspection.
def line(values,color):
    points=' '.join(f'{60+v*10:.2f},{330-y*800:.2f}' for v,y in zip(query,values))
    return f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'
svg='<svg xmlns="http://www.w3.org/2000/svg" width="920" height="410" viewBox="0 0 920 410"><rect width="920" height="410" fill="white"/>'
svg+='<text x="60" y="25" font-family="sans-serif" font-size="18">Floor shadow profile - linear red irradiance</text>'
for v in range(0,81,20):
    svg+=f'<path d="M {60+v*10} 50 V 330" stroke="#ddd"/><text x="{55+v*10}" y="350">{v}</text>'
for v in (0,.1,.2,.3):
    svg+=f'<path d="M 60 {330-v*800} H 860" stroke="#ddd"/><text x="20" y="{334-v*800}">{v}</text>'
svg+=line(hard,'#e07030')+line(reference,'#222')+line(filtered,'#1688cc')
svg+='<text x="60" y="380" fill="#e07030">Hard rays</text><text x="220" y="380" fill="#222">BSP interpolated</text><text x="420" y="380" fill="#1688cc">Reconstructed filter</text><text x="700" y="380">X (Source units)</text></svg>'
(folder/'shadow-filter-profile.svg').write_text(svg,encoding='utf-8')
result={'profile_check':profile_check,'surface_check':surface_check,'four_cell_max_absolute_error':max(errors),'four_cell_mean_absolute_error':float(np.mean(errors)),'rows':rows,'partial_stored_samples':partial,
    'finding':'Intermediate shadow values already exist in the BSP before game sampling or postprocessing.',
    'scope':'Single noextra direct-only fixture, four-unit regular floor grid. Four equal-area cell-center samples reproduce the interior radial filter here. Does not validate clipped cells, smoothing neighbors, displacements, or GI filtering.'}
(folder/'shadow-edge-samples.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'profile_check':profile_check,'surface_check':surface_check,'partial_stored_samples':partial,'sample_count':len(rows),'four_cell_max_error':max(errors),'four_cell_mean_error':float(np.mean(errors))},indent=2))
