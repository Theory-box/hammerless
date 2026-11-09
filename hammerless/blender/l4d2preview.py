"""Experimental L4D2 unshadowed point irradiance; not full lighting parity.

Caches evaluated triangles in object space. Transforms and camera changes do not
re-extract meshes. No scene mutation, bake invocation, timers or global handlers.
GPU resources belong to an engine instance and are released with that instance.
"""
from dataclasses import dataclass, field

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader


@dataclass
class Geometry:
    positions: np.ndarray
    normals: np.ndarray
    batch: dict = field(default_factory=dict)
    uvs: object = None
    face_normals: object = None
    painted: object = None
    material_ids: object = None
    materials: tuple = ()
    use_uv: bool = False
    material_batches: dict = field(default_factory=dict)


def extract_geometry(obj):
    mesh = obj.to_mesh()
    try:
        mesh.calc_loop_triangles()
        positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', positions)
        positions = positions.reshape(-1, 3)
        vertices = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        loops = np.empty_like(vertices)
        mesh.loop_triangles.foreach_get('vertices', vertices)
        mesh.loop_triangles.foreach_get('loops', loops)
        normals = np.empty(len(mesh.corner_normals) * 3, dtype=np.float32)
        mesh.corner_normals.foreach_get('vector', normals)
        geometry = Geometry(positions[vertices].copy(), normals.reshape(-1, 3)[loops].copy())
        uv = np.zeros((len(mesh.loops), 2), dtype=np.float32)
        if mesh.uv_layers.active:
            mesh.uv_layers.active.data.foreach_get('uv', uv.ravel())
        geometry.uvs = uv[loops].copy()
        face_normals = np.empty((len(mesh.loop_triangles), 3), dtype=np.float32)
        mesh.loop_triangles.foreach_get('normal', face_normals.ravel())
        geometry.face_normals = np.repeat(face_normals, 3, axis=0)
        polygon_ids = np.empty(len(mesh.loop_triangles), dtype=np.int32)
        mesh.loop_triangles.foreach_get('polygon_index', polygon_ids)
        painted = np.zeros(len(mesh.polygons), dtype=np.int32)
        attribute = mesh.attributes.get('hl_tex_uv')
        if attribute and attribute.domain == 'FACE' and attribute.data_type == 'INT':
            attribute.data.foreach_get('value', painted)
        geometry.painted = np.repeat(painted[polygon_ids], 3).astype(np.float32)
        geometry.material_ids = np.empty(len(mesh.loop_triangles), dtype=np.int32)
        mesh.loop_triangles.foreach_get('material_index', geometry.material_ids)
        return geometry
    finally:
        obj.to_mesh_clear()


