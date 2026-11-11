"""Experimental L4D2 unshadowed point irradiance; not full lighting parity.

Caches evaluated triangles in object space. Transforms and camera changes do not
re-extract meshes. No scene mutation, bake invocation, timers or global handlers.
GPU resources belong to an engine instance and are released with that instance.
"""
from dataclasses import dataclass, field
import operator
import math
import time

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
    # SceneCache supplies an evaluated object. Its mesh already contains applied
    # modifiers; only Edit Mode needs a temporary mesh to materialize live BMesh.
    temporary = bool(getattr(obj.original.data, 'is_editmode', False))
    mesh = obj.to_mesh() if temporary else obj.data
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
        # Integer-array indexing already owns a copy, independent of Blender data.
        geometry = Geometry(positions[vertices], normals.reshape(-1, 3)[loops])
        uv = np.zeros((len(mesh.loops), 2), dtype=np.float32)
        if mesh.uv_layers.active:
            mesh.uv_layers.active.data.foreach_get('uv', uv.ravel())
        geometry.uvs = uv[loops]
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
        if temporary:obj.to_mesh_clear()


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


def diagnostic_shader(lit=False, textured=False, shadows=False, display=False, bounce=False, surface_samples=False, receiver_data=False):
    if receiver_data and (not lit or not textured or shadows or bounce or display):
        raise ValueError("Receiver data requires unlit textured raster input")
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
    if lit and not receiver_data:
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
        info.push_constant('INT', 'bounceSampleOffset')
        info.push_constant('INT', 'bounceSampleCount')
        info.push_constant('INT', 'bounceSampleStride')
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
    if receiver_data:
        info.fragment_out(1,'VEC4','receiverPosition')
        info.fragment_out(2,'VEC4','receiverNormal')
        info.fragment_source(r"""void main(){
            vec2 coords=surfaceUV;
            if(useUV==0 && paintedUV<0.5){
                vec3 fn=surfaceNormal,a=abs(fn),p=worldPosition*unitScale;
                if(a.z>=a.x && a.z>=a.y)coords=vec2(fn.z>=0.0?p.x:-p.x,p.y);
                else if(a.x>=a.y)coords=vec2(fn.x>0.0?p.y:-p.y,p.z);
                else coords=vec2(fn.y>0.0?-p.x:p.x,p.z);
                coords/=textureSizeScale.xy*textureSizeScale.z;
            }
            vec3 albedo=baseColor;
            if(hasTexture!=0)albedo=texture(baseTexture,fract(coords)).rgb;
            color=vec4(albedo,float(surfaceUnlit));
            receiverPosition=vec4(worldPosition+normalize(surfaceNormal)*(receiverOffset/unitScale),1.0);
            receiverNormal=vec4(normalize(worldNormal),0.0);
        }""")
        return gpu.shader.create_from_info(info)
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


def bounce_slice_stride(total):
    """Spread early slices while permuting exactly the configured path indices.

    Coprimality guarantees complete coverage. The half-turn choice is only a
    simple early-preview heuristic, not an optimal sampling sequence.
    """
    total=operator.index(total)
    if total<=1:return 1
    stride=total//2+1
    while math.gcd(stride,total)!=1:stride+=1
    return stride


