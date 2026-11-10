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

_material_refresh_revision = 0


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
    shadow_positions: object = None
    shadow_revision: int = 0
    shadow_signature: object = None
    shadow_triangle_ids: object = None
    depth_batches: dict = field(default_factory=dict)


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
        self.sky_color = (0.,0.,0.)
        self.sky_texture = None
        self.surface_materials = None
        self.emission_cache = None

    def update_lights(self, depsgraph):
        from .extract import light_entity, scene_settings_to_ir, effective_role
        from .previewscene import included_instance
        from ..core.build import ATTENUATION_KEYS, default_light_environment
        from ..core.previewlighting import PointLight, spot_parameters, vrad_light_direction, sky_intensity
        scale = depsgraph.scene.hammerless.units_per_meter
        lights, warnings = [], []
        environment = None
        self.sky_color = (0.,0.,0.)
        for instance in depsgraph.object_instances:
            obj = instance.object
            if obj.type != 'LIGHT' or not instance.show_self:
                continue
            if effective_role(obj.original) != 'LIGHT' or not included_instance(instance,depsgraph.view_layer):
                continue
            # The exporter does not export instanced point/spot/sun lights.
            # Check before SUN handling so an instanced sun cannot replace the
            # map's real environment (AREA instances have a separate exporter).
            if instance.is_instance and obj.data.type != 'AREA':
                warnings.append(f'{obj.name}: Instanced lights are not exported by the current build path')
                continue
            if obj.data.type == 'AREA':
                from .emission import area_lights
                try:
                    entities=area_lights(obj,scale,instance.matrix_world)
                    for sample_index,sample in enumerate(entities):
                        keys=dict(sample.keyvalues)
                        light=PointLight.from_keys(keys)
                        cone=spot_parameters(keys)
                        direction=vrad_light_direction(sample.angles,keys)
                        lights.append((tuple(v/scale for v in sample.origin),light.intensity,(*light.attenuation,len(entities)),
                                       (*direction,cone[0]),(*cone[1:],sample_index+1 if len(entities)>8 else 0)))
                    if len(entities)>64:
                        warnings.append(f'{obj.name}: {len(entities)} area samples; each adds shadow-ray cost')
                except (ValueError,ZeroDivisionError) as exc:
                    warnings.append(f'{obj.name}: area light unavailable: {exc}')
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
                direction = vrad_light_direction(entity.angles, keys)
            except (ValueError, NotImplementedError) as exc:
                warnings.append(f'{obj.name}: {exc}')
                continue
            lights.append((tuple(instance.matrix_world.translation), light.intensity, light.attenuation,
                           (*direction,cone[0]), cone[1:]))
        if self.emission_cache is None:
            from .previewemission import EmissionCache
            self.emission_cache = EmissionCache()
        groups, emission_warnings = self.emission_cache.update(self, depsgraph, _material_refresh_revision)
        warnings.extend(emission_warnings)
        for entities in groups:
            for index, sample in enumerate(entities):
                keys = dict(sample.keyvalues)
                light = PointLight.from_keys(keys)
                cone = spot_parameters(keys)
                direction = vrad_light_direction(sample.angles, keys)
                lights.append((tuple(v/scale for v in sample.origin), light.intensity,
                               (*light.attenuation, len(entities)), (*direction, cone[0]),
                               (*cone[1:], index+1 if len(entities)>8 else 0)))
        if environment is None:
            environment = default_light_environment(scene_settings_to_ir(depsgraph.scene.hammerless))
        if environment is not None:
            keys = dict(environment.keyvalues)
            keys.update(_constant_attn='1', _linear_attn='0', _quadratic_attn='0', _fifty_percent_distance='0')
            sun = PointLight.from_keys(keys)
            direction = vrad_light_direction(environment.angles, keys)
            lights.append(((0.,0.,0.), sun.intensity, (1.,0.,0.), (*direction,2.), (0.,0.,0.)))
            if depsgraph.scene.hammerless.sky_light == 'FLAT':
                self.sky_color = sky_intensity(keys, sun.intensity)
            else:
                warnings.append('Skybox/HDRI ambient is not implemented; only flat sky is supported')
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

    def upload_sky(self):
        if self.sky_texture is None:
            from ..core.previewlighting import sky_directions
            data = np.zeros((162,4),np.float32)
            data[:,:3] = sky_directions(162)
            self.sky_texture = gpu.types.GPUTexture((162,1),format='RGBA32F',
                data=gpu.types.Buffer('FLOAT',data.size,data.ravel()))
        return self.sky_texture

    def update(self, depsgraph):
        from .previewscene import included_instance, material_path, NON_SHADOW_TOOLS, collection_role_snapshot
        self.role_lookup = collection_role_snapshot()
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
            if not included_instance(instance,depsgraph.view_layer,self.role_lookup):
                continue
            key = original.as_pointer()
            if key not in used and (key not in self.geometry or key in dirty or
                                   original.data.as_pointer() in dirty_meshes):
                self.geometry[key] = extract_geometry(obj)
                self.extractions += 1
            used.add(key)
            geometry = self.geometry[key]
            geometry.materials = tuple(slot.material for slot in obj.material_slots)
            material_paths = tuple(material_path(m) for m in geometry.materials)
            if material_paths != geometry.shadow_signature:
                noncasters = [i for i,path in enumerate(material_paths) if path in NON_SHADOW_TOOLS]
                if noncasters:
                    keep = ~np.isin(geometry.material_ids,noncasters)
                    geometry.shadow_triangle_ids = np.flatnonzero(keep)
                    mask = np.repeat(keep,3)
                    geometry.shadow_positions = geometry.positions[mask].copy()
                else:
                    geometry.shadow_positions = None
                    geometry.shadow_triangle_ids = None
                geometry.shadow_signature = material_paths
                geometry.shadow_revision += 1
            geometry.use_uv = bool(obj.data.get('hl_model')) or obj.hammerless.role == 'MODEL'
            instances.append((key, instance.matrix_world.copy()))
        self.geometry = {key: value for key, value in self.geometry.items() if key in used}
        self.instances = instances