class SceneCache:
    def __init__(self):
        self.geometry = {}
        self.instances = []
        self.extractions = 0
        self.lights = []
        self.light_texture = None
        self.light_signature = None
        self.warnings = []
        self.trace = None

    def update_lights(self, depsgraph):
        from .extract import light_entity, scene_settings_to_ir
        from ..core.build import ATTENUATION_KEYS, default_light_environment
        from ..core.previewlighting import PointLight, spot_parameters, vrad_light_direction
        scale = depsgraph.scene.hammerless.units_per_meter
        lights, warnings = [], []
        environment = None
        for instance in depsgraph.object_instances:
            obj = instance.object
            if obj.type != 'LIGHT' or not instance.show_self:
                continue
            if obj.data.type not in ('POINT', 'SPOT', 'SUN'):
                warnings.append(f'{obj.name}: {obj.data.type} light not supported yet')
                continue
            entity = light_entity(obj, scale)
            if obj.data.type == 'SUN':
                if environment is None:
                    environment = entity
                else:
                    warnings.append('Only the first light_environment is used, as in VRAD')
                continue
            keys = dict(entity.keyvalues)
            if not any(keys.get(k, '').strip() for k in ATTENUATION_KEYS):
                keys.update(_constant_attn='0', _linear_attn='0', _quadratic_attn='1')
            try:
                light = PointLight.from_keys(keys)
                cone = spot_parameters(keys) if obj.data.type == 'SPOT' else (0.,0.,0.,0.)
                if keys.get('target', '').strip():
                    raise NotImplementedError('Target-aimed light not supported yet')
                if instance.is_instance:
                    raise NotImplementedError('Instanced lights are not exported by the current build path')
                direction = vrad_light_direction(entity.angles, keys)
            except (ValueError, NotImplementedError) as exc:
                warnings.append(f'{obj.name}: {exc}')
                continue
            lights.append((tuple(instance.matrix_world.translation), light.intensity, light.attenuation,
                           (*direction,cone[0]), cone[1:]))
        if environment is None:
            environment = default_light_environment(scene_settings_to_ir(depsgraph.scene.hammerless))
        if environment is not None:
            keys = dict(environment.keyvalues)
            keys.update(_constant_attn='1', _linear_attn='0', _quadratic_attn='0', _fifty_percent_distance='0')
            sun = PointLight.from_keys(keys)
            direction = vrad_light_direction(environment.angles, keys)
            lights.append(((0.,0.,0.), sun.intensity, (1.,0.,0.), (*direction,2.), (0.,0.,0.)))
            if any(float(x)>0 for x in keys.get('_ambient','0').split()[-1:]):
                warnings.append('Sky ambient is not implemented yet')
            if float(keys.get('SunSpreadAngle',0)):
                warnings.append('Sun currently has hard shadows; angular spread is pending')
        signature = (scale, tuple(lights))
        if signature != self.light_signature:
            self.light_texture = None
            self.light_signature = signature
        self.lights, self.warnings = lights, warnings
        self.unit_scale = scale

    def upload_lights(self):
        if self.light_texture is None:
            data = np.zeros((5, max(1, len(self.lights)), 4), dtype=np.float32)
            for i, light in enumerate(self.lights):
                for row, value in enumerate(light):
                    data[row, i, :len(value)] = value
            self.light_texture = gpu.types.GPUTexture(
                (data.shape[1], 5), format='RGBA32F',
                data=gpu.types.Buffer('FLOAT', data.size, data.ravel()))
        return self.light_texture

    def update(self, depsgraph):
        dirty = set()
        dirty_meshes = set()
        for update in depsgraph.updates:
            if not update.is_updated_geometry:
                continue
            original = update.id.original
            if isinstance(original, bpy.types.Object):
                dirty.add(original.as_pointer())
            elif isinstance(original, bpy.types.Mesh):
                dirty_meshes.add(original.as_pointer())
        used, instances = set(), []
        for instance in depsgraph.object_instances:
            obj = instance.object
            if obj.type != 'MESH' or not instance.show_self:
                continue
            original = obj.original
            key = original.as_pointer()
            if key not in used and (key not in self.geometry or key in dirty or
                                   original.data.as_pointer() in dirty_meshes):
                self.geometry[key] = extract_geometry(obj)
                self.extractions += 1
            used.add(key)
            geometry = self.geometry[key]
            geometry.materials = tuple(slot.material for slot in obj.material_slots)
            geometry.use_uv = bool(obj.data.get('hl_model')) or obj.hammerless.role == 'MODEL'
            instances.append((key, instance.matrix_world.copy()))
        self.geometry = {key: value for key, value in self.geometry.items() if key in used}
        self.instances = instances