def draw_geometry(cache, shader, view_projection, lit=False, textured=False, shadows=False, display=False, exposure=0.0, bounce_samples=None, sampled_bounce_sky=False, bounce_seed=0, bounce_depth=1, primary_sky_index=-1, primary_sky_count=162, primary_area_phase=-1, sampled_bounce_area=False, lighting_samples=None, exclude_keys=(), raster_geometry=None, bounce_sample_offset=0, bounce_sample_count=None, bounce_sample_stride=1, receiver_data=False):
    """Draw to the bound framebuffer; caller owns clearing and viewport setup."""
    old_depth = gpu.state.depth_test_get()
    old_mask = gpu.state.depth_mask_get()
    old_blend = gpu.state.blend_get()
    texture_handles = {}  # Per draw: no cross-frame image invalidation required.
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
            total=max(0,int(bounce_samples));offset=operator.index(bounce_sample_offset)
            count=total-offset if bounce_sample_count is None else operator.index(bounce_sample_count)
            stride=operator.index(bounce_sample_stride)
            if offset<0 or count<0 or offset+count>total:
                raise ValueError('Bounce sample slice must lie inside configured ray count')
            if stride<=0 or (total>0 and math.gcd(stride,total)!=1):
                raise ValueError('Bounce sample stride must be positive and coprime to ray count')
            shader.uniform_int('bounceSamples',total)
            shader.uniform_int('bounceSampleOffset',offset)
            shader.uniform_int('bounceSampleCount',count)
            shader.uniform_int('bounceSampleStride',stride%total if total else 1)
            shader.uniform_int('bounceSkySampled',int(sampled_bounce_sky))
            shader.uniform_int('bounceAreaSampled',int(sampled_bounce_area))
            shader.uniform_int('bounceSeed',int(bounce_seed))
            shader.uniform_int('bounceDepth',max(1,min(8,int(bounce_depth))))
            shader.uniform_sampler('traceSurfaces',cache.trace.surfaces)
        if receiver_data:
            shader.uniform_float('unitScale', cache.unit_scale)
        if lit and not receiver_data:
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
                    image_key=img.as_pointer() if img else 0
                    texture=texture_handles.get(image_key)
                    if texture is None:
                        texture=gpu.texture.from_image(img) if img else cache.upload_lights()
                        texture_handles[image_key]=texture
                    shader.uniform_sampler('baseTexture', texture)
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
        self.interaction_signature = None
        self.interaction_until = 0.0
        self.interacting = False
        self.interaction_shader = None
        self.native_view = None
        self.native_surface = None
        self.surface_first_draw = True

    def __del__(self):
        native = getattr(self, 'native_view', None)
        if native is not None:
            try:
                native.close()
            except Exception:
                # Module globals may already be gone during interpreter exit;
                # the native client's atexit and owned Job also close its worker.
                pass

    def _prepare_native(self, context):
        if not context.scene.hl_preview_native:
            if self.native_view is not None:
                self.native_view.close()
                self.native_view = None
            return
        from .previewnativeview import NativeView
        if self.native_view is None:
            self.native_view = NativeView(owner=id(self))
        signature = self.scene_signature
        tokens=((signature[:2], signature[4:]),self.cache.light_signature)
        pending=self.native_view._pending_prepare
        current=(pending[2],pending[3]) if pending is not None else (self.native_view.geometry_token,self.native_view.lights_token)
        if tokens==current:return
        if self.native_surface is not None:self.native_surface.pause(self.native_view)
        self.native_view.stage(self.cache, self.bounce_materials,
                               *tokens)

    def _vrad_ready(self, context):
        """Built-Map Lighting: the last build's map, lit by the light compiler kept running (livelighting.py), is what
        this viewport shows. Starts the session and loads the map when needed; False when there is no build to show
        (the engine's own lighting is used then). Never raises."""
        if not getattr(context.scene, 'hl_preview_vrad', False):
            return False
        try:
            import os
            from . import lightview, livelighting
            path = lightview.bsp_path(context)
            if not os.path.exists(path):
                _VRAD['note'] = 'No build yet: Build the map once to see its baked-quality lighting here'
                return False
            stamp = (os.path.normcase(path), os.path.getmtime(path))
            if not livelighting.running():
                if _VRAD['failed'] == stamp:      # (it couldn't start on this build: not again)
                    return False
                err = livelighting.start(context, for_engine=True)
                if err:
                    _VRAD['failed'], _VRAD['note'] = stamp, err
                    return False
            state = lightview._state
            if state['data'] is None or os.path.normcase(state['path'] or '') != stamp[0]:
                err = lightview.load(context, path)
                if err:
                    _VRAD['failed'], _VRAD['note'] = stamp, err
                    return False
            _VRAD['note'] = ''
            return True
        except Exception as exc:
            _VRAD['note'] = f'Built-map lighting unavailable: {exc}'
            return False

    def _vrad_draw(self, context):
        """The compiled map's faces (game textures x the live lightmaps), its props and the sky behind, drawn as the
        Lighting view's overlay draws them. Blender shows what a render engine draws through the scene's view
        settings, which neutral_view keeps at the plain sRGB display while this is the scene's engine; that display
        codes the colours as the overlays' buffer does (measured, see docs/dev/live-lighting.md), so the picture is
        the Lighting view's."""
        from . import lightview, lighttextures, livelighting, skyview
        state = lightview._state
        s = context.scene.hammerless
        if state['texture'] is None:
            lightview._upload()
        if state['tbatches'] is None or state.get('tscale') != s.units_per_meter:
            state['tbatches'] = lighttextures.build(state['data'], state['props'], s.units_per_meter,
                                                    state['content'], state['game_dir'])
            state['tscale'] = s.units_per_meter
        rv3d = context.region_data
        gpu.matrix.push()
        gpu.matrix.push_projection()
        try:
            gpu.matrix.load_matrix(rv3d.view_matrix)
            gpu.matrix.load_projection_matrix(rv3d.window_matrix)
            skyview.draw_sky(context)
            gpu.state.blend_set('NONE')
            gpu.state.depth_mask_set(True)
            gpu.state.depth_test_set('LESS_EQUAL')
            gpu.state.face_culling_set('NONE')
            lighttextures.draw(state['tbatches'], state['texture'], 2.0 ** context.scene.hl_preview_exposure, 0.0, True)
        finally:
            gpu.state.depth_test_set('NONE')
            gpu.matrix.pop_projection()
            gpu.matrix.pop()
        note = livelighting.status().replace('Live Lighting: ', 'Lighting: ')
        if state['edited']:
            note += ' | walls changed since the build: Build to show them'
        self.update_stats('L4D2 Preview', note)
        if livelighting.busy():
            self.tag_redraw()

    def view_update(self, context, depsgraph):
        neutral_view_check(context.scene)
        if self._vrad_ready(context):   # (the built map is shown: nothing of the scene to extract)
            self.error = ''
            self.tag_redraw()
            return
        try:
            if self.update_guard.check(context,depsgraph,self.surface_cache,_material_refresh_revision):
                # Only lighting changes: retain geometry/material/chart snapshots.
                # The explicit guard also catches custom changes Blender may not tag.
                self.cache.update_lights(depsgraph)
                old=self.scene_signature
                self.scene_signature=(old[0],old[1],self.cache.light_signature,self.cache.sky_color,old[4],old[5])
                self._prepare_native(context)
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
            if context.scene.hl_preview_bounce_samples>0 or context.scene.hl_preview_surface_cache or context.scene.hl_preview_native:
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
            self._prepare_native(context)
            if context.scene.hl_preview_surface_cache:
                from .previewlivecache import LiveSurfaceCache
                if self.surface_cache is None:
                    self.surface_cache=LiveSurfaceCache()
                    self.surface_first_draw=True
                if context.scene.hl_preview_native:
                    self.surface_cache.stage(context,depsgraph,self.cache,self.material_revision)
                else:self.surface_cache.update(context,depsgraph,self.cache,self.material_revision)
            else:self.surface_cache=None
            self.error = ''
        except Exception as exc:
            if self.native_view is not None:self.native_view.pause()
            self.error = str(exc)
            self.cache = SceneCache()
            self.bounce_signature = None
            self.update_guard.clear()
        self.tag_redraw()

    def view_draw(self, context, depsgraph):
        gpu.state.active_framebuffer_get().clear(color=(0.025, 0.025, 0.025, 1), depth=1.0)
        if self._vrad_ready(context):
            try:
                self._vrad_draw(context)
                return
            except Exception as exc:    # (the engine's own lighting instead, until the next build)
                import os
                from . import lightview
                path = lightview.bsp_path(context)
                _VRAD['note'] = f'Built-map lighting unavailable: {exc}'
                _VRAD['failed'] = (os.path.normcase(path), os.path.getmtime(path)) if os.path.exists(path) else None
                from . import livelighting
                if livelighting.for_engine():
                    livelighting.stop(reload=False)
                gpu.state.active_framebuffer_get().clear(color=(0.025, 0.025, 0.025, 1), depth=1.0)
        if self.error:
            self.update_stats('L4D2 Preview', self.error)
            return
        try:
            matrix=context.region_data.perspective_matrix.copy()
            interaction_signature=(self.scene_signature,self.cache.unit_scale,tuple(v for row in matrix for v in row),
                                   tuple(gpu.state.viewport_get()[2:]))
            now=time.monotonic()
            navigating=getattr(context.region_data,'is_navigating',False)
            playing=getattr(context.screen,'is_animation_playing',False)
            if ((self.interaction_signature is not None and interaction_signature!=self.interaction_signature)
                    or navigating or playing):
                self.interaction_until=now+0.15
            self.interaction_signature=interaction_signature
            self.interacting=now<self.interaction_until
            if (context.scene.hl_preview_native and self.native_view is not None
                    and self.surface_cache is not None):
                from .previewnativesurface import NativeSurfaceCache
                if self.native_surface is None:
                    self.native_view.pause()
                    self.native_surface=NativeSurfaceCache()
                # Show the scene before chart construction. Subsequent idle frames
                # admit visible receiver objects; the complete ray scene is retained.
                if not self.surface_first_draw and not self.interacting and self.native_surface.request is None:
                    self.surface_cache.advance(context,depsgraph,self.cache,self.material_revision,matrix)
                self.surface_first_draw=False
                preparing=self.surface_cache.has_pending(matrix)
                pending,status=self.native_surface.step(
                    self.native_view,self.surface_cache,self.cache,self.scene_signature,
                    rays=context.scene.hl_preview_bounce_samples,
                    bounces=context.scene.hl_preview_max_bounces,
                    frames=context.scene.hl_preview_refine_frames,
                    view_projection=matrix,paused=self.interacting,yield_after_result=preparing)
                # Each triangle belongs to one display path. Drawing the direct
                # mesh beneath cached copies causes precision-dependent z-fighting.
                if self.interaction_shader is None:
                    self.interaction_shader=diagnostic_shader(lit=True,textured=True,display=True)
                draw_geometry(self.cache,self.interaction_shader,matrix,lit=True,textured=True,
                              display=True,exposure=context.scene.hl_preview_exposure,
                              exclude_keys=self.native_surface.exclude_keys,
                              raster_geometry=self.native_surface.remaining)
                self.native_surface.draw(matrix,context.scene.hl_preview_exposure)
                if pending or self.interacting or preparing:self.tag_redraw()
                self.update_stats('L4D2 Preview',status)
                return
            if self.native_surface is not None:
                if self.native_view is not None:self.native_surface.pause(self.native_view)
                self.native_surface=None
            if self.interacting:
                if self.native_view is not None:self.native_view.pause()
                # Current geometry and depth, without tracing or touching the
                # accumulation. Never exclude surface-cache receivers here.
                if self.interaction_shader is None:
                    self.interaction_shader=diagnostic_shader(lit=True,textured=True,display=True)
                draw_geometry(self.cache,self.interaction_shader,matrix,lit=True,textured=True,
                              display=True,exposure=context.scene.hl_preview_exposure)
                self.tag_redraw()  # Ensures refinement resumes after input stops.
                self.update_stats('L4D2 Preview','Interacting: ray-traced lighting paused')
                return
            samples=context.scene.hl_preview_bounce_samples
            bounce=samples>0
            if context.scene.hl_preview_native and self.native_view is not None:
                signature=(self.scene_signature,samples,context.scene.hl_preview_max_bounces,
                           tuple(v for row in matrix for v in row),tuple(gpu.state.viewport_get()[2:]))
                displayed,pending,status=self.native_view.draw(
                    self.cache,matrix,signature,rays=samples,depth=context.scene.hl_preview_max_bounces,
                    frames=context.scene.hl_preview_refine_frames,exposure=context.scene.hl_preview_exposure)
                if not displayed:
                    if self.interaction_shader is None:
                        self.interaction_shader=diagnostic_shader(lit=True,textured=True,display=True)
                    draw_geometry(self.cache,self.interaction_shader,matrix,lit=True,textured=True,
                                  display=True,exposure=context.scene.hl_preview_exposure)
                if pending:self.tag_redraw()
                self.update_stats('L4D2 Preview',status)
                return
            if not context.scene.hl_preview_native and self.native_view is not None:
                self.native_view.close()
                self.native_view=None
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
                # Input uses the cheap interaction path above. While idle,
                # complete the ray set once to avoid repeating primary lighting
                # and rasterization for every two-ray slice.
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
                if frames<context.scene.hl_preview_refine_frames or self.history.batch_index:self.tag_redraw()
            else:
                self.history=None
                if self.surface_cache:self.surface_cache.draw_depth(matrix)
                draw_geometry(self.cache,self.shader,matrix,lit=True,textured=True,shadows=True,display=True,
                              exposure=context.scene.hl_preview_exposure,exclude_keys=excluded,raster_geometry=remaining)
            if self.surface_cache:self.surface_cache.draw(matrix,context.scene.hl_preview_exposure)
        except Exception as exc:
            self.error=str(exc)
            if self.native_view is not None:self.native_view.pause()
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
        col.prop(context.scene,'hl_preview_vrad')
        if context.scene.hl_preview_vrad:
            from . import livelighting
            note = _VRAD['note'] or livelighting.status().replace('Live Lighting: ', 'Lighting: ')
            if note:col.label(text=note, icon='ERROR' if _VRAD['note'] else 'INFO')
            col.prop(context.scene,'hl_preview_exposure')
            if not _VRAD['note']:
                col.label(text="Shows the last build's map; lights are followed live.")
                col.label(text='Walls moved since need a Build.')
                return
            col.separator()
            col.label(text='Until then, the experimental preview lighting:')
        col.label(text='Point/spot/sun/area; opaque GPU shadows', icon='INFO')
        col.label(text='Blender emission lights nearby surfaces.')
        col.label(text='Base textures; linear irradiance / 255.')
        col.operator('hammerless.refresh_preview_materials', icon='FILE_REFRESH')
        col.prop(context.scene,'hl_preview_native')
        col.prop(context.scene,'hl_preview_surface_cache')
        if context.scene.hl_preview_native and context.scene.hl_preview_surface_cache:
            col.label(text='Brush lighting stays visible while navigating.')
            col.label(text='Props/unsupported faces: direct preview only.',icon='INFO')
        col.prop(context.scene,'hl_preview_exposure')
        col.prop(context.scene,'hl_preview_bounce_samples')
        col.prop(context.scene,'hl_preview_max_bounces')
        col.prop(context.scene,'hl_preview_refine_frames')
        col.label(text='More rays and bounces cost time.')
        col.label(text='Fixed sRGB display; game auto-exposure not simulated.')
        col.label(text='Flat sky ambient uses 162 visibility directions.')
        col.label(text='HDRI, alpha and advanced shaders pending.')


