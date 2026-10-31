"""Blender area lights and connected material emission -> bake light samples."""
import math
import numpy as np
from mathutils import Vector
from ..core.surfacelights import (SPACING, MAX_MESH_SAMPLES, rectangle_samples,
                                  triangle_samples, light_sample)


class UnsupportedEmission(ValueError):
    pass


class MaterialEmission:
    """Compile the active surface's emission, not disconnected shader nodes.

    Common uniform and UV-textured emission is evaluated without a render bake.
    Unsupported linked expressions are reported rather than silently ignored.
    """
    def __init__(self, material):
        self.images = {}
        self.enabled = False
        self.active = set()
        self.function = lambda uv: np.zeros(3)
        if not material or not material.use_nodes or not material.node_tree:
            return
        outputs = [n for n in material.node_tree.nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output]
        if outputs:
            self.function = self.shader(outputs[0].inputs['Surface'], {})

    def __call__(self, uv):
        if uv is None:
            # No UV map: use an area average, rather than the (often black) texture corner.
            return np.mean([np.maximum(0,self.function(((x+.5)/8,(y+.5)/8)))
                            for y in range(8) for x in range(8)],axis=0)
        return np.maximum(0, self.function(uv))

    def linked(self, socket, groups, shader=False):
        if not socket.is_linked:
            if shader:
                return lambda uv: np.zeros(3)
            value = np.array(socket.default_value, dtype=float)
            return lambda uv: value
        link = socket.links[0]
        node, output = link.from_node, link.from_socket
        key = (node.as_pointer(), output.identifier)
        if key in self.active:
            raise UnsupportedEmission('cyclic emission node graph')
        self.active.add(key)
        try:
            if node.type == 'REROUTE':
                return self.linked(node.inputs[0], groups, shader)
            if node.type == 'GROUP_INPUT':
                if output.identifier not in groups:
                    raise UnsupportedEmission('unbound emission group input')
                outer, outer_groups = groups[output.identifier]
                return self.linked(outer, outer_groups, shader)
            if node.type == 'GROUP':
                boundary = {s.identifier: (s, groups) for s in node.inputs}
                target = next((n for n in node.node_tree.nodes if n.type == 'GROUP_OUTPUT' and n.is_active_output), None)
                if target is None:
                    return lambda uv: np.zeros(3) if shader else 0.0
                inner = next(s for s in target.inputs if s.identifier == output.identifier)
                return self.linked(inner, boundary, shader)
            if shader:
                return self.shader_node(node, groups)
            return self.value_node(node, output, groups)
        finally:
            self.active.remove(key)

    def shader(self, socket, groups):
        return self.linked(socket, groups, True)

    def shader_node(self, node, groups):
        if node.type in ('EMISSION', 'BSDF_PRINCIPLED'):
            c = node.inputs.get('Color' if node.type == 'EMISSION' else 'Emission Color')
            strength = node.inputs.get('Strength' if node.type == 'EMISSION' else 'Emission Strength')
            # Don't evaluate unused complex emission colors at zero strength.
            if strength is None or (not strength.is_linked and strength.default_value <= 0):
                return lambda uv: np.zeros(3)
            if not c.is_linked and not any(float(x)>0 for x in c.default_value[:3]):
                return lambda uv: np.zeros(3)
            self.enabled = True
            color = self.linked(c, groups)
            power = self.linked(strength, groups)
            def evaluate(uv):
                rgb=np.asarray(color(uv)).reshape(-1)
                if len(rgb)==1:rgb=np.repeat(rgb,3)
                return rgb[:3]*float(np.asarray(power(uv)).flat[0])
            return evaluate
        if node.type in ('ADD_SHADER', 'MIX_SHADER'):
            offset = 1 if node.type == 'MIX_SHADER' else 0
            a = self.shader(node.inputs[offset], groups)
            b = self.shader(node.inputs[offset + 1], groups)
            if not offset:
                return lambda uv: a(uv) + b(uv)
            factor = self.linked(node.inputs[0], groups)
            return lambda uv: a(uv) * (1 - float(np.clip(factor(uv), 0, 1))) + b(uv) * float(np.clip(factor(uv), 0, 1))
        if node.type.startswith('BSDF_') or node.type in ('HOLDOUT', 'BACKGROUND'):
            return lambda uv: np.zeros(3)
        raise UnsupportedEmission('unsupported surface node ' + node.bl_label)

    def value_node(self, node, output, groups):
        if node.type in ('RGB', 'VALUE'):
            value = np.array(output.default_value, dtype=float)
            return lambda uv: value
        if node.type == 'TEX_IMAGE':
            image = node.image
            if image is None or not image.size[0] or not image.size[1]:
                raise UnsupportedEmission('emission image is missing or empty')
            if node.projection != 'FLAT':
                raise UnsupportedEmission('emission images require Flat/UV projection')
            coords = self.linked(node.inputs['Vector'], groups) if node.inputs['Vector'].is_linked else lambda uv: uv
            ptr = image.as_pointer()
            if ptr not in self.images:
                data = np.empty(len(image.pixels), dtype=np.float32)
                image.pixels.foreach_get(data)
                self.images[ptr] = data.reshape(image.size[1], image.size[0], image.channels)
            pixels = self.images[ptr]
            def sample(uv):
                pos = np.asarray(coords(uv)).reshape(-1)
                u, v = pos[:2]
                if node.extension == 'CLIP' and not (0 <= u <= 1 and 0 <= v <= 1):
                    return 0.0 if output.name == 'Alpha' else np.zeros(4)
                if node.extension == 'REPEAT':
                    u, v = u % 1, v % 1
                else:
                    u, v = np.clip([u, v], 0, 1)
                h, w = pixels.shape[:2]
                # Bilinear sampling of Blender's scene-linear pixel data.
                x, y = u * w - .5, v * h - .5
                ix, iy = math.floor(x), math.floor(y)
                def tex(i, j):
                    return pixels[j % h, i % w] if node.extension == 'REPEAT' else pixels[min(h-1,max(0,j)),min(w-1,max(0,i))]
                if node.interpolation == 'Closest':
                    c = tex(round(x), round(y))
                else:
                    fx, fy = x - ix, y - iy
                    c = (tex(ix,iy)*(1-fx)+tex(ix+1,iy)*fx)*(1-fy)+(tex(ix,iy+1)*(1-fx)+tex(ix+1,iy+1)*fx)*fy
                if output.name == 'Alpha':
                    return c[3] if len(c) > 3 else 1.0
                return c
            return sample
        if node.type == 'UVMAP' or (node.type == 'TEX_COORD' and output.name == 'UV'):
            return lambda uv: np.array((*uv[:2], 0))
        if node.type == 'VALTORGB':
            factor = self.linked(node.inputs['Fac'], groups)
            return lambda uv: np.array(node.color_ramp.evaluate(float(np.asarray(factor(uv)).flat[0])))
        if node.type == 'MIX' and node.data_type == 'RGBA':
            factor = self.linked(node.inputs[0], groups)
            a,b = self.linked(node.inputs[6], groups),self.linked(node.inputs[7], groups)
            if node.blend_type not in ('MIX','MULTIPLY','ADD','SUBTRACT'):
                raise UnsupportedEmission('unsupported emission color mix ' + node.blend_type)
            def mix(uv):
                x,y=np.asarray(a(uv)),np.asarray(b(uv));t=float(np.asarray(factor(uv)).flat[0])
                if node.clamp_factor:t=float(np.clip(t,0,1))
                z={'MIX':lambda:y,'MULTIPLY':lambda:x*y,'ADD':lambda:x+y,'SUBTRACT':lambda:x-y}[node.blend_type]()
                value=x*(1-t)+z*t
                return np.clip(value,0,1) if node.clamp_result else value
            return mix
        if node.type == 'MAPPING':
            vector = self.linked(node.inputs['Vector'], groups)
            location = self.linked(node.inputs['Location'], groups)
            rotation = self.linked(node.inputs['Rotation'], groups)
            scale = self.linked(node.inputs['Scale'], groups)
            kind = node.vector_type
            def mapped(uv):
                from mathutils import Euler
                v = Vector(vector(uv)); loc = Vector(location(uv)); sc = np.array(scale(uv))
                rot = Euler(rotation(uv)).to_matrix()
                if kind == 'TEXTURE':
                    v = rot.inverted() @ (v-loc)
                    return np.divide(v,sc,out=np.zeros(3),where=abs(sc)>1e-12)
                if kind == 'NORMAL':
                    v = rot @ Vector(np.divide(v,sc,out=np.zeros(3),where=abs(sc)>1e-12))
                    return v.normalized()
                v = rot @ Vector(np.array(v)*sc)
                return v+loc if kind == 'POINT' else v
            return mapped
        if node.type == 'MIX_RGB':
            factor = self.linked(node.inputs[0], groups)
            a = self.linked(node.inputs[1], groups)
            b = self.linked(node.inputs[2], groups)
            if node.blend_type not in ('MIX','MULTIPLY','ADD','SUBTRACT'):
                raise UnsupportedEmission('unsupported emission color blend ' + node.blend_type)
            def mixed(uv):
                x,y = np.asarray(a(uv)),np.asarray(b(uv));t=float(np.clip(factor(uv),0,1))
                z = {'MIX':lambda:y, 'MULTIPLY':lambda:x*y, 'ADD':lambda:x+y, 'SUBTRACT':lambda:x-y}[node.blend_type]()
                value=x*(1-t)+z*t
                return np.clip(value,0,1) if node.use_clamp else value
            return mixed
        if node.type == 'MATH':
            funcs = [self.linked(s, groups) for s in node.inputs[:3]]
            operations = {'ADD':lambda a,b,c:a+b, 'SUBTRACT':lambda a,b,c:a-b,
                          'MULTIPLY':lambda a,b,c:a*b, 'DIVIDE':lambda a,b,c:a/b if b else 0,
                          'MULTIPLY_ADD':lambda a,b,c:a*b+c, 'MINIMUM':lambda a,b,c:min(a,b),
                          'MAXIMUM':lambda a,b,c:max(a,b), 'POWER':lambda a,b,c:max(a,0)**b,
                          'ABSOLUTE':lambda a,b,c:abs(a)}
            if node.operation not in operations:
                raise UnsupportedEmission('unsupported emission Math operation ' + node.operation)
            def calc(uv):
                values = [float(np.asarray(f(uv)).flat[0]) for f in funcs]
                result = operations[node.operation](*values)
                return float(np.clip(result,0,1)) if node.use_clamp else result
            return calc
        raise UnsupportedEmission('unsupported emission input node ' + node.bl_label)