def diagnostic_shader(lit=False, textured=False, shadows=False):
    interface = gpu.types.GPUStageInterfaceInfo('hl_preview_normal')
    interface.smooth('VEC3', 'worldNormal')
    if lit:
        interface.smooth('VEC3', 'worldPosition')
        interface.flat('VEC3', 'surfaceNormal')
        info_world = 'worldPosition=(model*vec4(position,1.0)).xyz;surfaceNormal=normalMatrix*faceNormal;'
    else:
        info_world = ''
    info = gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0, 'VEC3', 'position')
    info.vertex_in(1, 'VEC3', 'normal')
    info.vertex_out(interface)
    if lit:
        info.vertex_in(3, 'VEC3', 'faceNormal')
        info.push_constant('FLOAT', 'receiverOffset')
    if textured:
        interface.smooth('VEC2', 'surfaceUV')
        interface.flat('FLOAT', 'paintedUV')
        info.vertex_in(2, 'VEC2', 'texcoord')
        info.vertex_in(4, 'FLOAT', 'painted')
        info_world += 'surfaceUV=texcoord;paintedUV=painted;'
        info.sampler(1, 'FLOAT_2D', 'baseTexture')
        info.push_constant('INT', 'hasTexture')
        info.push_constant('INT', 'useUV')
        info.push_constant('VEC3', 'baseColor')
        info.push_constant('VEC3', 'textureSizeScale')
    info.push_constant('MAT4', 'mvp')
    info.push_constant('MAT3', 'normalMatrix')
    if lit:
        info.push_constant('MAT4', 'model')
        info.push_constant('FLOAT', 'unitScale')
        info.push_constant('INT', 'lightCount')
        info.sampler(0, 'FLOAT_2D', 'lights')
    info.fragment_out(0, 'VEC4', 'color')
    if shadows:
        info.sampler(2, 'FLOAT_2D', 'traceNodes')
        info.sampler(3, 'FLOAT_2D', 'traceTriangles')
        info.sampler(4, 'FLOAT_2D', 'traceInstances')
        info.push_constant('INT', 'traceInstanceCount')
    info.vertex_source('void main() { gl_Position=mvp*vec4(position,1.0); worldNormal=normalMatrix*normal; '+info_world+' }')
    fragment = '''void main() {
        vec3 sum=vec3(0.0), n=normalize(worldNormal);
        vec3 samplePosition=worldPosition+normalize(surfaceNormal)*(receiverOffset/unitScale);
        for(int i=0;i<lightCount;i++) {
            vec4 direction=texelFetch(lights,ivec2(i,3),0);
            vec3 target=texelFetch(lights,ivec2(i,0),0).xyz;
            if(direction.w==2.0) target=samplePosition-direction.xyz*(56755.84/unitScale);
            vec3 delta=(target-samplePosition)*unitScale;
            float lengthSquared=dot(delta,delta);
            if(lengthSquared<=0.0) continue;
            float d=max(sqrt(lengthSquared),1.0);
            vec3 a=texelFetch(lights,ivec2(i,2),0).xyz;
            float cosine=max(dot(delta*inversesqrt(lengthSquared),n),0.0);
            if(direction.w==1.0) {
                vec3 cone=texelFetch(lights,ivec2(i,4),0).xyz;
                float facing=-dot(delta*inversesqrt(lengthSquared),direction.xyz);
                if(facing<=cone.y) continue;
                float ramp=1.0;
                if(facing<cone.x) {
                    ramp=clamp((facing-cone.y)/(cone.x-cone.y),0.0,1.0);
                    if(cone.z!=0.0 && cone.z!=1.0) ramp=pow(ramp,float(int(4.0*cone.z))*0.25);
                }
                cosine*=ramp*facing;
            }
            sum+=texelFetch(lights,ivec2(i,1),0).xyz*(cosine/(a.z*d*d+a.y*d+a.x));
        }
        color=vec4(sum/255.0,1.0);
    }''' if lit else 'void main() { color=vec4(normalize(worldNormal)*0.5+0.5,1.0); }'
    if textured:
        fragment = fragment.replace('color=vec4(sum/255.0,1.0);', '''
        vec2 coords=surfaceUV;
        if(useUV==0 && paintedUV<0.5) {
            vec3 fn=surfaceNormal, a=abs(fn), p=worldPosition*unitScale;
            if(a.z>=a.x && a.z>=a.y) coords=vec2(fn.z>=0.0?p.x:-p.x,p.y);
            else if(a.x>=a.y) coords=vec2(fn.x>0.0?p.y:-p.y,p.z);
            else coords=vec2(fn.y>0.0?-p.x:p.x,p.z);
            coords/=textureSizeScale.xy*textureSizeScale.z;
        }
        vec3 albedo=baseColor;
        if(hasTexture!=0) albedo=texture(baseTexture,fract(coords)).rgb;
        color=vec4(albedo*sum/255.0,1.0);''')
    if shadows:
        from .previewtrace import GLSL
        fragment = fragment.replace('sum+=texelFetch', '''
            if(cosine>0.0) cosine*=traceVisibility(samplePosition+n*(0.03125/unitScale),
                target);
            sum+=texelFetch''')
        fragment = GLSL + fragment
    info.fragment_source(fragment)
    return gpu.shader.create_from_info(info)


