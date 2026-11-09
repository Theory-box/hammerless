"""Live brush cache coordinator. CPU preparation in update; GPU work in draw.

All scene geometry stays in the ray acceleration structure. Only successful
brush charts are omitted from per-pixel raster lighting. Edits conservatively
invalidate all irradiance; camera movement does not.
"""
from collections import Counter
from dataclasses import replace
import hashlib
import numpy as np
import bpy
from .previewsurfacecharts import CachedObjectCharts,ChartSnapshot,chart_material_path
from .extract import effective_role
from . import vmfimport
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
        self.layouts=CachedObjectCharts()
        self.signature=None;self.generation=0
        self.geometry_refs={}
        self.faces=[];self.charts=[];self.bindings={};self.keys=set();self.remaining={}
        self.warnings=[];self.budget_warnings=[];self.match_warnings=[];self.cache=None;self.display=None

    def update(self,context,depsgraph,scene,material_revision):
        settings=context.scene.hammerless
        text=bpy.data.texts.get(context.scene.get(vmfimport.SOURCE_KEY,''))
        source=hashlib.sha256(text.as_string().encode()).digest() if text else None
        counts=Counter(key for key,_ in scene.instances)
        candidates=[]
        for instance in depsgraph.object_instances:
            obj=instance.object.original
            key=obj.as_pointer()
            if (key not in scene.geometry or counts[key]!=1 or instance.is_instance or
                obj.type!='MESH' or effective_role(obj) in ('MODEL','TERRAIN') or obj.data.get('hl_model') or
                obj.hammerless.use_convex_hull or
                obj.get(vmfimport.KIND)=='disp'):
                continue
            # Extraction currently uses the original object's world transform.
            if instance.matrix_world!=obj.matrix_world:continue
            candidates.append(obj)
        snapshot=None
        def get_snapshot():
            nonlocal snapshot
            if snapshot is None:snapshot=ChartSnapshot(context)
            return snapshot
        prepared=[];warnings=[]
        for obj in candidates:
            key=obj.as_pointer()
            try:
                charts=self.layouts.get(context,obj,depsgraph,id(scene.geometry[key]),source,get_snapshot,allow_partial=True)
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
        resolver=scene.surface_materials
        matrices=dict(scene.instances)
        for key,charts in prepared:
            try:
                charts,covered=matched_charts(charts,scene.geometry[key],matrices[key],settings.units_per_meter,settings.default_material)
            except ValueError:
                self.match_warnings.append('Noninvertible brush transform retains per-pixel lighting')
                continue
            size=sum(c.w*c.h for c in charts)
            if count+size>self.SAMPLE_BUDGET:
                self.budget_warnings=['Surface cache sample budget reached; remaining brushes use per-pixel lighting']
                continue
            object_faces=[];object_bindings={}
            for chart in charts:
                face=replace(chart,index=len(faces)+len(object_faces))
                mat=self.layouts.bindings[key].get(face.material_path)
                if mat is not None:
                    image,dimensions=resolver.resolve(mat)
                    unlit=resolver.unlit.get(mat.as_pointer(),False)
                else:
                    image,dimensions=resolver.resolve_path(face.material_path)
                    unlit=resolver.path_unlit.get(face.material_path,False)
                object_bindings[face.index]=(image,face.texture_uv(dimensions),unlit)
                object_faces.append(face)
            for face in object_faces:
                metadata.append({'face':face.index,'start':count,'width':face.w,'height':face.h})
                count+=face.w*face.h
            faces.extend(object_faces);bindings.update(object_bindings)
            if object_faces:
                if covered.all():keys.add(key)
                else:remaining[key]=remaining_geometry(scene.geometry[key],~covered)
        self.faces=faces;self.charts=metadata;self.bindings=bindings;self.keys=keys;self.remaining=remaining
        self.warnings=warnings+self.budget_warnings+self.match_warnings;self.signature=signature;self.generation+=1
        self.cache=None;self.display=None
        self.units=settings.units_per_meter
        self.positions=np.concatenate([f.samples() for f in faces])/self.units if faces else None
        self.normals=np.concatenate([np.tile(f.normal,(f.w*f.h,1)) for f in faces]) if faces else None

    def refine(self,scene,signature,*,frames,rays,bounces):
        if not self.faces:return False
        from .previewlightcache import SurfaceLightCache,SurfaceCacheDisplay
        if self.cache is None:
            self.cache=SurfaceLightCache(self.positions,self.normals)
            self.display=SurfaceCacheDisplay(self.cache,self.faces,self.charts,
                lambda face:self.bindings[face.index],units_per_meter=self.units)
        return self.cache.refine(scene,(signature,self.generation),max_frames=frames,rays=rays,bounces=bounces)

    def draw(self,matrix,exposure):
        if self.display:self.display.draw(matrix,exposure)

    def draw_depth(self,matrix):
        if self.display:self.display.draw_depth(matrix)
