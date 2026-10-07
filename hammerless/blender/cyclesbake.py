"""Lighting baked with Cycles into a compiled map (Light Compiler: Cycles).

vrad has already run: it laid out every face's lightmap and wrote the rest of the lighting (prop
lighting, ambient samples, switchable lights). Here a temporary scene is made from the compiled faces,
lit like vrad lights the map (its light entities, in its units), baked, and the values written over
vrad's static lightmaps (core.lightbake). The user's scenes are left as they were.

The bake is Combined (Cycles denoises Combined bakes; it doesn't denoise the light-only Diffuse pass),
limited to diffuse light, on a white surface: that is exactly the light arriving there. The baked surface
is invisible to rays, so its white doesn't bounce; the copy behind it has the real colours and does.

Units: the bake (light arriving, white surface) times 100 pi is vrad's units: a sun of strength 1
gives E = 1 W/m^2 -> bake 1/pi; vrad gives brightness 100 for it. So sun strength = brightness / 100,
sky radiance = ambient / (100 pi), a point light of P watts gives P / (4 pi d^2) at d metres -> P =
brightness * 400 pi / upm^2 (vrad: brightness at 100 units, inverse square).
"""
from __future__ import annotations

import math
import time

import bpy
import numpy as np
from mathutils import Vector

from ..core import lightbake as lb
from ..core.lightmap import _lumps

SAMPLES = 1024
OCCLUDER_SHIFT = 1.0        # units: what blocks and bounces light sits this far behind each baked face
TO_VRAD = 100 * math.pi
NORMAL_ATTR = "hl_normal"


def _gpu_devices(prefs):
    """Turn on a GPU for this bake if the user has none chosen; returns how to put the preferences back."""
    saved = (prefs.compute_device_type, [(d, d.use) for d in prefs.devices])
    if prefs.compute_device_type != "NONE":
        prefs.refresh_devices()
        if any(d.use and d.type == prefs.compute_device_type for d in prefs.devices):
            return saved, True
    for kind in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = kind
        except TypeError:
            continue
        prefs.refresh_devices()
        gpus = [d for d in prefs.devices if d.type == kind]
        if gpus:
            for d in gpus:
                d.use = True
            return saved, True
    prefs.compute_device_type = saved[0]
    return saved, False


def _restore_devices(prefs, saved):
    kind, uses = saved
    try:
        prefs.compute_device_type = kind
        for d, use in uses:
            d.use = use
    except (TypeError, ReferenceError):
        pass