def draw_geometry(cache, shader, view_projection, lit=False, textured=False, shadows=False):
    """Draw to the bound framebuffer; caller owns clearing and viewport setup."""
    old_depth = gpu.state.depth_test_get()
    old_mask = gpu.state.depth_mask_get()
    old_blend = gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        gpu.state.blend_set('NONE')
        shader.bind()
        if shadows:
            from .previewtrace import TraceCache
            if cache.trace is None:
                cache.trace = TraceCache()
            cache.trace.update(cache)
            cache.trace.bind(shader)
        if lit:
            shader.uniform_sampler('lights', cache.upload_lights())
            shader.uniform_int('lightCount', len(cache.lights))
            shader.uniform_float('unitScale', cache.unit_scale)
        for key, matrix in cache.instances:
            geometry = cache.geometry[key]
            if not len(geometry.positions):
                continue
            if not textured and lit not in geometry.batch:
                attributes = {'position':geometry.positions,'normal':geometry.normals}
                if lit:
                    attributes['faceNormal'] = geometry.face_normals if geometry.face_normals is not None else geometry.normals
                geometry.batch[lit] = batch_for_shader(shader, 'TRIS', attributes)
            shader.uniform_float('mvp', view_projection @ matrix)
            if lit:
                shader.uniform_float('model', matrix)
                shader.uniform_float('receiverOffset', 0.0 if geometry.use_uv else 1.0)
            shader.uniform_float('normalMatrix', matrix.to_3x3().inverted_safe().transposed())
            if textured:
                from .extract import _base_color_image, texture_size
                for mi in np.unique(geometry.material_ids):
                    if int(mi) not in geometry.material_batches:
                        mask = np.repeat(geometry.material_ids == mi, 3)
                        geometry.material_batches[int(mi)] = batch_for_shader(shader, 'TRIS', {
                            'position': geometry.positions[mask], 'normal': geometry.normals[mask],
                            'texcoord': geometry.uvs[mask], 'faceNormal': geometry.face_normals[mask],
                            'painted': geometry.painted[mask]})
                    mat = geometry.materials[mi] if mi < len(geometry.materials) else None
                    img = _base_color_image(mat) if mat else None
                    color = (1.0, 1.0, 1.0)
                    if mat:
                        color = mat.diffuse_color[:3]
                        if mat.use_nodes:
                            bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
                            if bsdf and not bsdf.inputs['Base Color'].is_linked:
                                color = bsdf.inputs['Base Color'].default_value[:3]
                    shader.uniform_float('baseColor', color)
                    shader.uniform_int('hasTexture', int(img is not None))
                    shader.uniform_int('useUV', int(geometry.use_uv))
                    w, h = texture_size(mat)
                    scale = mat.hammerless.texture_scale if mat else 0.25
                    shader.uniform_float('textureSizeScale', (w, h, max(scale, 1e-6)))
                    # Bind a valid sampler even for untextured materials.
                    shader.uniform_sampler('baseTexture', gpu.texture.from_image(img) if img else cache.upload_lights())
                    geometry.material_batches[int(mi)].draw(shader)
            else:
                geometry.batch[lit].draw(shader)
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.depth_mask_set(old_mask)
        gpu.state.blend_set(old_blend)


class HAMMERLESS_L4D2Preview(bpy.types.RenderEngine):
    bl_idname = 'HAMMERLESS_L4D2_PREVIEW'
    bl_label = 'L4D2 Preview (Experimental)'
    bl_use_preview = False
    bl_use_postprocess = False

    def __init__(self):
        self.cache = SceneCache()
        self.shader = None
        self.error = ''

    def view_update(self, context, depsgraph):
        try:
            self.cache.update(depsgraph)
            self.cache.update_lights(depsgraph)
            self.error = ''
        except Exception as exc:
            self.error = str(exc)
            self.cache = SceneCache()
        self.tag_redraw()

    def view_draw(self, context, depsgraph):
        gpu.state.active_framebuffer_get().clear(color=(0.025, 0.025, 0.025, 1), depth=1.0)
        if self.error:
            self.update_stats('L4D2 Preview', self.error)
            return
        try:
            if self.shader is None:
                self.shader = diagnostic_shader(lit=True, textured=True, shadows=True)
            draw_geometry(self.cache, self.shader, context.region_data.perspective_matrix,
                          lit=True, textured=True, shadows=True)
        except Exception as exc:
            self.update_stats('L4D2 Preview GPU error', str(exc))
            return
        warning = '; '.join(self.cache.warnings)
        self.update_stats('L4D2 Preview', warning or 'Opaque point shadows — advanced materials and GI pending')

    def render(self, depsgraph):
        self.report({'ERROR'}, 'L4D2 Preview is a viewport prototype; final rendering is not implemented.')


class HAMMERLESS_PT_l4d2_preview(bpy.types.Panel):
    bl_label = 'L4D2 Preview — Experimental'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'render'

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == HAMMERLESS_L4D2Preview.bl_idname

    def draw(self, context):
        col = self.layout.column()
        col.label(text='Point/spot/sun with opaque GPU shadows', icon='INFO')
        col.label(text='Base textures; linear irradiance / 255.')
        col.label(text='Sky ambient, alpha, advanced shaders and GI pending.')


CLASSES = (HAMMERLESS_L4D2Preview, HAMMERLESS_PT_l4d2_preview)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
