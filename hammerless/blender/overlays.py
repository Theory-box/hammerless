"""Native brush overlays: transient projected GPU preview and a material browser."""
import json
import math
import bpy
import gpu
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty, PointerProperty, CollectionProperty
from mathutils import Vector, Matrix
from gpu_extras.batch import batch_for_shader
from ..core import geometry as g
from ..core.overlays import project, validate, stop_at_surface
from ..core.ir import Entity
from ..core.vmf import fmt_vec

_state = {"dirty": True, "batches": [], "textures": {}, "messages": {}, "faces": None}
_handle = None
_shader = None


def changed(self=None, context=None):
    _state["dirty"] = True


class HL_OverlayReceiver(bpy.types.PropertyGroup):
    object: PointerProperty(type=bpy.types.Object, update=changed)


class HL_OverlaySettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(default=False, update=changed)
    material: StringProperty(name="Material", default="decals/asphalt/asphalt1", update=changed)
    width: FloatProperty(name="Width", default=64, min=.01, description="Overlay width in Hammer units", update=changed)
    height: FloatProperty(name="Height", default=64, min=.01, description="Overlay height in Hammer units", update=changed)
    depth: FloatProperty(name="Receiver Depth", default=16, min=.01, description="Projection distance from the empty along local -Z; faces behind the emitter are excluded", update=changed)
    stop_at_surface: BoolProperty(name="Stop at Surface", default=True, description="Exclude receiving faces hidden behind nearer receivers; partially hidden faces are excluded as a whole to match game export", update=changed)
    order: IntProperty(name="Render Order", default=0, min=0, max=3, update=changed)
    repeat_u: FloatProperty(name="Repeat U", default=1, min=.01, update=changed)
    repeat_v: FloatProperty(name="Repeat V", default=1, min=.01, update=changed)
    preview: BoolProperty(name="Show Preview", default=True, update=changed)
    receivers: CollectionProperty(type=HL_OverlayReceiver)
    restrict_receivers: BoolProperty(name="Only Assigned Brushes", default=False, update=changed)


def is_overlay(obj):
    return obj is not None and hasattr(obj,"hl_overlay") and obj.hl_overlay.enabled


def entity(obj, scale, matrix=None):
    p=obj.hl_overlay; m=matrix if matrix is not None else obj.matrix_world
    axes=[tuple(m.to_3x3().col[i]) for i in range(3)]
    lengths=[g.length(a) for a in axes]
    if min(lengths)<1e-8:raise ValueError("Overlay cannot have zero scale")
    u,v,n=[g.normalize(a) for a in axes]
    # Source stores handedness separately, so mirrored U/V must not reverse the
    # receiving normal. Local +Z stays the outward projection direction.
    if abs(g.dot(g.normalize(g.cross(u,v)),n))<.9999:raise ValueError("Apply overlay shear before export")
    origin=tuple(c*scale for c in m.translation)
    w,h=p.width*lengths[0],p.height*lengths[1]
    names=[r.object.name for r in p.receivers if r.object is not None]
    if p.restrict_receivers and not names:raise ValueError("No receiver brushes assigned")
    kv={"material":p.material.strip().lower().removeprefix("materials/").removesuffix(".vmt"),
        "BasisOrigin":fmt_vec(origin),"BasisU":" ".join(map(str,u)),"BasisV":" ".join(map(str,v)),"BasisNormal":" ".join(map(str,n)),
        "StartU":"0","EndU":str(p.repeat_u),"StartV":"0","EndV":str(p.repeat_v),"RenderOrder":str(p.order),
        "_hl_overlay_stop":"1" if p.stop_at_surface else "0",
        "_hl_overlay_width":str(w),"_hl_overlay_height":str(h),"_hl_overlay_depth":str(p.depth*lengths[2]),
        "_hl_overlay_receivers":json.dumps(names if p.restrict_receivers else [])}
    for i,(x,y) in enumerate(((-w/2,-h/2),(-w/2,h/2),(w/2,h/2),(w/2,-h/2))):kv["uv"+str(i)]=fmt_vec((x,y,0))
    validate(kv)
    return Entity("info_overlay",origin,keyvalues=kv,source=obj.name)


