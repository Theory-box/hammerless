"""Live brush cache coordinator. CPU preparation in update; GPU work in draw.

All scene geometry stays in the ray acceleration structure. Only successful
brush charts are omitted from per-pixel raster lighting. Edits conservatively
invalidate all irradiance; camera movement does not.
"""
from collections import Counter
from dataclasses import replace
import numpy as np
import bpy
from .previewsurfacecharts import CachedObjectCharts,ChartSnapshot,chart_material_path
from .extract import effective_role
from . import vmfimport
from .previewsource import SourceRevision
from ..core.surfacecharts import chart_triangle_mask


def matched_charts(charts,geometry,matrix,units,default_material):
    """Keep original raster triangles; never draw synthetic export-only surfaces."""
    m=np.asarray(matrix,dtype=np.float64)
    normal_matrix=np.linalg.inv(m[:3,:3]).T
    triangles=((geometry.positions@m[:3,:3].T+m[:3,3])*units).reshape(-1,3,3)
    normals=(geometry.face_normals if geometry.face_normals is not None else geometry.normals).reshape(-1,3,3)[:,0]@normal_matrix.T
    default_material=default_material.lower().replace('\\','/')
    paths=[chart_material_path(mat,default_material) for mat in geometry.materials]
    ids=geometry.material_ids
    triangle_paths=np.array([paths[i] if 0<=i<len(paths) else default_material for i in ids])
    masks=[];owners=np.zeros(len(triangles),dtype=np.int32)
    for chart in charts:
        mask=chart_triangle_mask(chart,triangles,normals)&(triangle_paths==chart.material_path)
        masks.append(mask);owners+=mask
    accepted=[];covered=np.zeros(len(triangles),dtype=bool)
    for chart,mask in zip(charts,masks):
        mask &= owners==1
        if not mask.any():continue
        # Display exactly the matched mesh triangles, even if the export face
        # extends beyond them. Unmatched or ambiguous triangles stay untouched.
        accepted.append(replace(chart,positions=triangles[mask].reshape(-1,3).astype(np.float32)))
        covered |= mask
    return accepted,covered


def remaining_geometry(original,keep):
    from .l4d2preview import Geometry
    vertices=np.repeat(keep,3)
    result=Geometry(original.positions[vertices],original.normals[vertices])
    for name in ('uvs','face_normals','painted'):
        value=getattr(original,name)
        setattr(result,name,value[vertices] if value is not None else None)
    result.material_ids=original.material_ids[keep]
    result.materials=original.materials;result.use_uv=original.use_uv
    return result