def game_emission(mat, content, native_texlights=True):
    """Game self-illumination is emissive by default, unless native texlights already cover it."""
    from .extract import game_path
    from ..core.gamematerials import read_vmt, read_texture_bytes, texture_path
    from ..core.vtf_read import read_vtf
    from pathlib import Path
    path = game_path(mat)
    if not path or content is None:
        return None
    params = read_vmt(content, path, str(Path(content.game_root)/'left4dead2'))
    if params.get('$selfillum', '0') != '1':
        return None
    # Existing game texlights already emit from brush faces in VRAD.
    rad = Path(content.game_root)/'left4dead2/lights.rad'
    if native_texlights and rad.exists():
        for line in rad.read_text(errors='replace').splitlines():
            parts = line.lower().split()
            if parts and parts[0] in ('hdr:', 'ldr:'):
                parts = parts[1:]
            if parts and parts[0].strip(chr(34)) == path.lower():
                return None
    data = read_texture_bytes(content, texture_path(params.get('$basetexture','')))
    if not data:
        raise UnsupportedEmission('game self-illumination texture could not be read')
    _, _, rgba = read_vtf(data, 512)
    pixels = rgba.astype(np.float32)/255
    rgb = pixels[:,:,:3]
    rgb = np.where(rgb<=.04045, rgb/12.92, ((rgb+.055)/1.055)**2.4)
    mask_path = params.get('$selfillummask')
    if mask_path:
        mask_data = read_texture_bytes(content, texture_path(mask_path))
        if not mask_data:
            raise UnsupportedEmission('game self-illumination mask could not be read')
        _, _, mask_pixels = read_vtf(mask_data, 512)
        mask = mask_pixels[:,:,0].astype(np.float32)/255
    else:
        mask = pixels[:,:,3]
    tint = [float(v) for v in params.get('$selfillumtint','1 1 1').strip('[]{}').split()]
    if len(tint)!=3:
        raise UnsupportedEmission('invalid self-illumination tint')
    def sample(uv):
        u,v = uv[0]%1, uv[1]%1
        h,w = rgb.shape[:2];mh,mw=mask.shape
        return rgb[min(h-1,int(v*h)),min(w-1,int(u*w))]*mask[min(mh-1,int(v*mh)),min(mw-1,int(u*mw))]*tint
    result = MaterialEmission(None)
    result.enabled = True
    result.function = sample
    return result