CLASSES = (HAMMERLESS_L4D2Preview, HAMMERLESS_OT_refresh_preview_materials, HAMMERLESS_PT_l4d2_preview)


def _blender_panels():
    """Blender's own panels that show for EEVEE (materials, lights, world, mesh data, output...): they show for this
    engine too, so a material's or a light's settings stay editable while it is the scene's engine. Not EEVEE's
    render and view layer settings: those are its own."""
    skip = ('bl_ui.properties_render', 'bl_ui.properties_view_layer')
    for panel in bpy.types.Panel.__subclasses__():
        engines = getattr(panel, 'COMPAT_ENGINES', None)
        if engines is None or getattr(panel, '__module__', '') in skip:
            continue
        if 'BLENDER_EEVEE_NEXT' in engines or 'BLENDER_EEVEE' in engines:
            yield panel


_VRAD = {'failed': None, 'note': ''}     # (the build Built-Map Lighting couldn't start on; why it isn't shown)


def _vrad_toggled(self, context):
    from . import livelighting
    _VRAD['failed'] = None
    if not self.hl_preview_vrad and livelighting.running() and livelighting.for_engine():
        livelighting.stop()
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ---------------------------------------------------------------- the scene's view settings
# Blender turns what a render engine draws into display colours with the scene's Colour Management settings: its
# view transform, look, exposure, gamma and curves. The engine's picture must be the game's, which those would
# change (measured in 4.4: exposure 2 doubles its brightness; AgX and Filmic can't be undone, saturated colours come
# back up to 0.64 off). So while a scene's engine is this one its view settings are kept neutral (the plain sRGB
# display), the ones it had kept on the scene ("hl_view_saved") and put back when it gets another engine.
NEUTRAL_VIEW = {'view_transform': 'Standard', 'look': 'None', 'exposure': 0.0, 'gamma': 1.0, 'use_curve_mapping': False}
_msgbus_owner = object()