def receiver_objects(context):
    from .extract import effective_role
    from .mapcollection import map_objects
    inside=map_objects(context.scene)
    return [o for o in context.scene.objects if o.name in inside and o.type=="MESH" and not o.get("hl_vmf_kind")=="disp"
            and (effective_role(o)=="BRUSH" or (effective_role(o)=="BRUSH_ENTITY" and o.hammerless.classname=="func_detail"))]


def receiver_faces(context):
    from .extract import mesh_to_brushes, game_path
    class Materials:
        def resolve(self,mat):return (game_path(mat) if mat else context.scene.hammerless.default_material,.25,16)
    scale=context.scene.hammerless.units_per_meter
    objects=receiver_objects(context)
    # Collection updates also happen whenever a child projector moves. Compare
    # actual receiver membership/visibility instead of treating those as geometry.
    visible=[obj for obj in objects if obj.visible_get()]
    key=(context.scene.as_pointer(),context.view_layer.name,scale,
         context.scene.hammerless.default_material,tuple(o.as_pointer() for o in visible))
    if _state.get("faces") is not None and _state.get("face_key")==key:
        return _state["faces"]
    deps=context.evaluated_depsgraph_get()
    from ..core.vmf import VMFWriter
    from ..core.vmfimport import solid_faces
    writer=VMFWriter()
    faces=[]
    for obj in visible:
        for brush in mesh_to_brushes(obj,deps,scale,Materials()):
            # merge_coplanar retains a representative triangle, not the full
            # surface. Reconstruct the same plane intersection used by export.
            for side,verts in solid_faces(writer.solid(brush)):
                faces.append((verts,side.get("material",""),brush.source))
    import numpy as np
    _state["faces"]=faces;_state["face_key"]=key
    _state["face_min"]=np.array([np.min(f[0],axis=0) for f in faces]).reshape((-1,3))
    _state["face_max"]=np.array([np.max(f[0],axis=0) for f in faces]).reshape((-1,3))
    return faces


def nearby_faces(kv,faces):
    """Vectorized broad phase: clip only faces near this projection volume."""
    import numpy as np
    origin=np.fromstring(kv["BasisOrigin"],sep=" ")
    origin-=np.fromstring(kv["BasisNormal"],sep=" ")*float(kv["_hl_overlay_depth"])/2
    half=sum(np.abs(np.fromstring(kv[axis],sep=" "))*float(kv[size])/2
             for axis,size in (("BasisU","_hl_overlay_width"),("BasisV","_hl_overlay_height"),("BasisNormal","_hl_overlay_depth")))+.02
    indices=np.flatnonzero(np.all(_state["face_max"]>=origin-half,axis=1)&np.all(_state["face_min"]<=origin+half,axis=1))
    return (faces[i] for i in indices)


def shader():
    global _shader
    if _shader is None:
        info=gpu.types.GPUShaderCreateInfo(); iface=gpu.types.GPUStageInterfaceInfo("hl_overlay_iface")
        iface.smooth("VEC2","texcoord")
        info.vertex_in(0,"VEC3","pos");info.vertex_in(1,"VEC2","uv")
        info.vertex_out(iface);info.fragment_out(0,"VEC4","fragColor")
        info.push_constant("MAT4","mvp");info.push_constant("FLOAT","modulate");info.push_constant("FLOAT","alphaTest")
        info.sampler(0,"FLOAT_2D","image")
        info.vertex_source("void main(){texcoord=uv;gl_Position=mvp*vec4(pos,1.0);gl_Position.z-=0.00004*gl_Position.w;}")
        info.fragment_source("void main(){vec4 c=texture(image,fract(vec2(texcoord.x,1.0-texcoord.y)));if(c.a<=alphaTest)discard;fragColor=modulate>0.5?vec4(c.rgb*2.0,1.0):c;}")
        _shader=gpu.shader.create_from_info(info)
    return _shader