def area_lights(obj, scale, matrix=None, source=None):
    from .extract import object_keyvalues, light_style_keyvalues
    lamp = obj.data
    matrix = matrix if matrix is not None else obj.matrix_world
    # Transform local geometry so nonuniform and negative object scales are honored.
    ax, ay = matrix.to_3x3().col[0], matrix.to_3x3().col[1]
    width = lamp.size
    height = lamp.size_y if lamp.shape in ('RECTANGLE','ELLIPSE') else width
    if min(ax.length, ay.length, width, height) <= 1e-8:
        return []
    normal = -(matrix.to_3x3().inverted().transposed() @ Vector((0,0,1))).normalized()
    kv = object_keyvalues(obj)
    kv.update(light_style_keyvalues(obj, kv))
    samples = rectangle_samples(width*ax.length*scale, height*ay.length*scale, lamp.shape in ('DISK','ELLIPSE'))
    entities = []
    for x, y, weight in samples:
        position = matrix.translation*scale + ax.normalized()*x + ay.normalized()*y
        # Lambert emitter P = pi * area * radiance; sample shares sum to P.
        rgb = [max(0,lamp.energy) * c * scale**2 * weight / (100*math.pi) for c in lamp.color]
        entity = light_sample(position, normal, rgb, source or obj.name, kv)
        if entity:
            entities.append(entity)
    return entities