def _mesh(name, corners, uv=None, mats=None, normals=None):
    """Triangles (n*3, 3) as a mesh with one material index per triangle."""
    me = bpy.data.meshes.new(name)
    n = len(corners)
    me.vertices.add(n)
    me.vertices.foreach_set("co", corners.astype(np.float32).ravel())
    me.loops.add(n)
    me.loops.foreach_set("vertex_index", np.arange(n, dtype=np.int32))
    me.polygons.add(n // 3)
    me.polygons.foreach_set("loop_start", np.arange(0, n, 3, dtype=np.int32))
    me.polygons.foreach_set("loop_total", np.full(n // 3, 3, np.int32))
    me.update()
    if uv is not None:
        me.uv_layers.new(name="bake").data.foreach_set("uv", uv.astype(np.float32).ravel())
    if mats is not None:
        me.polygons.foreach_set("material_index", mats.astype(np.int32))
    if normals is not None:
        me.attributes.new(NORMAL_ATTR, "FLOAT_VECTOR", "FACE").data.foreach_set(
            "vector", normals.astype(np.float32).ravel())
    return me


def _material(name, color, image=None):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.remove(nt.nodes["Principled BSDF"])
    diff = nt.nodes.new("ShaderNodeBsdfDiffuse")
    diff.inputs["Color"].default_value = (*color, 1.0)
    nt.links.new(diff.outputs[0], nt.nodes["Material Output"].inputs[0])
    if image is not None:            # the baked surface: shaded with the normal being baked
        attr = nt.nodes.new("ShaderNodeAttribute")
        attr.attribute_type = "GEOMETRY"
        attr.attribute_name = NORMAL_ATTR
        nt.links.new(attr.outputs["Vector"], diff.inputs["Normal"])
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        nt.nodes.active = tex
    return m


def _aim(obj, direction):
    """Point a light's -Z (where Blender lights shine) along direction."""
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = Vector(tuple(direction)).to_track_quat("-Z", "Y")


def bake_bsp(bsp_path: str, upm: float, samples: int = SAMPLES, denoise: bool = False,
             stitch: bool = True) -> list[str]:
    """Bake the map's static lighting with Cycles and write it into the BSP. Returns log lines."""
    t0 = time.time()
    with open(bsp_path, "rb") as f:
        data = f.read()
    faces, lump_no = lb.read_faces(data)
    if not faces:
        return ["Cycles: no lit faces to bake"]
    sun, ambient, lights, notes = lb.scene_lights(_lumps(data)(0).decode("latin-1"), hdr=lump_no == 53)
    width, height = lb.pack(faces)

    keys, mat_of = [], {}
    corners, uvs, mats, normals, bumps = [], [], [], [], []
    occ, occ_mats = [], []
    smp: dict[int, lb.Samples] = {}           # flat faces: where vrad would light them
    for f in faces:
        key = tuple(round(c, 3) for c in f.reflectivity)
        if key not in mat_of:
            mat_of[key] = len(keys)
            keys.append(key)
        if f.rect is not None:
            smp[f.index] = lb.samples(f)
        pos, lux = lb.bake_triangles(f, smp.get(f.index))
        co = (pos / upm).reshape(-1, 3, 3)
        uv = lb.uvs(f, width, height, lux).reshape(-1, 3, 2)
        flip = np.cross(co[:, 1] - co[:, 0], co[:, 2] - co[:, 0]) @ f.normal < 0
        co[flip], uv[flip] = co[flip][:, [0, 2, 1]], uv[flip][:, [0, 2, 1]]   # Blender's normal = the face's
        corners.append(co.reshape(-1, 3))
        uvs.append(uv.reshape(-1, 2))
        mats.append(np.full(len(co), mat_of[key]))
        normals.append(np.broadcast_to(f.normal.astype(np.float32), (len(co), 3)))      # one per triangle
        bumps.append(np.broadcast_to((f.bump_normals if f.bump else np.tile(f.normal, (3, 1))).astype(np.float32),
                                     (len(co), 3, 3)))
        oc = (f.positions / upm).reshape(-1, 3, 3) - f.normal * (OCCLUDER_SHIFT / upm)
        oflip = np.cross(oc[:, 1] - oc[:, 0], oc[:, 2] - oc[:, 0]) @ f.normal < 0
        oc[oflip] = oc[oflip][:, [0, 2, 1]]
        occ.append(oc.reshape(-1, 3))
        occ_mats.append(np.full(len(oc), mat_of[key]))
    corners, uvs, mats = np.concatenate(corners), np.concatenate(uvs), np.concatenate(mats)
    any_bump = any(f.bump for f in faces)
    normals = np.concatenate(normals)
    bumps = np.concatenate(bumps) if any_bump else None

    made = {"objects": [], "meshes": [], "materials": [], "images": [], "lights": [], "worlds": [], "scenes": []}
    prefs = bpy.context.preferences.addons["cycles"].preferences
    saved, gpu = _gpu_devices(prefs)
    try:
        scene = bpy.data.scenes.new("HL_CyclesBake")
        made["scenes"].append(scene)
        image = bpy.data.images.new("HL_CyclesBake", width, height, alpha=False, float_buffer=True)
        made["images"].append(image)
        target_mats = [_material("HL_CyclesBake", (1.0, 1.0, 1.0), image)]    # white: see the top
        block_mats = [_material("HL_CyclesBlock", k) for k in keys]
        made["materials"] += target_mats + block_mats
        target_me = _mesh("HL_CyclesBake", corners, uvs, None, normals)
        block_me = _mesh("HL_CyclesBlock", np.concatenate(occ), mats=np.concatenate(occ_mats))
        made["meshes"] += [target_me, block_me]
        for m in target_mats:
            target_me.materials.append(m)
        for m in block_mats:
            block_me.materials.append(m)
        target = bpy.data.objects.new("HL_CyclesBake", target_me)
        block = bpy.data.objects.new("HL_CyclesBlock", block_me)
        made["objects"] += [target, block]
        # the baked surface doesn't block or bounce light (faces of overlapping brushes lie on top of each
        # other): its copy just behind does. Camera visibility stays on, or the bake comes out black
        for attr in ("visible_diffuse", "visible_glossy", "visible_transmission", "visible_volume_scatter",
                     "visible_shadow"):
            setattr(target, attr, False)
        scene.collection.objects.link(target)
        scene.collection.objects.link(block)

        def add_light(kind, name, energy, color):
            ld = bpy.data.lights.new(name, kind)
            ld.energy, ld.color = energy, color
            made["lights"].append(ld)
            ob = bpy.data.objects.new(name, ld)
            made["objects"].append(ob)
            scene.collection.objects.link(ob)
            return ld, ob

        def split(i):
            top = float(np.max(i))
            return top, tuple(i / top) if top > 0 else (1.0, 1.0, 1.0)

        if sun is not None and np.max(sun.intensity) > 0:
            top, color = split(sun.intensity)
            ld, ob = add_light("SUN", "HL_Sun", top / 100, color)
            ld.angle = 0.0
            _aim(ob, sun.direction)
        for light in lights:
            top, color = split(light.intensity)
            ld, ob = add_light("SPOT" if light.kind == "SPOT" else "POINT", "HL_Light",
                               top * 400 * math.pi / upm ** 2, color)
            ld.shadow_soft_size = 0.0
            ob.location = Vector(light.origin) / upm
            if light.kind == "SPOT":
                ld.spot_size = math.radians(2 * light.cone)
                ld.spot_blend = 1 - light.inner_cone / light.cone if light.cone else 0.0
                _aim(ob, light.direction)
        world = bpy.data.worlds.new("HL_CyclesBake")
        made["worlds"].append(world)
        world.use_nodes = True
        bg = world.node_tree.nodes["Background"]
        bg.inputs["Color"].default_value = (*(ambient / TO_VRAD), 1.0)
        bg.inputs["Strength"].default_value = 1.0
        scene.world = world

        scene.render.engine = "CYCLES"
        scene.cycles.device = "GPU" if gpu else "CPU"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = denoise              # Combined bakes go through the render denoiser
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
        scene.cycles.denoising_input_passes = "RGB_ALBEDO_NORMAL"
        if hasattr(scene.cycles, "denoising_use_gpu"):
            scene.cycles.denoising_use_gpu = gpu
        scene.cycles.diffuse_bounces = 8
        scene.cycles.glossy_bounces = scene.cycles.transmission_bounces = 0
        scene.render.bake.margin = lb.PAD
        layer = scene.view_layers[0]
        attr = target_me.attributes[NORMAL_ATTR]

        def bake() -> np.ndarray:
            override = dict(scene=scene, view_layer=layer, active_object=target, object=target,
                            selected_objects=[target], selected_editable_objects=[target])
            with bpy.context.temp_override(**override):
                target.select_set(True, view_layer=layer)
                bpy.ops.object.bake(type="COMBINED", pass_filter={"DIRECT", "INDIRECT", "DIFFUSE"}, margin=lb.PAD,
                                    use_clear=True)
            return np.array(image.pixels[:], np.float32).reshape(height, width, 4)[:, :, :3] * TO_VRAD

        tb = time.time()
        passes = [bake()]
        if any_bump:
            for k in range(3):
                attr.data.foreach_set("vector", np.ascontiguousarray(bumps[:, k]).ravel())
                target_me.update()
                passes.append(bake())
        seconds = time.time() - tb
    finally:
        for kind in ("objects", "meshes", "materials", "images", "lights", "worlds", "scenes"):
            for block_ in made[kind]:
                try:
                    getattr(bpy.data, kind).remove(block_)
                except (ReferenceError, RuntimeError):
                    pass
        _restore_devices(prefs, saved)

    # flat faces: the sample values, then vrad's luxel filter over each face and its neighbours (this is
    # what joins neighbouring faces' lighting up); displacements: their texels are the luxels
    values = {}
    for f in faces:
        x, y = f.atlas
        maps = passes if f.bump else passes[:1]
        if f.index in smp:
            st = smp[f.index].st
            values[f.index] = [p[y + st[:, 1], x + st[:, 0]].astype(np.float64) for p in maps]
        else:
            values[f.index] = [p[y:y + f.h, x:x + f.w].astype(np.float64) for p in maps]
    near = lb.neighbours(faces)
    samples_of, keep = {}, {}
    for f in faces:
        if f.index not in smp:
            samples_of[f.index] = values[f.index]
            continue
        others = [(g, smp[g.index], values[g.index]) for g in near[f.index] if g.index in smp]
        maps, empty = lb.radial(f, (f, smp[f.index], values[f.index]), others)
        if stitch:          # luxels no sample reached: grown in from the face (vrad leaves them black)
            maps = [lb.fill_empty(mp, empty) for mp in maps]
        samples_of[f.index] = maps
        keep[f.index] = np.where(empty, 0.01, 1.0)      # those may move freely when stitching
    seam_note = ""
    if stitch:
        ts = time.time()
        edges = lb.shared_edges(faces)
        before = lb.seam_error(edges, samples_of)
        samples_of = lb.stitch(edges, samples_of, keep)
        seam_note = (f"Cycles: stitched {len(edges)} shared edges in {time.time() - ts:.1f}s: seams "
                     f"{before:.2%} -> {lb.seam_error(edges, samples_of):.2%} (mean difference across edges)")
    with open(bsp_path, "wb") as out:
        out.write(lb.write(data, lump_no, faces, samples_of))
    lines = [f"Cycles: {len(faces)} faces, {len(passes)} bake{'s' if len(passes) > 1 else ''} on "
             f"{'GPU' if gpu else 'CPU'} in {seconds:.1f}s ({time.time() - t0:.1f}s in all), "
             f"{samples} samples{', denoised' if denoise else ''}, "
             f"{len(lights)} light{'s' if len(lights) != 1 else ''}" + (" + sun and sky" if sun else "")]
    return lines + ([seam_note] if seam_note else []) + [f"Cycles: {n}" for n in notes]