def texture(context,path):
    from .ops import game_content,game_root
    import numpy as np
    from ..core.vtf_read import read_vtf
    from ..core.gamematerials import read_vmt,texture_path,read_texture_bytes
    root=game_root(context);key=(root,path)
    if key in _state["textures"]:return _state["textures"][key]
    content=game_content(root)
    params=read_vmt(content,path,root+"/left4dead2")
    tex=params.get("$basetexture")
    if not tex:raise ValueError("Material has no readable base texture")
    data=read_texture_bytes(content,texture_path(tex),root+"/left4dead2")
    if not data:raise ValueError("Texture could not be loaded")
    _,_,rgba=read_vtf(data,512)
    pixels=np.ascontiguousarray(rgba[::-1],dtype=np.float32)/255.0
    h,w=pixels.shape[:2]
    image=gpu.types.GPUTexture((w,h),format="RGBA16F",data=gpu.types.Buffer("FLOAT",pixels.size,pixels.ravel()))
    name=params.get("shader","");modulate=name.startswith("decalmodulate")
    blend="MULTIPLY" if modulate else "ADDITIVE" if params.get("$additive","0")=="1" else "ALPHA"
    threshold=float(params.get("$alphatestreference","0.5")) if params.get("$alphatest","0")=="1" else 0.0
    warning=""
    if name not in ("decalmodulate","lightmappedgeneric","unlitgeneric"):
        warning="Preview approximates shader: "+name
    elif not modulate and name!="unlitgeneric":warning="Projection matches export; preview lighting is approximate"
    extras=[k for k in ("$basetexturetransform","$bumpmap","$detail","$color","$color2","$alpha") if k in params]
    if extras:warning="Preview does not yet reproduce "+", ".join(extras)
    value=(image,blend,modulate,threshold,warning)
    _state["textures"][key]=value
    return value


def rebuild(context):
    _state["batches"]=[];_state["messages"]={}
    from .mapcollection import map_objects
    inside=map_objects(context.scene)
    overlays=[o for o in context.scene.objects if is_overlay(o) and o.name in inside and o.hl_overlay.preview and o.visible_get()]
    if not overlays:
        _state["textures"].clear();_state["dirty"]=False
        return
    used={o.hl_overlay.material.strip().lower().removeprefix("materials/").removesuffix(".vmt") for o in overlays}
    _state["textures"]={k:v for k,v in _state["textures"].items() if k[1] in used}
    faces=receiver_faces(context);scale=context.scene.hammerless.units_per_meter
    for obj in sorted(overlays,key=lambda o:o.hl_overlay.order):
        try:
            kv=entity(obj,scale).keyvalues;vertices=[];uvs=[];count=0
            hits=[]
            for index,(verts,mat,source) in enumerate(nearby_faces(kv,faces)):
                clipped=project(kv,verts,mat,source)
                if clipped:hits.append((index,clipped))
            for index,clipped in stop_at_surface(kv,hits):
                count+=1
                for i in range(1,len(clipped)-1):
                    for p,uv in (clipped[0],clipped[i],clipped[i+1]):vertices.append(tuple(c/scale for c in p));uvs.append(uv)
            _state["messages"][obj.name]=(str(count)+" receiving brush face(s)" if count else
                "No receiving face: use Align to Nearest Brush")
            if vertices:
                tex,blend,modulate,threshold,warning=texture(context,kv["material"])
                batch=batch_for_shader(shader(),"TRIS",{"pos":vertices,"uv":uvs})
                _state["batches"].append((batch,tex,blend,modulate,threshold))
                if warning:_state["messages"][obj.name]+="; "+warning
        except Exception as exc:_state["messages"][obj.name]=str(exc)
    _state["dirty"]=False


def draw():
    context=bpy.context
    if context.region_data is None:return
    view=(context.scene.as_pointer(),context.view_layer.name)
    if _state.get("view")!=view:
        _state["view"]=view;_state["faces"]=None;changed()
    from .particlepreview import work_busy
    if work_busy():return
    old=(gpu.state.blend_get(),gpu.state.depth_test_get(),gpu.state.depth_mask_get())
    try:
        if _state["dirty"]:rebuild(context)
        gpu.state.depth_test_set("LESS_EQUAL");gpu.state.depth_mask_set(False)
        for batch,tex,blend,modulate,threshold in _state["batches"]:
            sh=shader();sh.bind();sh.uniform_float("mvp",context.region_data.perspective_matrix)
            sh.uniform_float("modulate",float(modulate));sh.uniform_float("alphaTest",threshold);sh.uniform_sampler("image",tex)
            gpu.state.blend_set(blend);batch.draw(sh)
    finally:
        gpu.state.blend_set(old[0]);gpu.state.depth_test_set(old[1]);gpu.state.depth_mask_set(old[2])