def _view_is_neutral(scene) -> bool:
    vs = scene.view_settings
    for key, want in NEUTRAL_VIEW.items():
        have = getattr(vs, key, want)
        if (abs(have - want) > 1e-6) if isinstance(want, float) else (have != want):
            return False
    return True


def neutral_view() -> None:
    """Every scene's view settings as its engine needs them: neutral for this engine's (the old ones kept), the kept
    ones back for the others. Changes scenes: never called while drawing (neutral_view_check asks for it)."""
    _VRAD['view_timer'] = False
    for scene in bpy.data.scenes:
        vs = scene.view_settings
        saved = scene.get('hl_view_saved')
        if scene.render.engine == HAMMERLESS_L4D2Preview.bl_idname:
            if _view_is_neutral(scene):
                continue
            if saved is None:
                scene['hl_view_saved'] = {key: getattr(vs, key) for key in NEUTRAL_VIEW if hasattr(vs, key)}
            for key, want in NEUTRAL_VIEW.items():
                try:
                    setattr(vs, key, want)
                except Exception:       # (a configuration without that view or look: as near as it goes)
                    pass
        elif saved is not None:
            for key in NEUTRAL_VIEW:
                try:
                    if key in saved:
                        setattr(vs, key, saved[key])
                except Exception:
                    pass
            del scene['hl_view_saved']
    return None