class LiveSurfaceCache:
    SAMPLE_BUDGET=1_000_000

    def __init__(self):
        self.layouts=CachedObjectCharts();self.document_ids={};self.source_revision=SourceRevision()
        self.signature=None;self.generation=0
        self.geometry_refs={}
        self.object_data={};self.display_tokens={};self.object_generation=0
        self.previous_cache=None;self.previous_display=None
        self.faces=[];self.charts=[];self.bindings={};self.keys=set();self.remaining={}
        self.warnings=[];self.budget_warnings=[];self.match_warnings=[];self.cache=None;self.display=None

    def update(self,context,depsgraph,scene,material_revision):
        settings=context.scene.hammerless
        if not vmfimport.imported(context.scene):self.document_ids.clear()
        text=bpy.data.texts.get(context.scene.get(vmfimport.SOURCE_KEY,''))
        source=self.source_revision.update(text.as_string() if text else None)
        counts=Counter(key for key,_ in scene.instances)
        candidates=[]
        for instance in depsgraph.object_instances:
            obj=instance.object.original
            key=obj.as_pointer()
            if (key not in scene.geometry or counts[key]!=1 or instance.is_instance or
                obj.type!='MESH' or effective_role(obj,getattr(scene,'role_lookup',None)) in ('MODEL','TERRAIN') or obj.data.get('hl_model') or
                obj.hammerless.use_convex_hull or
                obj.get(vmfimport.KIND)=='disp'):
                continue
            # Extraction currently uses the original object's world transform.
            if instance.matrix_world!=obj.matrix_world:continue
            candidates.append(obj)
        snapshot=None
        def get_snapshot():
            nonlocal snapshot
            if snapshot is None:snapshot=ChartSnapshot(context,self.document_ids)
            return snapshot
        prepared=[];warnings=[];material_signatures={}
        for obj in candidates:
            key=obj.as_pointer()
            try:
                charts=self.layouts.get(context,obj,depsgraph,id(scene.geometry[key]),source,get_snapshot,allow_partial=True,material_signatures=material_signatures)
                rejected=self.layouts.warnings.get(key,[])
                if rejected:warnings.append(f'{obj.name}: {len(rejected)} face(s) retain per-pixel lighting ({rejected[0]})')
                prepared.append((key,charts))
            except (ValueError,RuntimeError) as exc:
                warnings.append(f'{obj.name}: per-pixel lighting ({exc})')
        self.layouts.retain(candidates)
        # Hold prior geometry references until after comparing tokens, so Python
        # cannot recycle an old id into an apparently unchanged layout.
        self.geometry_refs=dict(scene.geometry)
        signature=(tuple((key,id(charts)) for key,charts in prepared),
                   settings.units_per_meter,material_revision,id(scene.surface_materials))
        if signature==self.signature:
            self.warnings=warnings+self.budget_warnings+self.match_warnings
            return
        self.budget_warnings=[]
        self.match_warnings=[]
        faces=[];metadata=[];bindings={};keys=set();remaining={};count=0
        object_data={};sample_arrays=[];normal_arrays=[];bound_arrays=[];display_tokens={}
        resolver=scene.surface_materials
        matrices=dict(scene.instances)
        for key,source_charts in prepared:
            geometry=scene.geometry[key]
            token=(id(source_charts),id(geometry),tuple(v for row in matrices[key] for v in row),
                   settings.units_per_meter,settings.default_material,material_revision,id(resolver))
            entry=self.object_data.get(key)
            if entry is None or entry['token']!=token:
                try:
                    charts,covered=matched_charts(source_charts,geometry,matrices[key],settings.units_per_meter,settings.default_material)
                except ValueError:
                    self.match_warnings.append('Noninvertible brush transform retains per-pixel lighting')
                    continue
                # Keep source objects alive alongside their identity tokens.
                self.object_generation+=1
                entry={'token':token,'source':source_charts,'geometry':geometry,'charts':charts,
                       'covered':covered,'size':sum(c.w*c.h for c in charts),'samples':None,
                       'generation':self.object_generation}
            object_data[key]=entry
            charts=entry['charts'];covered=entry['covered']
            if count+entry['size']>self.SAMPLE_BUDGET:
                self.budget_warnings=['Surface cache sample budget reached; remaining brushes use per-pixel lighting']
                # Budget membership may change as earlier brushes resize. Keep
                # matching reusable, but not old luxel arrays outside the budget.
                entry['samples']=None
                for name in ('normals','bounds','bindings','remaining'):entry.pop(name,None)
                continue
            if not charts:continue
            if entry['samples'] is None:
                entry['bindings']=[]
                for chart in charts:
                    mat=self.layouts.bindings[key].get(chart.material_path)
                    if mat is not None:
                        image,dimensions=resolver.resolve(mat)
                        unlit=resolver.unlit.get(mat.as_pointer(),False)
                    else:
                        image,dimensions=resolver.resolve_path(chart.material_path)
                        unlit=resolver.path_unlit.get(chart.material_path,False)
                    entry['bindings'].append((image,chart.texture_uv(dimensions),unlit))
                units=settings.units_per_meter
                entry['samples']=np.concatenate([c.samples() for c in charts])/units
                entry['normals']=np.concatenate([np.tile(c.normal,(c.w*c.h,1)) for c in charts])
                corners=[]
                for chart in charts:
                    low=chart.positions.min(axis=0)/units;high=chart.positions.max(axis=0)/units
                    corners.append([(x,y,z,1) for x in (low[0],high[0]) for y in (low[1],high[1]) for z in (low[2],high[2])])
                entry['bounds']=np.asarray(corners,dtype=np.float64).reshape(-1,8,4)
                entry['remaining']=remaining_geometry(geometry,~covered) if not covered.all() else None
            for local,chart in enumerate(charts):
                face=replace(chart,index=len(faces))
                metadata.append({'face':face.index,'start':count,'width':face.w,'height':face.h})
                count+=face.w*face.h
                bindings[face.index]=entry['bindings'][local]
                display_tokens[face.index]=(entry['generation'],local)
                faces.append(face)
            sample_arrays.append(entry['samples']);normal_arrays.append(entry['normals']);bound_arrays.append(entry['bounds'])
            if covered.all():keys.add(key)
            else:remaining[key]=entry['remaining']
        self.object_data=object_data;self.display_tokens=display_tokens
        self.faces=faces;self.charts=metadata;self.bindings=bindings;self.keys=keys;self.remaining=remaining
        self.warnings=warnings+self.budget_warnings+self.match_warnings;self.signature=signature;self.generation+=1
        # Retain only the last GPU generation until draw can reuse its shaders,
        # atlas allocations and unchanged material batches. Never show it stale.
        if self.cache is not None:self.previous_cache=self.cache
        if self.display is not None:self.previous_display=self.display
        self.cache=None;self.display=None
        if not faces:self.previous_cache=None;self.previous_display=None
        self.units=settings.units_per_meter
        self.positions=np.concatenate(sample_arrays) if faces else None
        self.normals=np.concatenate(normal_arrays) if faces else None
        self.bounds=np.concatenate(bound_arrays) if faces else np.empty((0,8,4),dtype=np.float64)

    def refine(self,scene,signature,*,frames,rays,bounces,view_projection=None):
        if not self.faces:return False
        from .previewlightcache import SurfaceLightCache,SurfaceCacheDisplay
        if self.cache is None:
            self.cache=SurfaceLightCache(self.positions,self.normals,sample_counts=[f.w*f.h for f in self.faces],reuse=self.previous_cache)
            self.display=SurfaceCacheDisplay(self.cache,self.faces,self.charts,
                lambda face:self.bindings[face.index],units_per_meter=self.units,
                reuse=self.previous_display,face_tokens=self.display_tokens)
            self.previous_cache=None;self.previous_display=None
        from .previewcull import visible_bounds
        visible=np.flatnonzero(visible_bounds(self.bounds,view_projection)) if view_projection is not None else None
        self.visible_charts=len(visible) if visible is not None else len(self.faces)
        return self.cache.refine(scene,(signature,self.generation),max_frames=frames,rays=rays,bounces=bounces,visible_groups=visible)

    def draw(self,matrix,exposure):
        if self.display:self.display.draw(matrix,exposure)

    def draw_depth(self,matrix):
        if self.display:self.display.draw_depth(matrix)