@persistent
def deps_changed(scene,depsgraph):
    for update in depsgraph.updates:
        item=update.id
        if isinstance(item,bpy.types.Object) and is_overlay(item):
            changed()  # Moving a projector never reconverts the map brushes.
        elif isinstance(item,bpy.types.Object):
            from .extract import effective_role
            if item.type=="MESH" and effective_role(item) in ("BRUSH","BRUSH_ENTITY"):
                _state["faces"]=None
                changed()
        elif isinstance(item,bpy.types.Mesh):
            _state["faces"]=None
            changed()
        elif isinstance(item,(bpy.types.Collection,bpy.types.Scene)):
            # Membership, visibility and units are checked by receiver_faces.
            changed()


@persistent
def reset(*_):
    _state.update(dirty=True,batches=[],textures={},messages={},faces=None)


class HL_OT_add_overlay(bpy.types.Operator):
    bl_idname="hammerless.add_overlay";bl_label="Add Brush Overlay";bl_options={"REGISTER","UNDO"}
    def execute(self,context):
        from .mapcollection import map_collection
        obj=bpy.data.objects.new("Brush Overlay",None)
        (map_collection(context.scene) or context.collection).objects.link(obj)
        obj.hammerless.role="ENTITY";obj.hammerless.classname="info_overlay";obj.hl_overlay.enabled=True
        obj.location=context.scene.cursor.location;obj.rotation_euler=context.scene.cursor.rotation_euler
        obj.empty_display_type="ARROWS";obj.empty_display_size=32/context.scene.hammerless.units_per_meter
        for o in context.selected_objects:o.select_set(False)
        obj.select_set(True);context.view_layer.objects.active=obj;changed()
        # A cursor may carry arbitrary rotation or sit above the brush. Start on
        # the nearest receiver so the default overlay is immediately usable.
        context.view_layer.update()
        HL_OT_overlay_align.execute(self,context)
        return {"FINISHED"}


class HL_OT_overlay_align(bpy.types.Operator):
    bl_idname="hammerless.overlay_align";bl_label="Align to Nearest Brush";bl_options={"REGISTER","UNDO"}
    def execute(self,context):
        from mathutils.bvhtree import BVHTree
        obj=context.object;scale=context.scene.hammerless.units_per_meter
        verts=[];polys=[]
        for points,mat,source in receiver_faces(context):
            if mat.lower().startswith("tools/"):continue
            polys.append(tuple(range(len(verts),len(verts)+len(points))));verts.extend(points)
        if not polys:self.report({"ERROR"},"No receiving brushes");return {"CANCELLED"}
        hit=BVHTree.FromPolygons(verts,polys).find_nearest(obj.matrix_world.translation*scale)
        if hit[0] is None:return {"CANCELLED"}
        loc,normal,_,_=hit
        old_scale=obj.matrix_world.to_scale()
        obj.matrix_world=normal.to_track_quat("Z","Y").to_matrix().to_4x4() @ Matrix.Diagonal((*old_scale,1))
        obj.matrix_world.translation=loc/scale
        changed();return {"FINISHED"}


class HL_OT_overlay_receivers(bpy.types.Operator):
    bl_idname="hammerless.overlay_receivers";bl_label="Assign Selected Brushes";bl_options={"REGISTER","UNDO"}
    clear:BoolProperty(default=False)
    def execute(self,context):
        obj=context.object;p=obj.hl_overlay
        p.receivers.clear()
        if not self.clear:
            eligible=set(receiver_objects(context))
            for o in context.selected_objects:
                if o in eligible:p.receivers.add().object=o
            if not p.receivers:self.report({"WARNING"},"Select brushes and keep the overlay active")
        p.restrict_receivers=not self.clear;changed();return {"FINISHED"}


class HL_OT_overlay_material(bpy.types.Operator):
    bl_idname="hammerless.overlay_material";bl_label="Use Decal Material";bl_options={"REGISTER","UNDO"}
    material:StringProperty()
    def execute(self,context):
        context.object.hl_overlay.material=self.material;changed();return {"FINISHED"}


class HL_OT_overlay_page(bpy.types.Operator):
    bl_idname="hammerless.overlay_page";bl_label="Decal Browser Page"
    delta:IntProperty()
    def execute(self,context):
        context.window_manager.hl_decal_page=max(0,context.window_manager.hl_decal_page+self.delta)
        return {"FINISHED"}