def neutral_view_check(scene=None) -> None:
    """Ask for neutral_view when a scene needs it (safe from draw and update callbacks: the change is made by a
    timer)."""
    if _VRAD.get('view_timer'):
        return
    if scene is not None and scene.render.engine == HAMMERLESS_L4D2Preview.bl_idname and _view_is_neutral(scene):
        return
    _VRAD['view_timer'] = True
    bpy.app.timers.register(neutral_view, first_interval=0.0)


def _engine_changed(*_args):
    neutral_view_check()


@bpy.app.handlers.persistent
def _view_on_load(*_args):
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.RenderSettings, 'engine'), owner=_msgbus_owner, args=(), notify=_engine_changed)
    neutral_view_check()


def vrad_view(context) -> bool:
    """Whether this 3D viewport is showing the built map through the engine (the Lighting view's overlay stays out)."""
    space = getattr(context, 'space_data', None)
    return (space is not None and space.type == 'VIEW_3D' and space.shading.type == 'RENDERED'
            and context.scene.render.engine == HAMMERLESS_L4D2Preview.bl_idname
            and getattr(context.scene, 'hl_preview_vrad', False))


def register():
    bpy.types.Scene.hl_preview_vrad = bpy.props.BoolProperty(
        name='Built-Map Lighting',default=True,update=_vrad_toggled,
        description="Show the last build's map lit by the light compiler, kept running: the lighting is a bake's, and follows the lights as you edit them. Walls moved since the build show after a Build. Off: the experimental preview lighting on the scene as it is")
    bpy.types.Scene.hl_preview_native = bpy.props.BoolProperty(
        name='Hardware Ray Tracing (Experimental)',default=False,
        description='Use a separate Vulkan hardware ray tracing worker. Requires a ray-query capable GPU. Pauses during interaction. Enable Surface Lighting Cache to retain brush lighting while navigating')
    bpy.types.Scene.hl_preview_surface_cache = bpy.props.BoolProperty(
        name='Surface Lighting Cache (Experimental)',default=False,
        description='Cache brush lighting at exported lightmap density. Camera movement retains lighting. Offscreen geometry still affects lighting. With Hardware Ray Tracing, previous brush lighting remains visible during interaction and updates; props and unsupported faces currently use direct preview only. One million cached sample limit')
    bpy.types.Scene.hl_preview_max_bounces = bpy.props.IntProperty(
        name='Max Bounces',default=1,min=1,max=8,
        description='Maximum diffuse reflections per preview ray. Higher values add light carried around more corners, but cost more time. Does not change bake settings')
    bpy.types.Scene.hl_preview_refine_frames = bpy.props.IntProperty(
        name='Refinement Frames',default=72,min=1,max=512,
        description='Average fresh bounce and sky samples. Multiples of 72 complete both eight-frame area and eighteen-frame sky cycles. With surface caching, each chart keeps its progress while navigating; newly revealed charts begin refining. Scene edits restart lighting; uncached geometry also restarts on camera changes')
    bpy.types.Scene.hl_preview_bounce_samples = bpy.props.IntProperty(
        name='Bounce Rays',default=0,min=0,max=128,
        description='Experimental indirect light: 0 disables it. Rays per refinement step while idle. Higher values take longer per update but reduce sampling noise. Ray-traced lighting pauses during navigation and scene changes, then resumes after 150 ms idle. No denoising')
    bpy.types.Scene.hl_preview_exposure = bpy.props.FloatProperty(
        name='Preview Exposure',default=0.,min=-10.,max=10.,
        description='Display-only exposure in stops: +1 doubles brightness. Does not change exported lights or simulate game auto-exposure')
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for panel in _blender_panels():
        panel.COMPAT_ENGINES.add(HAMMERLESS_L4D2Preview.bl_idname)
    bpy.app.handlers.load_post.append(_view_on_load)
    _view_on_load()


def unregister():
    if _view_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_view_on_load)
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    try:                                # (the scenes' own view settings back: the engine is going)
        for scene in bpy.data.scenes:
            saved = scene.get('hl_view_saved')
            if saved is None:
                continue
            for key in NEUTRAL_VIEW:
                try:
                    if key in saved:
                        setattr(scene.view_settings, key, saved[key])
                except Exception:
                    pass
            del scene['hl_view_saved']
    except Exception:
        pass
    for panel in _blender_panels():
        panel.COMPAT_ENGINES.discard(HAMMERLESS_L4D2Preview.bl_idname)
    from .previewnative import shutdown
    shutdown()
    del bpy.types.Scene.hl_preview_native
    del bpy.types.Scene.hl_preview_vrad
    del bpy.types.Scene.hl_preview_surface_cache
    del bpy.types.Scene.hl_preview_max_bounces
    del bpy.types.Scene.hl_preview_refine_frames
    del bpy.types.Scene.hl_preview_bounce_samples
    del bpy.types.Scene.hl_preview_exposure
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