def diagnostic_shader(lit=False, textured=False, shadows=False, display=False, bounce=False, surface_samples=False):
    if bounce and not (lit and shadows):
        raise ValueError('Bounced lighting requires lit geometry and ray tracing')
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
        info.push_constant('INT', 'surfaceUnlit')
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
        info.sampler(5, 'FLOAT_2D', 'skyDirections')
        info.push_constant('VEC3', 'skyColor')
        info.push_constant('INT', 'primarySkyIndex')
        info.push_constant('INT', 'primarySkyCount')
        info.push_constant('INT', 'primaryAreaPhase')
    info.fragment_out(0, 'VEC4', 'color')
    if bounce:
        info.sampler(7, 'FLOAT_2D', 'traceSurfaces')
        info.push_constant('INT', 'bounceSamples')
        info.push_constant('INT', 'bounceSkySampled')
        info.push_constant('INT', 'bounceAreaSampled')
        info.push_constant('INT', 'bounceSeed')
        info.push_constant('INT', 'bounceDepth')
    if display:
        info.push_constant('FLOAT', 'displayExposure')
    if shadows:
        info.sampler(2, 'FLOAT_2D', 'traceNodes')
        info.sampler(3, 'FLOAT_2D', 'traceTriangles')
        info.sampler(4, 'FLOAT_2D', 'traceInstances')
        info.sampler(6, 'FLOAT_2D', 'traceTop')
        info.push_constant('INT', 'traceInstanceCount')
        info.push_constant('INT', 'traceTopCount')
    if surface_samples:info.vertex_in(5,'VEC2','sampleClip')
    clip='vec4(sampleClip,0,1)' if surface_samples else 'mvp*vec4(position,1.0)'
    info.vertex_source('void main() { gl_Position='+clip+'; worldNormal=normalMatrix*normal; '+info_world+' }')
    fragment = '''void main() {
        vec3 sum=vec3(0.0), n=normalize(worldNormal);
        vec3 samplePosition=worldPosition+normalize(surfaceNormal)*(receiverOffset/unitScale);
        for(int i=0;i<lightCount;i++) {
            int areaBatch=int(texelFetch(lights,ivec2(i,4),0).w)-1;
            float areaWeight=1.0;
            if(areaPhase>=0 && areaBatch>=0) {
                if(areaPhase<8) {
                    if(areaBatch%8!=areaPhase) continue;
                    areaWeight=8.0;
                } else {
                    int count=max(1,int(texelFetch(lights,ivec2(i,2),0).w));
                    if(areaBatch!=(areaPhase-8)%count) continue;
                    areaWeight=float(count);
                }
            }
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
            sum+=texelFetch(lights,ivec2(i,1),0).xyz*(areaWeight*cosine/(a.z*d*d+a.y*d+a.x));
        }
        if(any(greaterThan(skyColor,vec3(0.0)))) {
            float weight=0.0, visibleWeight=0.0;
            for(int j=0;j<162;j++) {
                vec3 skyDirection=-texelFetch(skyDirections,ivec2(j,0),0).xyz;
                float facing=dot(n,skyDirection);
                if(facing<=0.001) continue;
                weight+=facing;
                if(skyIndex>=0 && ((j-skyIndex+162)%162)>=skyCount)continue;
                float visibility=1.0; // SKY_VISIBILITY
                visibleWeight+=facing*visibility*(skyIndex>=0?162.0/float(skyCount):1.0);
            }
            if(weight>0.0) sum+=skyColor*(visibleWeight/weight);
        }
        color=vec4(sum/255.0,1.0);
    }''' if lit else 'void main() { color=vec4(normalize(worldNormal)*0.5+0.5,1.0); }'
    if lit:
        direct = fragment.split('color=vec4(sum/255.0,1.0);')[0]
        direct = direct.replace('void main() {', 'vec3 previewDirect(vec3 samplePosition, vec3 n, int skyIndex, int skyCount, int areaPhase) {')
        direct = direct.replace('vec3 sum=vec3(0.0), n=normalize(worldNormal);', 'vec3 sum=vec3(0.0);')
        direct = direct.replace('vec3 samplePosition=worldPosition+normalize(surfaceNormal)*(receiverOffset/unitScale);', '')
        fragment = direct + '''return sum;}
        void main() {
            vec3 n=normalize(worldNormal);
            vec3 samplePosition=worldPosition+normalize(surfaceNormal)*(receiverOffset/unitScale);
            vec3 sum=previewDirect(samplePosition,n,primarySkyIndex,primarySkyCount,primaryAreaPhase);
            color=vec4(sum/255.0,1.0);
        }'''
        if bounce:
            from .previewbounce import GLSL as BOUNCE_GLSL
            fragment = fragment.replace('void main()', BOUNCE_GLSL+'\nvoid main()')
            fragment = fragment.replace('vec3 sum=previewDirect(samplePosition,n,primarySkyIndex,primarySkyCount,primaryAreaPhase);',
                'vec3 sum=previewDirect(samplePosition,n,primarySkyIndex,primarySkyCount,primaryAreaPhase)+previewBounce(samplePosition,n);')
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
        color=vec4(albedo*(surfaceUnlit!=0?vec3(1.0):sum/255.0),1.0);''')
    if display:
        fragment = fragment.rsplit('}',1)[0] + '''
        vec3 linearColor=max(color.rgb*displayExposure,vec3(0.0));
        color.rgb=mix(12.92*linearColor,1.055*pow(linearColor,vec3(1.0/2.4))-0.055,
                      greaterThan(linearColor,vec3(0.0031308)));
    }'''
    if shadows:
        from .previewtrace import GLSL
        fragment = fragment.replace('float visibility=1.0; // SKY_VISIBILITY',
            'float visibility=traceVisibility(samplePosition+n*(0.03125/unitScale),samplePosition+skyDirection*(56755.84/unitScale));')
        fragment = fragment.replace('sum+=texelFetch', '''
            if(cosine>0.0) cosine*=traceVisibility(samplePosition+n*(0.03125/unitScale),
                target);
            sum+=texelFetch''')
        fragment = GLSL + fragment
    info.fragment_source(fragment)
    return gpu.shader.create_from_info(info)


def draw_geometry(cache, shader, view_projection, lit=False, textured=False, shadows=False, display=False, exposure=0.0, bounce_samples=None, sampled_bounce_sky=False, bounce_seed=0, bounce_depth=1, primary_sky_index=-1, primary_sky_count=162, primary_area_phase=-1, sampled_bounce_area=False, lighting_samples=None, exclude_keys=(), raster_geometry=None):
    """Draw to the bound framebuffer; caller owns clearing and viewport setup."""
    old_depth = gpu.state.depth_test_get()
    old_mask = gpu.state.depth_mask_get()
    old_blend = gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        gpu.state.blend_set('NONE')
        shader.bind()
        if display:
            shader.uniform_float('displayExposure', 2.0**exposure)
        if shadows:
            from .previewtrace import TraceCache
            if cache.trace is None:
                cache.trace = TraceCache()
            cache.trace.update(cache)
            cache.trace.bind(shader)
        if bounce_samples is not None:
            shader.uniform_int('bounceSamples',max(0,int(bounce_samples)))
            shader.uniform_int('bounceSkySampled',int(sampled_bounce_sky))
            shader.uniform_int('bounceAreaSampled',int(sampled_bounce_area))
            shader.uniform_int('bounceSeed',int(bounce_seed))
            shader.uniform_int('bounceDepth',max(1,min(8,int(bounce_depth))))
            shader.uniform_sampler('traceSurfaces',cache.trace.surfaces)
        if lit:
            shader.uniform_sampler('lights', cache.upload_lights())
            shader.uniform_int('lightCount', len(cache.lights))
            shader.uniform_float('unitScale', cache.unit_scale)
            shader.uniform_float('skyColor', cache.sky_color)
            shader.uniform_int('primarySkyIndex',int(primary_sky_index)%162 if primary_sky_index>=0 else -1)
            shader.uniform_int('primaryAreaPhase',int(primary_area_phase)%8 if primary_area_phase>=0 else -1)
            shader.uniform_int('primarySkyCount',max(1,min(162,int(primary_sky_count))))
            shader.uniform_sampler('skyDirections', cache.upload_sky())
        if lighting_samples is not None:
            from mathutils import Matrix
            shader.uniform_float('model',Matrix.Identity(4))
            shader.uniform_float('normalMatrix',Matrix.Identity(3))
            shader.uniform_float('receiverOffset',1.0)
            gpu.state.point_size_set(1.0)
            lighting_samples.draw(shader)
            return
        from .previewcull import raster_instances
        for key, matrix in raster_instances(cache,view_projection):
            if key in exclude_keys:continue
            geometry = (raster_geometry or {}).get(key,cache.geometry[key])
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
                    from .previewscene import draws_material
                    mat = geometry.materials[mi] if mi < len(geometry.materials) else None
                    if not draws_material(mat):
                        continue
                    if int(mi) not in geometry.material_batches:
                        mask = np.repeat(geometry.material_ids == mi, 3)
                        geometry.material_batches[int(mi)] = batch_for_shader(shader, 'TRIS', {
                            'position': geometry.positions[mask], 'normal': geometry.normals[mask],
                            'texcoord': geometry.uvs[mask], 'faceNormal': geometry.face_normals[mask],
                            'painted': geometry.painted[mask]})
                    if cache.surface_materials is not None:
                        img, (w, h) = cache.surface_materials.materials.get(mat.as_pointer() if mat else 0, (None,(512,512)))
                        # The prepared export-facing snapshot owns image/size.
                        # Avoid scanning material nodes again on every redraw.
                        color = (1.0,1.0,1.0)
                    else:
                        img = _base_color_image(mat) if mat else None
                        color = (1.0, 1.0, 1.0)
                        if mat:
                            color = mat.diffuse_color[:3]
                            if mat.use_nodes:
                                bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
                                if bsdf and not bsdf.inputs['Base Color'].is_linked:
                                    color = bsdf.inputs['Base Color'].default_value[:3]
                        w, h = texture_size(mat)
                    shader.uniform_int('surfaceUnlit',int(cache.surface_materials.unlit.get(mat.as_pointer() if mat else 0,False)) if cache.surface_materials else 0)
                    shader.uniform_float('baseColor', color)
                    shader.uniform_int('hasTexture', int(img is not None))
                    shader.uniform_int('useUV', int(geometry.use_uv))
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


def depth_shader():
    info=gpu.types.GPUShaderCreateInfo()
    info.vertex_in(0,'VEC3','position');info.push_constant('MAT4','mvp')
    info.vertex_source('void main(){gl_Position=mvp*vec4(position,1);}')
    info.fragment_source('void main(){}')
    return gpu.shader.create_from_info(info)


def draw_depth(cache,shader,view_projection,exclude_keys=(),raster_geometry=None):
    from .previewscene import draws_material
    shader.bind()
    from .previewcull import raster_instances
    for key,matrix in raster_instances(cache,view_projection):
        if key in exclude_keys:continue
        g=(raster_geometry or {}).get(key,cache.geometry[key])
        shader.uniform_float('mvp',view_projection@matrix)
        ids=g.material_ids if g.material_ids is not None else np.zeros(len(g.positions)//3,dtype=int)
        for mi in np.unique(ids):
            mat=g.materials[mi] if 0<=mi<len(g.materials) else None
            if not draws_material(mat):continue
            if int(mi) not in g.depth_batches:
                g.depth_batches[int(mi)]=batch_for_shader(shader,'TRIS',{'position':g.positions[np.repeat(ids==mi,3)]})
            g.depth_batches[int(mi)].draw(shader)


class HAMMERLESS_L4D2Preview(bpy.types.RenderEngine):
    bl_idname = 'HAMMERLESS_L4D2_PREVIEW'
    bl_label = 'L4D2 Preview (Experimental)'
    bl_use_preview = False
    bl_use_postprocess = False

    def __init__(self, *args, **kwargs):
        # Blender 4.4 requires initialization of the underlying RNA instance.
        super().__init__(*args, **kwargs)
        self.cache = SceneCache()
        self.shader = None
        self.shader_bounce = None
        self.bounce_materials = None
        self.bounce_signature = None
        self.bounce_pending = False
        self.bounce_uploads = 0
        self.history = None
        self.history_depth_shader = None
        self.material_revision = 0
        self.refresh_revision = _material_refresh_revision
        self.scene_signature = None
        self.error = ''
        self.surface_signature = None
        self.surface_cache = None
        from .previewchanges import LightUpdateGuard
        self.update_guard = LightUpdateGuard()

    def view_update(self, context, depsgraph):
        try:
            if self.update_guard.check(context,depsgraph,self.surface_cache,_material_refresh_revision):
                # Only lighting changes: retain geometry/material/chart snapshots.
                # The explicit guard also catches custom changes Blender may not tag.
                self.cache.update_lights(depsgraph)
                old=self.scene_signature
                self.scene_signature=(old[0],old[1],self.cache.light_signature,self.cache.sky_color,old[4],old[5])
                self.error=''
                self.tag_redraw()
                return
            self.cache.update(depsgraph)
            self.cache.update_lights(depsgraph)
            material_change = any(isinstance(u.id, (bpy.types.Material,bpy.types.Image,bpy.types.ShaderNodeTree))
                                  for u in depsgraph.updates)
            if material_change or self.refresh_revision != _material_refresh_revision:
                self.refresh_revision = _material_refresh_revision
                self.bounce_materials = None
                self.material_revision+=1
            from .previewmaterials import SurfaceMaterials
            from .ops import game_root,game_content
            import os
            settings=context.scene.hammerless
            surface_signature=(settings.game_root,settings.default_material,self.material_revision,
                tuple(sorted({m.as_pointer() if m else 0 for g in self.cache.geometry.values() for m in g.materials or (None,)})))
            if self.cache.surface_materials is None or surface_signature!=self.surface_signature:
                root=game_root(context)
                resolver=SurfaceMaterials(game_content(root),os.path.join(root,'left4dead2') if root else None,settings.default_material)
                for g in self.cache.geometry.values():
                    for mat in g.materials or (None,):resolver.resolve(mat)
                self.cache.surface_materials=resolver
                self.surface_signature=surface_signature
            if context.scene.hl_preview_bounce_samples>0 or context.scene.hl_preview_surface_cache:
                from .previewmaterials import BounceMaterials
                from .ops import game_root,game_content
                import os
                settings=context.scene.hammerless
                signature=(settings.game_root,settings.default_material,tuple(
                    (key,id(g),g.shadow_revision,g.use_uv,tuple(m.as_pointer() if m else 0 for m in g.materials))
                    for key,g in self.cache.geometry.items()))
                if self.bounce_materials is None or signature!=self.bounce_signature:
                    if self.bounce_materials is None or self.bounce_signature is None or signature[:2]!=self.bounce_signature[:2]:
                        root=game_root(context)
                        self.bounce_materials=BounceMaterials(game_content(root),os.path.join(root,'left4dead2') if root else None,
                                                             settings.default_material)
                    # Resolve CPU/image/content data in the update callback, never
                    # during drawing. The draw only uploads the prepared snapshot.
                    self.bounce_materials.resolve(None)
                    for g in self.cache.geometry.values():
                        for mat in g.materials or (None,):self.bounce_materials.resolve(mat)
                    self.bounce_signature=signature
                    self.bounce_pending=True
            self.scene_signature=(self.surface_signature,self.material_revision,self.cache.light_signature,self.cache.sky_color,
                tuple((key,id(g),g.shadow_revision,g.use_uv) for key,g in self.cache.geometry.items()),
                tuple((key,tuple(v for row in matrix for v in row)) for key,matrix in self.cache.instances))
            if context.scene.hl_preview_surface_cache:
                from .previewlivecache import LiveSurfaceCache
                if self.surface_cache is None:self.surface_cache=LiveSurfaceCache()
                self.surface_cache.update(context,depsgraph,self.cache,self.material_revision)
            else:self.surface_cache=None
            self.error = ''
        except Exception as exc:
            self.error = str(exc)
            self.cache = SceneCache()
            self.bounce_signature = None
            self.update_guard.clear()
        self.tag_redraw()

    def view_draw(self, context, depsgraph):
        gpu.state.active_framebuffer_get().clear(color=(0.025, 0.025, 0.025, 1), depth=1.0)
        if self.error:
            self.update_stats('L4D2 Preview', self.error)
            return
        try:
            samples=context.scene.hl_preview_bounce_samples
            bounce=samples>0
            area_batches=any(len(l)>4 and len(l[4])>3 and l[4][3]>0 for l in self.cache.lights)
            progressive=bounce or area_batches or any(c>0 for c in self.cache.sky_color)
            shader_mode=(bounce,progressive)
            if self.shader is None or self.shader_bounce!=shader_mode:
                self.shader = diagnostic_shader(lit=True, textured=True, shadows=True, display=not progressive,bounce=bounce)
                self.shader_bounce=shader_mode
            if (bounce or self.surface_cache is not None) and self.bounce_pending:
                from .previewtrace import TraceCache
                if self.cache.trace is None:self.cache.trace=TraceCache()
                self.cache.trace.update_bounce_materials(self.cache,self.bounce_materials)
                self.bounce_pending=False
                self.bounce_uploads+=1
            matrix=context.region_data.perspective_matrix.copy()
            excluded=self.surface_cache.keys if self.surface_cache else set()
            remaining=self.surface_cache.remaining if self.surface_cache else None
            if self.surface_cache:
                changed=self.surface_cache.refine(self.cache,(self.scene_signature,self.bounce_uploads),
                    frames=context.scene.hl_preview_refine_frames if progressive else 1,
                    rays=samples,bounces=context.scene.hl_preview_max_bounces,view_projection=matrix)
                if changed:self.tag_redraw()
            if progressive:
                from .previewhistory import FrameHistory
                if self.history is None:self.history=FrameHistory()
                if self.history_depth_shader is None:self.history_depth_shader=depth_shader()
                signature=(self.scene_signature,samples,context.scene.hl_preview_max_bounces,self.bounce_uploads,self.surface_cache.generation if self.surface_cache else None,tuple(v for row in matrix for v in row))
                def sample(frame):
                    if self.surface_cache:self.surface_cache.draw_depth(matrix)
                    if len(self.cache.instances)>256:
                        gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True)
                        draw_depth(self.cache,self.history_depth_shader,matrix,exclude_keys=excluded,raster_geometry=remaining)
                    draw_geometry(self.cache,self.shader,matrix,lit=True,textured=True,shadows=True,
                                  bounce_samples=samples if bounce else None,sampled_bounce_sky=True,sampled_bounce_area=True,bounce_seed=frame,bounce_depth=context.scene.hl_preview_max_bounces,
                                  primary_sky_index=(frame*9)%162,primary_sky_count=9,primary_area_phase=frame%8 if area_batches else -1,exclude_keys=excluded,raster_geometry=remaining)
                def restore_depth():
                    if self.surface_cache:self.surface_cache.draw_depth(matrix)
                    draw_depth(self.cache,self.history_depth_shader,matrix,exclude_keys=excluded,raster_geometry=remaining)
                frames=self.history.render(signature,sample,restore_depth,
                                           exposure=context.scene.hl_preview_exposure,max_frames=context.scene.hl_preview_refine_frames)
                if frames<context.scene.hl_preview_refine_frames:self.tag_redraw()
            else:
                self.history=None
                if self.surface_cache:self.surface_cache.draw_depth(matrix)
                draw_geometry(self.cache,self.shader,matrix,lit=True,textured=True,shadows=True,display=True,
                              exposure=context.scene.hl_preview_exposure,exclude_keys=excluded,raster_geometry=remaining)
            if self.surface_cache:self.surface_cache.draw(matrix,context.scene.hl_preview_exposure)
        except Exception as exc:
            self.error=str(exc)
            self.update_stats('L4D2 Preview GPU error', str(exc))
            return
        warnings = (self.surface_cache.warnings if self.surface_cache else [])+self.cache.warnings+(self.cache.surface_materials.warnings if self.cache.surface_materials else [])+(self.bounce_materials.warnings if bounce and self.bounce_materials else [])
        warning = '; '.join(warnings[:3])
        if len(warnings)>3:warning+=f'; {len(warnings)-3} more warnings'
        self.update_stats('L4D2 Preview', warning or ('Experimental bounced light' if bounce else 'Direct light and opaque shadows'))

    def render(self, depsgraph):
        self.report({'ERROR'}, 'L4D2 Preview is a viewport prototype; final rendering is not implemented.')


class HAMMERLESS_OT_refresh_preview_materials(bpy.types.Operator):
    bl_idname = 'hammerless.refresh_preview_materials'
    bl_label = 'Refresh Game Materials'
    bl_description = 'Reread game textures and materials, then restart preview lighting. Does not reload or overwrite your Blender image edits'

    def execute(self, context):
        global _material_refresh_revision
        from . import ops
        root = ops.game_root(context)
        ops._content_cache.pop(root, None)
        _material_refresh_revision += 1
        context.scene.update_tag()
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}


class HAMMERLESS_PT_l4d2_preview(bpy.types.Panel):
    bl_label = 'L4D2 Preview - Experimental'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'render'

    @classmethod
    def poll(cls, context):
        return context.scene.render.engine == HAMMERLESS_L4D2Preview.bl_idname

    def draw(self, context):
        col = self.layout.column()
        col.label(text='Point/spot/sun/area; opaque GPU shadows', icon='INFO')
        col.label(text='Blender emission lights nearby surfaces.')
        col.label(text='Base textures; linear irradiance / 255.')
        col.operator('hammerless.refresh_preview_materials', icon='FILE_REFRESH')
        col.prop(context.scene,'hl_preview_surface_cache')
        col.prop(context.scene,'hl_preview_exposure')
        col.prop(context.scene,'hl_preview_bounce_samples')
        col.prop(context.scene,'hl_preview_max_bounces')
        col.prop(context.scene,'hl_preview_refine_frames')
        col.label(text='More rays and bounces cost time.')
        col.label(text='Fixed sRGB display; game auto-exposure not simulated.')
        col.label(text='Flat sky ambient uses 162 visibility directions.')
        col.label(text='HDRI, alpha and advanced shaders pending.')


CLASSES = (HAMMERLESS_L4D2Preview, HAMMERLESS_OT_refresh_preview_materials, HAMMERLESS_PT_l4d2_preview)


def register():
    bpy.types.Scene.hl_preview_surface_cache = bpy.props.BoolProperty(
        name='Surface Lighting Cache (Experimental)',default=False,
        description='Cache brush lighting at exported lightmap density. Charts potentially visible to the camera refine first; offscreen geometry still casts shadows and contributes bounced light. Scene edits restart lighting. Props and unsupported faces use per-pixel lighting. One million cached sample limit')
    bpy.types.Scene.hl_preview_max_bounces = bpy.props.IntProperty(
        name='Max Bounces',default=1,min=1,max=8,
        description='Maximum diffuse reflections per preview ray. Higher values add light carried around more corners, but cost more time. Does not change bake settings')
    bpy.types.Scene.hl_preview_refine_frames = bpy.props.IntProperty(
        name='Refinement Frames',default=72,min=1,max=512,
        description='Average fresh bounce and sky samples. Multiples of 72 complete both eight-frame area and eighteen-frame sky cycles. With surface caching, each chart keeps its progress while navigating; newly revealed charts begin refining. Scene edits restart lighting; uncached geometry also restarts on camera changes')
    bpy.types.Scene.hl_preview_bounce_samples = bpy.props.IntProperty(
        name='Bounce Rays',default=0,min=0,max=128,
        description='Experimental indirect light: 0 disables it. Try 8 or 16 paths per frame; larger values slow interaction. Samples accumulate while still. No denoising')
    bpy.types.Scene.hl_preview_exposure = bpy.props.FloatProperty(
        name='Preview Exposure',default=0.,min=-10.,max=10.,
        description='Display-only exposure in stops: +1 doubles brightness. Does not change exported lights or simulate game auto-exposure')
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    del bpy.types.Scene.hl_preview_surface_cache
    del bpy.types.Scene.hl_preview_max_bounces
    del bpy.types.Scene.hl_preview_refine_frames
    del bpy.types.Scene.hl_preview_bounce_samples
    del bpy.types.Scene.hl_preview_exposure
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