def draw_settings(layout,context):
    obj=context.object;p=obj.hl_overlay
    layout.label(text="Brush Overlay")
    layout.prop(p,"material")
    row=layout.row(align=True);row.prop(p,"width");row.prop(p,"height")
    row=layout.row(align=True);row.prop(p,"repeat_u");row.prop(p,"repeat_v")
    layout.prop(p,"depth");layout.prop(p,"stop_at_surface");layout.prop(p,"order");layout.prop(p,"preview")
    layout.operator("hammerless.overlay_align")
    layout.prop(p,"restrict_receivers")
    row=layout.row(align=True);row.operator("hammerless.overlay_receivers");row.operator("hammerless.overlay_receivers",text="All Nearby").clear=True
    for r in p.receivers:layout.prop(r,"object",text="Brush")
    message=_state["messages"].get(obj.name,"")
    if message:
        from .ui import _wrap
        for line in _wrap(message,48):layout.label(text=line)
    layout.label(text="Local +Z points away from receiving surface")
    layout.label(text="World/detail brushes only; no props or terrain")


class HL_PT_decal_browser(bpy.types.Panel):
    bl_label="Decal Browser";bl_space_type="VIEW_3D";bl_region_type="UI";bl_category="Hammerless";bl_parent_id="HL_PT_object"
    @classmethod
    def poll(cls,context):return is_overlay(context.object)
    def draw(self,context):
        from .texturing import all_materials,thumbnail
        wm=context.window_manager;layout=self.layout
        layout.prop(wm,"hl_decal_search",text="Search",icon="VIEWZOOM");layout.prop(wm,"hl_decal_all",text="Search All Materials")
        terms=wm.hl_decal_search.lower().split()
        names=[n for n in all_materials(context) if (wm.hl_decal_all or "decal" in n or "overlay" in n) and all(t in n.lower() for t in terms)]
        pages=max(1,(len(names)+11)//12);page=min(wm.hl_decal_page,pages-1)
        row=layout.row(align=True);row.operator("hammerless.overlay_page",text="",icon="TRIA_LEFT").delta=-1
        row.label(text=f"{page+1}/{pages} ({len(names)} materials)");row.operator("hammerless.overlay_page",text="",icon="TRIA_RIGHT").delta=1
        grid=layout.grid_flow(columns=3,even_columns=True,even_rows=True)
        for name in names[page*12:page*12+12]:
            col=grid.column(align=True);icon=thumbnail(context,name)
            if icon:col.template_icon(icon_value=icon,scale=4)
            col.operator("hammerless.overlay_material",text=name.rsplit("/",1)[-1][:22]).material=name


def add_menu(self,context):self.layout.operator("hammerless.add_overlay",icon="IMAGE_DATA")


CLASSES=(HL_OverlayReceiver,HL_OverlaySettings,HL_OT_add_overlay,HL_OT_overlay_align,HL_OT_overlay_receivers,HL_OT_overlay_material,HL_OT_overlay_page,HL_PT_decal_browser)

def register():
    global _handle
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Object.hl_overlay=PointerProperty(type=HL_OverlaySettings)
    bpy.types.WindowManager.hl_decal_search=StringProperty(default="",update=lambda self,ctx:setattr(self,"hl_decal_page",0))
    bpy.types.WindowManager.hl_decal_all=BoolProperty(default=False,update=lambda self,ctx:setattr(self,"hl_decal_page",0))
    bpy.types.WindowManager.hl_decal_page=IntProperty(default=0,min=0)
    bpy.types.HL_MT_add.append(add_menu)
    bpy.types.HL_PT_add.append(add_menu)
    bpy.app.handlers.depsgraph_update_post.append(deps_changed)
    for handlers in (bpy.app.handlers.load_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post):handlers.append(reset)
    _handle=bpy.types.SpaceView3D.draw_handler_add(draw,(),"WINDOW","POST_VIEW")


def unregister():
    global _handle,_shader
    if _handle is not None:bpy.types.SpaceView3D.draw_handler_remove(_handle,"WINDOW")
    _handle=None;_shader=None;reset()
    bpy.types.HL_MT_add.remove(add_menu)
    bpy.types.HL_PT_add.remove(add_menu)
    if deps_changed in bpy.app.handlers.depsgraph_update_post:bpy.app.handlers.depsgraph_update_post.remove(deps_changed)
    for handlers in (bpy.app.handlers.load_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post):
        if reset in handlers:handlers.remove(reset)
    del bpy.types.Object.hl_overlay
    for name in ("hl_decal_search","hl_decal_all","hl_decal_page"):delattr(bpy.types.WindowManager,name)
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)