def mesh_lights(obj, depsgraph, scale, report, cache, matrix=None, source=None, mesh=None, content=None):
    """Static bake emission from evaluated mesh faces, with per-face UV sampling."""
    evaluated = obj.evaluated_get(depsgraph)
    owned = mesh is None
    mesh = evaluated.to_mesh() if owned else mesh
    if mesh is None:
        return []
    matrix = matrix if matrix is not None else obj.matrix_world
    try:
        emitters = {}
        for i, mat in enumerate(mesh.materials):
            if mat is None:
                continue
            is_model = obj.hammerless.role == 'MODEL'
            key = (mat.as_pointer(), is_model)
            if key not in cache:
                try:
                    cache[key] = MaterialEmission(mat)
                    if not cache[key].enabled:
                        cache[key] = game_emission(mat, content, not is_model) or cache[key]
                except UnsupportedEmission as exc:
                    report.warnings.append(f"Material '{mat.name}': baked emission unavailable: {exc}")
                    cache[key] = None
            if cache[key] is not None and cache[key].enabled:
                emitters[i] = cache[key]
        if not emitters:
            return []
        mesh.calc_loop_triangles()
        triangles = []
        for tri in mesh.loop_triangles:
            if tri.material_index not in emitters:
                continue
            verts = [matrix @ mesh.vertices[i].co for i in tri.vertices]
            cross = (verts[1]-verts[0]).cross(verts[2]-verts[0])
            area = cross.length / 2
            if area < 1e-12:
                continue
            # Inverse-transpose matches the evaluated face's outward normal under mirrored transforms.
            normal = (matrix.to_3x3().inverted().transposed() @ tri.normal).normalized()
            uv_layer = mesh.uv_layers.active
            uvs = [tuple(uv_layer.data[i].uv) for i in tri.loops] if uv_layer else None
            triangles.append((verts, normal, area, uvs, emitters[tri.material_index]))
        total_area = sum(t[2] for t in triangles)
        if not total_area:
            return []
        count = min(MAX_MESH_SAMPLES, max(4, math.ceil(total_area * scale**2 / SPACING**2)))
        cumulative = np.cumsum([t[2] for t in triangles])
        entities = []
        for index, weights in enumerate(triangle_samples(count)):
            ti = min(len(triangles)-1, int(np.searchsorted(cumulative, (index+.5)*total_area/count)))
            verts, normal, area, uvs, emission = triangles[ti]
            uv = tuple(sum(weights[i]*uvs[i][j] for i in range(3)) for j in range(2)) if uvs else None
            color = emission(uv)
            if not np.any(color > 0):
                continue
            pos = sum((verts[i]*weights[i] for i in range(3)),Vector())*scale
            pos += normal * .1
            rgb = color * (total_area*scale**2 / (count*100))
            entity = light_sample(pos, normal, rgb, source or obj.name)
            if entity:
                entities.append(entity)
        if entities and obj.hammerless.role == 'MODEL' and obj.hammerless.model_kind != 'STATIC':
            report.warnings.append(f"'{source or obj.name}': emissive lighting is baked at its starting position; it will not move with the model")
        return entities
    finally:
        if owned:
            evaluated.to_mesh_clear()
