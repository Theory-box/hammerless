"""Operators: add entities/presets, validate, export, compile, play."""
from __future__ import annotations

import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from mathutils import Vector

from ..core import compile as cc
from ..core.build import Report, build_vmf, validate
from ..core.ir import Entity
from ..core.entities import CATALOG, CATEGORIES, PRESET_BUILDERS, PRESETS, default_keyvalues, preview_model
from ..core.gamefiles import game_files
from ..core.nav import collect_climbs, collect_regions
from ..core.vpk import GameContent
from .extract import extract_scene

LOG_TEXT = "hammerless_log"
_content_cache: dict[str, GameContent] = {}


# ---------------------------------------------------------------- helpers

def game_root(context) -> str | None:
    from .props import preferences
    s = context.scene.hammerless
    prefs = preferences()
    roots = [bpy.path.abspath(r).rstrip("\\/") for r in (s.game_root, prefs.game_root if prefs else "") if r]
    return cc.find_game_root(roots or None)


def game_content(root: str | None) -> GameContent | None:
    if not root:
        return None
    if root not in _content_cache:
        _content_cache[root] = GameContent(root)
        from ..core.surfaces import load_surfaces
        from .props import set_surface_list
        set_surface_list(load_surfaces(_content_cache[root]))
    return _content_cache[root]


BUILD_PROGRESS = {"vis": ""}      # the running build's latest "vis 42%, about 3 s left" line


def compile_options(s) -> "cc.CompileOptions":
    """The build's options from the scene: Visibility's and Lighting's own settings (their Quality presets fill
    them in), the compilers chosen."""
    rad = "SKIP" if s.light_quality == "OFF" else ("FAST" if s.light_fast else "NORMAL")
    return cc.CompileOptions(vis=s.vis_mode, rad=rad, hdr=s.hdr_mode, static_prop_lighting=s.static_prop_lighting,
                             sky_rays=s.light_sky_rays, supersample=s.light_supersample, bounces=s.light_bounces,
                             prop_polys=s.light_prop_polys, patch_size=s.light_patch_size,
                             extra_vbsp=s.extra_vbsp, extra_vvis=s.extra_vvis, extra_vrad=s.extra_vrad,
                             vis_tool=s.vis_tool, light_tool=s.light_tool, map_tool=s.map_tool,
                             **_light_options(s))


def _light_options(s) -> dict:
    """The chosen light compiler's own settings (only then, so they don't change other builds' fingerprint): the
    Hammerless one's (Exact or the GPU, and what goes beyond vrad), or Cycles' bake settings."""
    if s.light_tool == "HAMMERLESS":
        return {"light_exact": s.light_exact, "ss_points": s.light_ss_points, "ss_passes": s.light_ss_passes,
                "ss_threshold": s.light_ss_threshold, "fix_quirks": s.light_fix_quirks,
                "gi": s.light_bounce_method == "GI", "gi_rays": s.light_gi_rays}
    if s.light_tool != "CYCLES":
        return {}
    return {"cycles_samples": s.cycles_samples, "cycles_denoise": s.cycles_denoise,
            "cycles_stitch": s.cycles_stitch}


def launch_options(s) -> cc.LaunchOptions:
    from ..core.window import monitors
    from .props import monitor_key
    choice = s.window_monitor
    monitor = next((m.index for m in monitors() if monitor_key(m) == choice), -1)   # unplugged: game decides
    return cc.LaunchOptions(width=s.window_width, height=s.window_height,
                            borderless=s.window_borderless, monitor_index=monitor, extra=s.launch_extra,
                            difficulty="" if s.difficulty == "KEEP" else s.difficulty, lan=s.fast_loading)


def _world_sky_image(context=None):
    """The image of the Environment Texture the World's output actually shows, if any (the World's own Mapping
    rotation isn't read: Sky Rotation turns it)."""
    world = (context or bpy.context).scene.world
    if not (world and world.use_nodes and world.node_tree):
        return None
    nodes = world.node_tree.nodes
    outputs = [n for n in nodes if n.type == "OUTPUT_WORLD" and n.is_active_output] or \
              [n for n in nodes if n.type == "OUTPUT_WORLD"]
    seen, todo = set(), list(outputs)
    while todo:                          # (upstream from the output, through every linked input)
        node = todo.pop(0)
        if node.name in seen:
            continue
        seen.add(node.name)
        if node.type == "TEX_ENVIRONMENT" and node.image:
            return node.image
        for sock in node.inputs:
            todo += [link.from_node for link in sock.links]
    return None


def _image_rgb(img, width: int = 512):
    """An image's pixels as linear float RGB (h, w, 3), row 0 at the top, scaled down to `width` wide."""
    import numpy as np
    small = img.copy()
    try:
        w, h = small.size
        if w > width:
            small.scale(width, max(1, round(h * width / w)))
        w, h = small.size
        px = np.empty(w * h * 4, np.float32)
        small.pixels.foreach_get(px)
        rgb = px.reshape(h, w, 4)[::-1, :, :3]
        if not small.is_float and small.colorspace_settings.name == "sRGB":
            rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
        return np.ascontiguousarray(rgb)
    finally:
        bpy.data.images.remove(small)


def export_sky(context, root, base: str) -> tuple[str, str]:
    """Sky Light from the sky: the sky's picture as <map>.hlsky_src.npy for the light compiler. Returns its
    fingerprint ("" for one colour) and a note for the log ("" if fine). Never stops the build."""
    import hashlib
    import numpy as np
    from ..core import skylight
    s = context.scene.hammerless
    if s.sky_light == "FLAT":
        return "", ""
    try:
        if s.sky_light == "SKYBOX":
            content = game_content(root)
            pano = skylight.from_skybox(content, s.skyname) if content else None
            if pano is None:
                return "", f"Sky Light: couldn't read the skybox '{s.skyname}': the sky lights the map with one colour"
        else:
            img = s.sky_image or _world_sky_image(context)
            if img is None:
                return "", "Sky Light: no image picked and the World has no Environment Texture: one colour instead"
            pano = skylight.from_equirect_image(_image_rgb(img), s.sky_rotation)
        np.save(base + ".hlsky_src.npy", pano)
    except Exception as ex:          # (a sky that can't be read or saved: one colour, never a failed build)
        return "", f"Sky Light: couldn't use the sky picture ({ex}): the sky lights the map with one colour"
    # (the fingerprint covers how the picture is turned into the light, so a new version remakes it)
    recipe = f"{skylight.BLUR_DEGREES}|{skylight.MAP_W}|{skylight.MAP_H}|v2".encode()
    return hashlib.sha1(recipe + pano.tobytes()).hexdigest()[:16], ""


def _quoted_object(message: str) -> str:
    """First 'Name' in a message that is an object in the scene (for selecting it)."""
    import re
    return next((q for q in re.findall(r"'([^']+)'", message) if bpy.data.objects.get(q)), "")


def needs_nav(context, root, by_game: bool = False) -> bool:
    """Generate nav when asked to, when the map has no nav mesh yet (without one, bots
    can't move and zombies can't spawn), when safe rooms / spawn areas changed, or when the
    nav mesh on disk was made by the other source than the one chosen (Nav Mesh setting).
    by_game: only the game can make it now (Launch): a game-made nav then stays when the
    setting is Made in Blender (our generator replaces it on the next Build)."""
    s = context.scene.hammerless
    tools = cc.Tools(root)
    nav = os.path.join(tools.maps_dir, f"{s.map_name}.nav")
    wanted = "game" if s.nav_source == "GAME" else "blender"
    maker_differs = cc.nav_maker(tools, s.map_name) != wanted and (wanted == "game" or not by_game)
    return (s.generate_nav or not os.path.exists(nav) or cc.nav_marks_changed(tools, s.map_name) or maker_differs
            or cc.nav_outdated(tools, s.map_name))


def launch(context, root, nav_written: bool = False, analyzed: bool = False) -> None:
    """nav_written: Hammerless just wrote the nav mesh; the game only analyzes it (unless it's
    already analyzed: then the game just loads the map)."""
    s = context.scene.hammerless
    tools = cc.Tools(root)
    generate = False if nav_written else needs_nav(context, root, by_game=True)
    if nav_written:
        analyze = not analyzed
    else:      # a nav whose analysis never got saved (the game was closed first): the game adds it now
        analyze = not generate and cc.nav_analyzed(tools, s.map_name) is False
    cc.launch_game(tools, s.map_name, generate_nav=generate, window=launch_options(s), analyze_nav=analyze)


def _start_nav_analysis(mesh, vmf_path: str, bsp_path: str, root: str) -> dict:
    """Run navanalyze (visibility, hiding spots) on our nav mesh in a background thread."""
    import threading
    import time
    box = {"stage": "starting", "done": False, "error": None, "seconds": 0.0}

    def work():
        t0 = time.time()
        try:
            from ..core.navanalyze import analyze
            from ..core.vpk import GameContent
            with open(vmf_path, encoding="utf-8") as f:
                text = f.read()
            analyze(mesh, text, bsp_path, GameContent(root), os.path.join(root, "left4dead2"),
                    progress=lambda stage: box.update(stage=stage.lower()))
            box["done"] = True
        except Exception as ex:          # reported; the game analyzes the nav mesh instead
            box["error"] = str(ex)
        box["seconds"] = time.time() - t0
    box["thread"] = threading.Thread(target=work, daemon=True)
    box["thread"].start()
    return box


def work_dir(context) -> str:
    s = context.scene.hammerless
    d = bpy.path.abspath(s.output_dir or "//hammerless_build")
    if d.startswith("//") or not os.path.isabs(d):  # unsaved .blend
        d = os.path.join(bpy.app.tempdir or os.path.expanduser("~"), "hammerless_build")
    os.makedirs(d, exist_ok=True)
    return d


def write_log(lines: list[str], append: bool = False) -> None:
    txt = bpy.data.texts.get(LOG_TEXT) or bpy.data.texts.new(LOG_TEXT)
    if not append:
        txt.clear()
    last = len(txt.lines) - 1
    txt.cursor_set(last, character=len(txt.lines[last].body))   # write at the end, wherever the user clicked
    txt.write("\n".join(lines) + "\n")


def report_lines(rep: Report) -> list[str]:
    out = [f"ERROR: {e}" for e in rep.errors]
    out += [f"WARNING: {w}" for w in rep.warnings]
    out += [f"info: {i}" for i in rep.info]
    return out


def surface_report(op, rep: Report) -> None:
    write_log(report_lines(rep))
    for e in rep.errors[:5]:
        op.report({"ERROR"}, e)
    if rep.errors:
        op.report({"ERROR"}, f"{len(rep.errors)} error(s). See the '{LOG_TEXT}' text block for details")
    elif rep.warnings:
        op.report({"WARNING"}, f"{len(rep.warnings)} warning(s). See the '{LOG_TEXT}' text block")


def build_map_text(context, root: str | None):
    """The scene as VMF text, exactly as Build exports it: (ir, text or None, report). Anything
    else that compares against the last build (Analyze Navmesh) must use this too."""
    s = context.scene.hammerless
    rep = Report()
    from .props import clean_map_name
    if not s.map_name or clean_map_name(s.map_name) != s.map_name:
        rep.errors.append(f"Map Name '{s.map_name}' can't be used: use lowercase letters, digits and _ only "
                          f"(for example '{clean_map_name(s.map_name) or 'my_map'}')")
        return None, None, rep
    gamedir = os.path.join(root, "left4dead2") if root else None
    content = game_content(root)        # loads the game's surface list before materials are read
    if content is not None and f"maps/{s.map_name}.bsp" in getattr(content, "files", ()):
        rep.errors.append(f"Map Name '{s.map_name}' is one of the game's own maps: give yours another name (building "
                          "it would put files over the official map's in the game folder)")
        return None, None, rep
    ir, _mats = extract_scene(context, rep, gamedir, content)
    if rep.errors:
        return ir, None, rep
    from .logic import compile_logic
    compile_logic(context, ir, rep)
    from .sound import add_acoustics
    from . import vmfimport as _vi
    if _vi.imported(context.scene) and s.sound_mode != "OFF":
        # (it works out rooms from the scene's own brushes: an imported map's aren't among them, so its
        # soundscape spots would land in the void and leak the map. The map has its own soundscapes)
        rep.info.append("Sound is off for an imported map (it keeps its own soundscapes)")
    else:
        add_acoustics(context, ir, rep)
    base = None
    from . import vmfimport
    if vmfimport.imported(context.scene):
        from ..core.vmf import VMFWriter
        writer = VMFWriter()
        doc, world, ents = vmfimport.rebuild(context, writer, _mats, rep)
        base = (doc, world, ents, writer)
    text, rep2 = build_vmf(ir, content if s.check_game_content else None, base)
    rep2.errors[:0] = rep.errors
    rep2.warnings[:0] = rep.warnings
    rep2.info[:0] = rep.info
    return ir, text, rep2


def _write_mode_addon(gamedir: str, rep: Report) -> None:
    """Hammerless's game mode (scripted mode, for Override and HUD nodes) as a small addon VPK. The
    game reads modes when it starts: a new or changed addon needs one restart of the game."""
    from ..core.gamefiles import MODE_ADDON, mode_addon_files
    from ..core.vpk import write_vpk
    import tempfile
    full = os.path.join(gamedir, *MODE_ADDON.split("/"))
    tmp = os.path.join(tempfile.gettempdir(), "hammerless_mode.vpk")
    write_vpk(tmp, mode_addon_files())
    with open(tmp, "rb") as f:
        new = f.read()
    old = None
    if os.path.exists(full):
        with open(full, "rb") as f:
            old = f.read()
    if old == new:
        return
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(new)
    rep.info.append(f"Wrote Hammerless's game mode ({MODE_ADDON}): the map uses Override or HUD nodes")
    if cc.game_running():
        rep.warnings.append("Override and HUD nodes need Hammerless's game mode, which was just installed: "
                            "close Left 4 Dead 2 and Build & Play again (the game reads game modes when it starts)")


def context_scene_setting(name: str, default):
    try:
        return getattr(bpy.context.scene.hammerless, name)
    except Exception:
        return default


def _compile_models(root: str, gamedir: str, ir, rep: Report, work: str) -> None:
    """The map's Custom Models: compiled with the game's studiomdl into models/hammerless/<map>/ (only the
    ones that changed since the last build), and their materials."""
    import hashlib
    from ..core import models as m
    studiomdl = os.path.join(root, "bin", "studiomdl.exe")
    own = context_scene_setting("model_compiler", "HAMMERLESS") == "HAMMERLESS"
    if not own and not os.path.exists(studiomdl):
        rep.errors.append("Custom Models need studiomdl.exe from the Left 4 Dead 2 Authoring Tools (Steam > Library > "
                          "Tools): it isn't in the game's bin folder")
        return
    m.write_model_materials(gamedir, f"models/hammerless/{ir.settings.name}", ir.model_materials)
    built = 0
    writer = _writer_version() if own else "studiomdl"     # (a fixed model writer makes the models again)
    for name, spec in ir.models.items():
        sig = hashlib.sha1((m.reference_smd(spec.triangles) + m.collision_smd(spec.collision)
                            + m.qc_text(spec) + writer).encode()).hexdigest()
        folder = os.path.join(work, "models", name.split("/")[-1])
        stamp = os.path.join(folder, "built.sha1")
        files = m.model_files(name)[:3] + (m.model_files(name)[3:] if spec.collision else [])   # (.phy too)
        have = all(os.path.exists(os.path.join(gamedir, *f.split("/"))) for f in files)
        old = None
        if have and os.path.exists(stamp):
            with open(stamp) as f:
                old = f.read()
        if old == sig:
            continue
        if own:
            try:
                from ..core.surfaces import surface_density
                os.makedirs(folder, exist_ok=True)
                m.write_model_files(gamedir, spec, surface_density(game_content(root), spec.surfaceprop))
            except Exception as ex:
                rep.errors.append(f"Custom Model '{name.split('/')[-1]}': {ex}")
                continue
            with open(stamp, "w") as f:
                f.write(sig)
            built += 1
            continue
        res = m.compile_model(studiomdl, gamedir, spec, folder)
        if not res.ok:
            errs = m.studiomdl_errors(res.log) or res.log.strip().splitlines()[-3:]
            rep.errors.append(f"Custom Model '{name.split('/')[-1]}' didn't compile: " + "; ".join(errs[:3]))
            continue
        with open(stamp, "w") as f:
            f.write(sig)
        built += 1
    _remove_old_models(gamedir, ir)
    rep.info.append(f"Custom Models: {len(ir.models)} ({built} compiled now)")


def _writer_version() -> str:
    """Hammerless's own model writer, as a fingerprint of its code."""
    import hashlib
    from ..core import mdlwrite, phywrite
    h = hashlib.sha1()
    for mod in (mdlwrite, phywrite):
        with open(mod.__file__, "rb") as f:
            h.update(f.read())
    return "own-" + h.hexdigest()[:12]


def _remove_old_models(gamedir: str, ir) -> None:
    """Model files this map no longer uses (a renamed or deleted Custom Model mesh) in models/hammerless/<map>/."""
    folder = os.path.join(gamedir, "models", "hammerless", ir.settings.name)
    keep = {n.split("/")[-1] for n in ir.models}
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for f in names:
        stem = f.split(".")[0]
        if stem not in keep and f.endswith((".mdl", ".vvd", ".vtx", ".phy")):
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass


def export_vmf(op, context) -> tuple[str | None, str | None, Report]:
    """Extract + build + write. Returns (vmf_path, game_root, report)."""
    s = context.scene.hammerless
    root = game_root(context)
    gamedir = os.path.join(root, "left4dead2") if root else None
    ir, text, rep2 = build_map_text(context, root)
    if text is None:
        return None, root, rep2
    try:
        path = os.path.join(work_dir(context), f"{s.map_name}.vmf")
    except OSError as ex:
        rep2.errors.append(f"Can't create the build folder ({ex}): set Settings > Game & Folders > Work Folder to a folder you can "
                           "write to")
        return None, root, rep2
    if not bpy.data.filepath and not os.path.isabs(bpy.path.abspath(s.output_dir or "//")):
        rep2.warnings.append("The .blend isn't saved: the build goes to a temporary folder Blender deletes on quit "
                             "(the next build after a restart compiles everything again). Save the .blend first")
    try:
        path.encode("mbcs", "strict")
    except (UnicodeEncodeError, LookupError):
        rep2.warnings.append(f"The build folder '{os.path.dirname(path)}' has characters the map compilers can't "
                             "read; if the compile fails, save the .blend in a folder with plain (English) "
                             "letters or set Settings > Game & Folders > Work Folder")
    import json
    from . import vmfimport
    imported = vmfimport.imported(context.scene)
    try:
        # (an imported map keeps the bytes it was read with)
        with open(path, "w", encoding="latin-1" if imported else "utf-8", errors="replace") as f:
            f.write(text)
        if imported and "func_instance" in text:
            vmfimport.copy_instances(context.scene, os.path.dirname(path), rep2)
        with open(cc.sources_path(path), "w", encoding="utf-8") as f:
            json.dump({str(k): v for k, v in rep2.solid_sources.items()}, f)
    except OSError as ex:
        rep2.errors.append(f"Can't write the map file ({ex}): set Settings > Game & Folders > Work Folder to a folder you can write to")
        return None, root, rep2
    rep2.info.append(f"Wrote {path}")
    if gamedir:
        other = cc.map_owner(cc.Tools(root), s.map_name)
        if other and os.path.normcase(other) != os.path.normcase(path) and os.path.exists(other):
            rep2.warnings.append(f"Another .blend also builds a map called '{s.map_name}' (its build: {other}). "
                                 "This build replaces that map in the game: give this one its own Map Name")
        files = game_files(ir)
        try:
            for rel, content in files.items():
                full = os.path.join(gamedir, *rel.split("/"))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
            cc.set_map_owner(cc.Tools(root), s.map_name, path)
            if ir.scripted_mode:
                _write_mode_addon(gamedir, rep2)
            if ir.models:
                _compile_models(root, gamedir, ir, rep2, os.path.dirname(path))
                if rep2.errors:
                    return None, root, rep2
            else:
                _remove_old_models(gamedir, ir)        # (every Custom Model deleted: their files too)
        except OSError as ex:
            rep2.errors.append(f"Can't write the map's scripts into the game folder ({ex}). Is Left 4 Dead 2 "
                               "installed somewhere that needs administrator rights?")
            return None, root, rep2
        regions, _ = collect_regions(ir)
        rep2.nav_regions = regions
        rep2.nav_climbs = collect_climbs(ir)[0]
        rep2.info.append(f"Wrote {len(files)} script(s) to the game folder; nav marking covers "
                         f"{len(regions)} region(s)")
    return path, root, rep2


# ---------------------------------------------------------------- preview meshes

def cache_name(prefix: str, key: str, scale: float | None = None) -> str:
    """A cached data-block's name: Blender cuts names at 63 characters, so long keys get a hash
    (otherwise the cache is never found and a new copy is made every time); per scale for meshes."""
    import hashlib
    if scale is not None and abs(scale - 52.49) > 1e-4:     # (a float property: 52.4900016...)
        key = f"{key}@{scale:g}"
    name = prefix + key
    if len(name) <= 60:
        return name
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return prefix + key[-(60 - len(prefix) - 9):] + "~" + digest


def preview_mesh(classname: str, scale: float, model: str = ""):
    """Shared wireframe box + forward arrow showing an entity's size and facing.
    With a model path, the box is the model's real bounds (read from the game)."""
    bounds = None
    if model:
        full = real_model_mesh(model, scale)
        if full is not None:
            return full
        name = cache_name("HL_model_", model, scale)
        mesh = bpy.data.meshes.get(name)
        if mesh:
            return mesh
        bounds = model_bounds_from_game(model)
    if bounds is None:
        name = cache_name("HL_preview_", classname, scale)
        mesh = bpy.data.meshes.get(name)
        if mesh:
            return mesh
        d = CATALOG.get(classname)
        bounds = d.preview_bounds() if d else ((-8, -8, -8), (8, 8, 8))
    return box_arrow_mesh(name, bounds, scale)


def real_model_mesh(model: str, scale: float):
    """The game model itself as a textured mesh, if model previews are on and it can be read."""
    ctx = bpy.context
    if not ctx.scene.hammerless.model_previews:
        return None
    root = game_root(ctx)
    content = game_content(root)
    if content is None:
        return None
    from .preview import model_mesh
    return model_mesh(model, content, os.path.join(root, "left4dead2"), scale)


def style_entity_object(obj) -> None:
    """Real models draw solid and textured; placeholder boxes draw as wireframes."""
    if obj.data is not None and obj.data.get("hl_model"):
        obj.display_type = "TEXTURED"
        obj.show_in_front = False
    else:
        obj.display_type = "WIRE"
        obj.show_in_front = True


def model_bounds_from_game(model: str):
    from ..core.vpk import model_bounds
    root = game_root(bpy.context)
    content = game_content(root)
    data = content.read(model) if content else None
    if not data:
        return None
    try:
        return model_bounds(data)
    except Exception:
        return None


def box_arrow_mesh(name, bounds, scale):
    (x0, y0, z0), (x1, y1, z1) = bounds
    x0, y0, z0, x1, y1, z1 = (c / scale for c in (x0, y0, z0, x1, y1, z1))
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    # forward (+X) arrow at mid height
    zm = (z0 + z1) / 2
    reach = max(x1, 16 / scale) + 12 / scale
    verts += [(x1, -6 / scale, zm), (reach, 0, zm), (x1, 6 / scale, zm)]
    faces.append((8, 9, 10))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    return mesh


def make_entity_object(context, classname: str, location, collection=None, rotation=(0, 0, 0),
                       keyvalues: dict | None = None, name: str | None = None):
    s = context.scene.hammerless
    model = preview_model(classname, {**default_keyvalues(classname), **(keyvalues or {})})
    obj = bpy.data.objects.new(name or classname, preview_mesh(classname, s.units_per_meter, model))
    style_entity_object(obj)
    obj.hide_render = True
    obj.location = location
    obj.rotation_euler = rotation
    (collection or context.collection).objects.link(obj)
    obj.hammerless.role = "ENTITY"
    obj.hammerless.classname = classname  # fills default keyvalues
    for k, v in (keyvalues or {}).items():
        kv = next((x for x in obj.hammerless.keyvalues if x.key == k), None) or obj.hammerless.keyvalues.add()
        kv.key, kv.value = k, v
    return obj


def apply_entity_data(obj, ent) -> None:
    """Copy an IR entity's keyvalues and outputs onto a tagged Blender object."""
    for k, v in ent.keyvalues.items():
        kv = next((x for x in obj.hammerless.keyvalues if x.key == k), None) or obj.hammerless.keyvalues.add()
        kv.key, kv.value = k, v
    for o in ent.outputs:
        item = obj.hammerless.outputs.add()
        item.output, item.target, item.input = o.output, o.target, o.input
        item.parameter, item.delay, item.only_once = o.parameter, o.delay, o.times == 1


def box_mesh_object(context, name, mins, maxs, scale, collection, material_path=None, brush=None):
    """Box object. With `brush`, each face gets the material of the brush face pointing the same way."""
    x0, y0, z0 = (c / scale for c in mins)
    x1, y1, z1 = (c / scale for c in maxs)
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    face_mats = sorted({f.material for f in brush.faces}) if brush else []
    if len(face_mats) > 1:
        from ..core.geometry import dot, polygon_normal
        for m in face_mats:
            obj.data.materials.append(game_material(m))
        normals = [(polygon_normal(f.verts), f.material) for f in brush.faces]
        for poly in mesh.polygons:
            best = max(normals, key=lambda nm: dot(nm[0], tuple(poly.normal)))
            poly.material_index = face_mats.index(best[1])
    elif material_path:
        obj.data.materials.append(game_material(material_path))
    return obj


def game_material(path: str):
    """Blender material standing in for a game material (name = game path)."""
    mat = bpy.data.materials.get(path)
    if mat is None:
        mat = bpy.data.materials.new(path)
        mat.hammerless.source_material = path
        colors = {"tools/toolsnodraw": (0.9, 0.8, 0.1, 1), "tools/toolstrigger": (0.9, 0.5, 0.1, 0.4),
                  "tools/toolsskybox": (0.4, 0.7, 1.0, 1), "tools/toolsclip": (0.6, 0.2, 0.8, 0.5),
                  "tools/toolsplayerclip": (0.8, 0.2, 0.8, 0.5)}
        mat.diffuse_color = colors.get(path, (0.55, 0.55, 0.55, 1))
        if not path.startswith("tools/"):
            refresh_material_preview(mat)
    return mat


def refresh_material_preview(mat) -> bool:
    from .preview import apply_preview
    ctx = bpy.context
    root = game_root(ctx)
    content = game_content(root)
    if content is None:
        return False
    return apply_preview(mat, content, os.path.join(root, "left4dead2"), ctx.scene.hammerless.units_per_meter)


# ---------------------------------------------------------------- operators

_ENTITY_ITEMS: list = []           # built once and kept: Blender only borrows the strings
_BRUSH_ENTITY_ITEMS: list = []


def _entity_enum(self, context):
    if not _ENTITY_ITEMS:
        for cat in CATEGORIES:
            for cls, d in sorted(CATALOG.items(), key=lambda kv: kv[1].label):
                if d.category == cat and not d.brush:
                    _ENTITY_ITEMS.append((cls, f"{d.label}", f"{cls}: {d.description}"))
    return _ENTITY_ITEMS


def _brush_entity_enum(self, context):
    if not _BRUSH_ENTITY_ITEMS:
        _BRUSH_ENTITY_ITEMS.extend((cls, d.label, f"{cls}: {d.description}")
                                   for cls, d in sorted(CATALOG.items(), key=lambda kv: kv[1].label) if d.brush)
    return _BRUSH_ENTITY_ITEMS


class HL_OT_add_entity(bpy.types.Operator):
    bl_idname = "hammerless.add_entity"
    bl_label = "Add L4D2 Entity"
    bl_description = "Add a point entity (spawn, item, weapon, infected...) at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "classname"

    classname: EnumProperty(name="Entity", items=_entity_enum)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        for o in context.selected_objects:
            o.select_set(False)
        d = CATALOG[self.classname]
        obj = make_entity_object(context, self.classname, context.scene.cursor.location.copy(),
                                 name=d.label)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class HL_OT_set_entity_class(bpy.types.Operator):
    bl_idname = "hammerless.set_entity_class"
    bl_label = "Change Entity"
    bl_description = "Change the selected entity's class (its settings reset to the new class's defaults)"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "classname"

    classname: EnumProperty(name="Entity", items=_entity_enum)

    @classmethod
    def poll(cls, context):
        return context.object is not None and bool(context.object.hammerless.classname)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        obj = context.object
        if obj.hammerless.classname != self.classname:
            obj.hammerless.classname = self.classname
        if obj.type == "MESH" and obj.hammerless.role in ("ENTITY", "AUTO"):
            kv = {k.key: k.value for k in obj.hammerless.keyvalues}
            obj.data = preview_mesh(self.classname, context.scene.hammerless.units_per_meter,
                                    preview_model(self.classname, kv) or "")
            style_entity_object(obj)
        return {"FINISHED"}


class HL_OT_set_brush_entity(bpy.types.Operator):
    bl_idname = "hammerless.set_brush_entity"
    bl_label = "Make Brush Entity"
    bl_description = "Turn the selected meshes into brush entities (func_detail, triggers, blockers...)"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "classname"

    classname: EnumProperty(name="Entity", items=_brush_entity_enum)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        changed = 0
        for o in context.selected_objects:
            hs = o.hammerless
            # point entities (their previews are meshes too) and preset pieces stay what they are
            if o.type != "MESH" or hs.role == "ENTITY" or hs.preset_part or (
                    hs.role == "AUTO" and hs.classname and not CATALOG.get(hs.classname, None) is None
                    and not CATALOG[hs.classname].brush):
                continue
            hs.role = "BRUSH_ENTITY"
            if hs.classname != self.classname:      # (setting it resets the keyvalues to the defaults)
                hs.classname = self.classname
            changed += 1
            if self.classname in ("info_changelevel", "trigger_once", "trigger_multiple",
                                  "env_player_blocker", "func_playerinfected_clip"):
                o.display_type = "WIRE"
                mat = "tools/toolstrigger" if "trigger" in self.classname or self.classname == "info_changelevel" \
                    else "tools/toolsplayerclip"
                if o.data.users > 1:
                    o.data = o.data.copy()          # don't change the other objects sharing this mesh
                o.data.materials.clear()
                o.data.materials.append(game_material(mat))
        if not changed:
            self.report({"WARNING"}, "Select plain meshes to turn into brush entities (entities and preset parts "
                                     "are left as they are)")
            return {"CANCELLED"}
        return {"FINISHED"}


NAME_KEYS = ("targetname", "climb")              # keys whose value names a preset's own piece
REFERENCE_KEYS = ("targetname", "climb", "target", "parentname", "landmark", "filtername")


def _unique_preset_names(preset) -> None:
    """Names a preset gives its pieces (e.g. 'tank_ambush_spawner', 'gate_1', 'climb_1') that are already
    used in the file get a number, everywhere in the preset, so two copies don't set each other off."""
    import re
    used = {kv.value for o in bpy.data.objects for kv in o.hammerless.keyvalues if kv.key in NAME_KEYS and kv.value}
    renames: dict[str, str] = {}
    for part in preset.parts:
        e = part.entity
        if e is None:
            continue
        for key in NAME_KEYS:
            old = e.keyvalues.get(key)
            if not old or old not in used or old in renames:
                continue
            stem = re.sub(r"_\d+$", "", old)
            n = 2
            while f"{stem}_{n}" in used or f"{stem}_{n}" in renames.values():
                n += 1
            renames[old] = f"{stem}_{n}"
    if not renames:
        return
    for part in preset.parts:
        e = part.entity
        if e is None:
            continue
        e.keyvalues = {k: (renames.get(v, v) if k in REFERENCE_KEYS else v) for k, v in e.keyvalues.items()}
        for o in e.outputs:
            o.target = renames.get(o.target, o.target)
            o.parameter = renames.get(o.parameter, o.parameter)


class HL_OT_add_preset(bpy.types.Operator):
    bl_idname = "hammerless.add_preset"
    bl_label = "Add Preset"
    bl_description = "Add a ready-made group (safe room with door, spawns, items...) at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    preset: EnumProperty(name="Preset", items=[(k, p.label, p.description) for k, p in PRESETS.items()])
    landmark: StringProperty(
        name="Landmark Name", default="landmark_1",
        description="Links this safe room to the neighbouring map. A map's END room and the NEXT "
                    "map's START room must use the same name")
    next_map: StringProperty(name="Next Map", default="c1m2_streets",
                             description="End safe room only: the map to load when survivors close the door. "
                                         "Must be a different map, or zombies won't wander (no start-to-end path)")

    def draw(self, context):
        if self.preset in ("START_SAFE_ROOM", "END_SAFE_ROOM"):
            self.layout.prop(self, "landmark")
        if self.preset == "END_SAFE_ROOM":
            self.layout.prop(self, "next_map")

    def execute(self, context):
        s = context.scene.hammerless
        scale = s.units_per_meter
        builder = PRESET_BUILDERS[self.preset]
        if self.preset in ("START_SAFE_ROOM", "END_SAFE_ROOM"):
            # two rooms in one map can't share a landmark name (the dialog remembers the last one)
            used = {kv.value for o in bpy.data.objects for kv in o.hammerless.keyvalues
                    if o.hammerless.classname == "info_landmark" and kv.key == "targetname"}
            if self.landmark in used:
                n = 1
                while f"landmark_{n}" in used:
                    n += 1
                self.report({"INFO"}, f"Landmark '{self.landmark}' is already used here; using 'landmark_{n}'")
                self.landmark = f"landmark_{n}"
        if self.preset == "START_SAFE_ROOM":
            preset = builder(landmark=self.landmark)
        elif self.preset == "END_SAFE_ROOM":
            preset = builder(next_map=self.next_map, landmark=self.landmark)
        elif self.preset == "CRESCENDO_BUTTON":
            used = {kv.value for o in bpy.data.objects for kv in o.hammerless.keyvalues
                    if o.hammerless.classname == "hammerless_crescendo" and kv.key == "name"}
            n = 1
            while f"crescendo_{n}" in used:
                n += 1
            preset = builder(name=f"crescendo_{n}")
        else:
            preset = builder()
        _unique_preset_names(preset)
        from .presets import make_root, parent_to
        coll = context.collection
        base = context.scene.cursor.location.copy()
        root = make_root(context, self.preset, base, coll)
        parts = []
        for part in preset.parts:
            if part.brush is not None:
                pts = [v for f in part.brush.faces for v in f.verts]
                mins = tuple(min(p[i] for p in pts) for i in range(3))
                maxs = tuple(max(p[i] for p in pts) for i in range(3))
                obj = box_mesh_object(context, f"{preset.label} {part.name}", mins, maxs, scale, coll,
                                      part.brush.faces[0].material)
                obj.location += base
                obj.hammerless.role = "BRUSH"
            elif part.entity is not None:
                e = part.entity
                if e.brushes:
                    pts = [v for b in e.brushes for f in b.faces for v in f.verts]
                    mins = tuple(min(p[i] for p in pts) for i in range(3))
                    maxs = tuple(max(p[i] for p in pts) for i in range(3))
                    obj = box_mesh_object(context, f"{preset.label} {part.name}", mins, maxs, scale, coll,
                                          e.brushes[0].faces[0].material, e.brushes[0])
                    obj.location += base
                    obj.display_type = "WIRE"
                    obj.hammerless.role = "BRUSH_ENTITY"
                    obj.hammerless.classname = e.classname
                    apply_entity_data(obj, e)
                    if e.classname == "func_button":
                        obj.display_type = "TEXTURED"
                else:
                    import math
                    loc = base + Vector(e.origin) / scale
                    rot = (math.radians(e.angles[2]), math.radians(e.angles[0]), math.radians(e.angles[1]))
                    obj = make_entity_object(context, e.classname, loc, coll, rot, e.keyvalues,
                                             f"{preset.label} {part.name}")
                    apply_entity_data(obj, Entity(e.classname, outputs=e.outputs))
            obj.hammerless.preset_part = part.name
            parts.append(obj)
        context.view_layer.update()
        for obj in parts:
            parent_to(obj, root)
        for o in context.selected_objects:
            o.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        self.report({"INFO"}, f"Added {preset.label}: select '{root.name}' to move it or change its settings")
        return {"FINISHED"}


class HL_OT_validate(bpy.types.Operator):
    bl_idname = "hammerless.validate"
    bl_label = "Check for Problems"
    bl_description = "Check brushes, terrain and entities without compiling"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.hammerless
        rep = Report()
        root = game_root(context)
        game_content(root)              # (the game's surface list, as Build loads it)
        ir, _ = extract_scene(context, rep, os.path.join(root, "left4dead2") if root else None)
        from .logic import compile_logic
        compile_logic(context, ir, rep)
        rep2 = validate(ir, game_content(root) if s.check_game_content else None)
        rep.errors += rep2.errors; rep.warnings += rep2.warnings; rep.info += rep2.info
        rep.problems = rep2.problems
        from .problems import store
        store(context, rep)
        surface_report(self, rep)
        if rep.ok and not rep.warnings:
            self.report({"INFO"}, "No problems found")
        return {"FINISHED"}


class HL_OT_export_vmf(bpy.types.Operator):
    bl_idname = "hammerless.export_vmf"
    bl_label = "Export VMF"
    bl_description = ("Write the map as a Hammer .vmf file without compiling it (to open in Valve's Hammer "
                      "editor). Build does this for you")

    def execute(self, context):
        path, _root, rep = export_vmf(self, context)
        surface_report(self, rep)
        if path:
            self.report({"INFO"}, f"Exported {path}")
        return {"FINISHED"} if path else {"CANCELLED"}


def _watch_load(before_launch: float, timing: str, nav: bool) -> None:
    """Once survivors are in the map, log how long Build & Play took; then show the game's
    start-to-end path report in the problem list (each map load sends one)."""
    from .problems import store_flow
    launch_id = cc.LOAD_STATUS["launch_id"]
    state = {"waited": 0.0, "logged": False, "flow_seq": cc.LOAD_STATUS["flow_seq"]}
    owner = (bpy.context.scene.name, bpy.context.scene.hammerless.map_name)

    def check():
        if cc.LOAD_STATUS["launch_id"] != launch_id or state["waited"] > 660:
            return None
        state["waited"] += 1.0
        if cc.LOAD_STATUS.get("error") and not state.get("error"):
            state["error"] = True
            write_log([cc.LOAD_STATUS["error"]], append=True)
            print("Hammerless:", cc.LOAD_STATUS["error"])
            _popup(cc.LOAD_STATUS["error"])
        if (bpy.context.scene.name, bpy.context.scene.hammerless.map_name) != owner:
            return 1.0          # another scene / map is active: its problem list isn't this map's
        secs = cc.LOAD_STATUS["seconds"]
        if secs is not None and not state["logged"]:
            state["logged"] = True
            line = (f"Build & Play: {before_launch + secs:.0f}s total ({timing}, game load {secs:.1f}s"
                    + (", then nav mesh generation and two reloads" if nav else "") + ")")
            write_log([line], append=True)
            print("Hammerless:", line)
        if cc.LOAD_STATUS["flow_seq"] != state["flow_seq"] and cc.LOAD_STATUS["flow"]:
            state["flow_seq"] = cc.LOAD_STATUS["flow_seq"]
            report = cc.LOAD_STATUS["flow"]
            scene = bpy.context.scene
            scene.hammerless["ingame_flow"] = (
                f"In game: path from start to end works ({report['length']:.0f} units)"
                if report["state"] == "ok" else "In game: no path from start to end")
            write_log([scene.hammerless["ingame_flow"]], append=True)
            try:        # the game saved its nav mesh by now: read it for the viewport and islands
                from . import navview
                navview.load(bpy.context)
                if navview.report() is not None:
                    from .problems import store_nav
                    store_nav(bpy.context, navview.mesh(), navview.report())
            except Exception as ex:
                print("Hammerless: couldn't read the nav mesh:", ex)
            store_flow(report)                  # after the nav rows, which replace only their own
        return 1.0
    _TIMERS.append(check)
    bpy.app.timers.register(check, first_interval=1.0)


_TIMERS: list = []           # load watchers, removed when the add-on is turned off


def _popup(message: str) -> None:
    """Show a message from a background step (timers have no operator to report through)."""
    try:
        wm = bpy.context.window_manager
        with bpy.context.temp_override(window=wm.windows[0]):
            wm.popup_menu(lambda self, _ctx: self.layout.label(text=message), title="Hammerless", icon="ERROR")
    except (IndexError, RuntimeError, AttributeError, TypeError):
        pass


def _start_nav_generation(vmf_path: str, regions, climbs=(), wall_climbs=False, after=None) -> dict:
    """Run our copy of the game's nav generator on the VMF in a background thread (once `after`, an Event, is
    set: the build's own Python work first, as both need Python's lock; measured 3.8 s against 0.3 s)."""
    import threading
    import time
    from ..core.navpredict import cached, predict
    with open(vmf_path, encoding="utf-8") as f:
        text = f.read()
    box = {"stage": "starting", "mesh": None, "error": None, "seconds": 0.0}
    reuse = cached(text, regions, climbs, wall_climbs)  # Build Navmesh was pressed on this exact map: no waiting
    if reuse is not None:
        box["mesh"] = reuse
        box["thread"] = threading.Thread(target=lambda: None)
        box["thread"].start()
        box["thread"].join()
        return box

    def work():
        if after is not None:
            after.wait(30)
        t0 = time.time()
        try:
            box["mesh"] = predict(text, regions, lambda stage, n: box.update(stage=stage.lower()), climbs,
                                  wall_climbs)
        except Exception as ex:          # reported; the game makes the nav mesh instead
            box["error"] = str(ex)
        box["seconds"] = time.time() - t0
    box["thread"] = threading.Thread(target=work, daemon=True)
    box["thread"].start()
    return box


def _selection_volume(context) -> str:
    """The selected objects' bounds (each its own box, turned with it) as "bake only inside" volumes ("" if none)."""
    from mathutils import Vector
    from ..core.lightvolumes import box_volume
    from .mapcollection import map_objects
    upm = context.scene.hammerless.units_per_meter
    inside = map_objects(context.scene)
    out = []
    for o in context.selected_objects:
        if o.name not in inside or o.type not in ("MESH", "EMPTY", "CURVE"):
            continue
        objs = [o] + [c for c in o.children_recursive if c.type == "MESH"]    # (a preset: its parts)
        for x in objs:
            if x.type == "EMPTY":
                continue                    # (an empty's box is a tiny one around it: its parts are what's baked)
            m = x.matrix_world
            corners = [Vector(c) for c in x.bound_box]
            lo = Vector([min(c[i] for c in corners) for i in range(3)])
            hi = Vector([max(c[i] for c in corners) for i in range(3)])
            center = m @ ((lo + hi) / 2)
            axes, half = [], []
            for i in range(3):
                col = m.to_3x3().col[i]
                length = col.length or 1.0
                axes.append(tuple(col / length))
                half.append(max((hi[i] - lo[i]) / 2 * length, 1e-3) * upm)
            out.append(box_volume(tuple(center * upm), axes, half))
    return "".join(out)


def _view_volume(context) -> str | None:
    """The 3D viewport's view (this one, else the largest on screen) as a "bake only inside" volume."""
    from ..core.lightvolumes import view_volume
    space = context.space_data if context.space_data and context.space_data.type == "VIEW_3D" else None
    if space is None:
        areas = [a for a in context.screen.areas if a.type == "VIEW_3D"] if context.screen else []
        if not areas:
            return None
        space = max(areas, key=lambda a: a.width * a.height).spaces.active
    r3d = space.region_3d
    inv = r3d.view_matrix.inverted()
    eye = inv.translation
    forward = -(inv.to_3x3() @ __import__("mathutils").Vector((0.0, 0.0, 1.0)))
    s = context.scene.hammerless
    return view_volume(r3d.perspective_matrix, eye, forward, s.light_view_distance, s.units_per_meter)


_BUILDING: set = set()       # maps whose Build operator is still running (its nav, analysis or bake after the compile)


def _build_key(vmf: str) -> str:
    return os.path.normcase(os.path.splitext(os.path.abspath(vmf))[0])


class HL_OT_build(bpy.types.Operator):
    bl_idname = "hammerless.build"
    bl_label = "Build"
    bl_description = "Export, compile and (optionally) launch Left 4 Dead 2 on the map"

    @classmethod
    def description(cls, context, properties):
        if properties.vis_only:
            return ("Compile the map and work out its visibility, without lighting or the nav mesh. Build and "
                    "Build & Play then reuse it and only add what's missing")
        if properties.bake and properties.selected:
            return ("Bake only around the selected objects (when they've changed): the rest of the map keeps its "
                    "last bake. Build & Play bakes the whole map again")
        if properties.bake and properties.volume:
            return (f"Bake only inside '{properties.volume}' (a quick look at that area): the rest of the map "
                    "keeps its last bake. Build & Play bakes the whole map again")
        if properties.bake and properties.view:
            return ("Bake only what this viewport sees, out to the distance set (a quick look at one spot): the "
                    "rest of the map keeps its last bake. Build & Play bakes the whole map again")
        if properties.bake:
            return ("Bake the map's lighting quickly and show it (about a third of a full build: the visibility "
                    "step runs in its fast mode). Build & Play then reuses this lighting and only adds the full "
                    "visibility")
        if properties.play:
            return ("Build the map (walls, visibility, lighting, nav mesh), then start Left 4 Dead 2 on it. "
                    "Only what changed is redone")
        return ("Build the map without starting the game: walls, visibility, baked lighting and the nav mesh. "
                "Only what changed is redone. Then Play starts it, and Lighting > Baked Lighting shows it")

    play: BoolProperty(name="Play", default=True, options={"SKIP_SAVE"})
    bake: BoolProperty(name="Bake Lighting", default=False, options={"HIDDEN", "SKIP_SAVE"},
                       description="Lighting only: fast visibility, no nav mesh, then show the lighting")
    selected: BoolProperty(name="Bake Selected", default=False, options={"HIDDEN", "SKIP_SAVE"},
                           description="With Bake Lighting: bake only around the selected objects")
    volume: StringProperty(name="Bake Volume", default="", options={"HIDDEN", "SKIP_SAVE"},
                           description="With Bake Lighting: bake only inside this Control Volume (Only inside, "
                                       "Light Baking)")
    vis_only: BoolProperty(name="Compute Visibility", default=False, options={"HIDDEN", "SKIP_SAVE"},
                           description="Compile and run visibility only (no lighting, no nav mesh)")
    view: BoolProperty(name="Bake What the View Sees", default=False, options={"HIDDEN", "SKIP_SAVE"},
                       description="With Bake Lighting: only what the viewport sees, out to its Distance")

    _timer = None
    _job: cc.CompileJob | None = None
    _root: str | None = None
    _t0 = 0.0
    _export_s = 0.0
    _nav: dict | None = None        # our nav generator running alongside the compile

    def execute(self, context):
        import time
        self._t0 = time.time()
        vmf = os.path.join(work_dir(context), f"{context.scene.hammerless.map_name}.vmf")
        if cc.compile_running(vmf) or _build_key(vmf) in _BUILDING:     # (the compile, or the nav / analysis /
            #                                                             bake after it, still uses the files)
            self.report({"ERROR"}, "This map is still compiling: wait for it to finish (see the hammerless_log "
                                   "text), then build again")
            return {"CANCELLED"}
        if self.vis_only:
            self.play = self.bake = False
        if self.play and not self.bake:      # the game boots while the map exports and compiles
            early_root = game_root(context)
            if early_root:
                gamedir = cc.Tools(early_root).gamedir
                from ..core.gamefiles import MODE_ADDON
                if os.path.exists(os.path.join(gamedir, *MODE_ADDON.split("/"))) and not cc.game_running():
                    # (an installed game mode the add-on has changed: updated before the game reads it)
                    _write_mode_addon(gamedir, Report())
                cc.prestart_game(cc.Tools(early_root), launch_options(context.scene.hammerless))
        path, root, rep = export_vmf(self, context)
        self._export_s = time.time() - self._t0
        from .problems import store
        store(context, rep, new_build=True)
        surface_report(self, rep)
        if not path:
            return {"CANCELLED"}
        if not root:
            self.report({"ERROR"}, "Left 4 Dead 2 not found: set Settings > Game & Folders > L4D2 Folder to the 'Left 4 Dead 2' folder (the one with left4dead2.exe)")
            return {"CANCELLED"}
        tools = cc.Tools(root)
        if tools.missing():
            self.report({"ERROR"}, "L4D2 Authoring Tools not installed (Steam > Library > Tools > "
                                   "Left 4 Dead 2 Authoring Tools). VMF was still exported")
            return {"CANCELLED"}
        write_log(report_lines(rep) + ["", "Compiling..."])
        self._root = root
        self._owner = (context.scene.name, context.scene.hammerless.map_name)   # what's being built
        opts = compile_options(context.scene.hammerless)
        if self.bake:
            import dataclasses
            opts = cc.PRESETS[opts] if isinstance(opts, str) else opts
            if opts.rad == "SKIP":
                self.report({"ERROR"}, "Lighting Quality is Off: choose Fast or higher in Lighting")
                return {"CANCELLED"}
            opts = dataclasses.replace(opts, vis="FAST" if opts.vis != "SKIP" else "SKIP")
            self.play = False
        if self.vis_only:
            import dataclasses
            opts = cc.PRESETS[opts] if isinstance(opts, str) else opts
            if opts.vis == "SKIP":
                self.report({"ERROR"}, "Visibility Quality is Off: choose Fast or Full in Visibility")
                return {"CANCELLED"}
            opts = dataclasses.replace(opts, rad="SKIP")
        if cc.use_hlvrad(opts):          # (only our light compiler uses the sky's picture)
            sky_key, sky_note = export_sky(context, root, os.path.splitext(path)[0])
            if sky_note:
                write_log([sky_note], append=True)
            if sky_key:
                import dataclasses
                opts = dataclasses.replace(opts, sky_key=sky_key)
        elif context.scene.hammerless.sky_light != "FLAT" and opts.rad != "SKIP":
            write_log(["Sky Light from the sky needs the Hammerless light compiler: the sky lights the map with "
                       "one colour"], append=True)
        no_bake = rep.no_bake
        if self.bake and self.view:
            vol = _view_volume(context)
            if vol is None:
                self.report({"ERROR"}, "Baking what the view sees needs a 3D viewport")
                return {"CANCELLED"}
            if not cc.use_hlvrad(opts):
                self.report({"ERROR"}, "Baking what the view sees needs the Hammerless light compiler (Lighting > Advanced)")
                return {"CANCELLED"}
            no_bake += vol
        if self.bake and self.volume:
            vol = rep.bake_only.get(self.volume)
            if vol is None:
                self.report({"ERROR"}, f"Control Volume '{self.volume}' (Only inside, Light Baking) isn't in the map")
                return {"CANCELLED"}
            if not cc.use_hlvrad(opts):
                self.report({"ERROR"}, "Baking part of the map needs the Hammerless light compiler (Lighting > Advanced)")
                return {"CANCELLED"}
            no_bake += vol
        if self.bake and self.selected:
            vol = _selection_volume(context)
            if not vol:
                self.report({"ERROR"}, "Select the objects to bake first")
                return {"CANCELLED"}
            if not cc.use_hlvrad(opts):
                self.report({"ERROR"}, "Baking part of the map needs the Hammerless light compiler (Lighting > Advanced)")
                return {"CANCELLED"}
            no_bake += vol
        if self.bake and (self.view or self.volume or self.selected):
            import dataclasses
            opts = dataclasses.replace(opts, keep_light=True)    # (the rest keeps the last bake)
        try:
            import dataclasses
            opts = cc.PRESETS[opts] if isinstance(opts, str) else opts
            opts = dataclasses.replace(opts, assets=cc.asset_fingerprint(cc.Tools(root).gamedir,
                                                                         context.scene.hammerless.map_name))
        except OSError:
            pass
        if no_bake and opts.rad != "SKIP":
            import dataclasses
            if cc.use_hlvrad(opts):
                opts = dataclasses.replace(opts, no_bake=no_bake)
            else:
                write_log(["Control Volumes (Light Baking) need the Hammerless light compiler: Valve's vrad bakes everything"],
                          append=True)
        self._job = cc.CompileJob(tools, path, opts, skip_if_unchanged=True)
        self._nav = None
        s = context.scene.hammerless
        unanalyzed = s.nav_analysis == "BLENDER" and cc.nav_analyzed(tools, s.map_name) is False
        if self.bake or self.vis_only:
            pass                     # lighting / visibility only: the nav is made by the next Build / Build & Play
        elif s.nav_source == "BLENDER" and (not self._job.up_to_date() or needs_nav(context, root) or unanalyzed):
            self._nav = _start_nav_generation(path, rep.nav_regions, rep.nav_climbs, s.wall_climbs,
                                              after=self._job.prepared)
        self._job.snapshot_vis = self._nav is not None and s.nav_analysis == "BLENDER"
        try:
            self._job.start()
        except RuntimeError as ex:
            self.report({"ERROR"}, str(ex))
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        self._key = _build_key(self._job.base + ".vmf")
        _BUILDING.add(self._key)
        self.report({"INFO"}, "Compiling... (see the hammerless_log text block)")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC" and event.value == "PRESS":
            self.report({"WARNING"}, "Stopped watching the compile (it continues in the background; the next "
                                     "Build & Play makes the nav mesh)")
            return self._finish(context, {"CANCELLED"})
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            return self._step(context)
        except Exception as ex:          # never leave the timer and status text behind
            import traceback
            traceback.print_exc()
            self.report({"ERROR"}, f"Build & Play stopped: {ex}")
            return self._finish(context, {"CANCELLED"})

    def _start_analysis_early(self, context):
        """The nav analysis needs the compiled walls and visibility, not the lighting: start it as soon
        as the nav mesh is made and the compile is past its last visibility step (vrad may still run)."""
        nav = self._nav
        if nav is None or nav.get("analysis") is not None or context.scene.hammerless.nav_analysis != "BLENDER":
            return
        if nav["thread"].is_alive() or nav["mesh"] is None:
            return
        bsp = self._job.vis_bsp or (self._job.base + ".bsp" if self._job.done and not self._job.failed else None)
        if bsp:
            nav["analysis"] = _start_nav_analysis(nav["mesh"], self._job.vmf, bsp, self._root)
            nav["analysis_early"] = not self._job.done

    def _step(self, context):
        if self._job.bake_request:
            if not getattr(self, "_bake_shown", False):      # the bake blocks Blender: show the status first,
                self._bake_shown = True                       # bake on the next tick (after the redraw)
                context.workspace.status_text_set("Hammerless: baking the lighting with Cycles "
                                                  "(Blender pauses until it's done)...")
                return {"PASS_THROUGH"}
            self._bake_shown = False
            self._cycles_bake(context)
        new = self._job.poll()
        if new:
            for line in new:                    # vis progress from Hammerless's vis compiler, for the panel header
                if isinstance(line, str):
                    if line.startswith("vis ") and "%" in line:
                        BUILD_PROGRESS["vis"] = line
                    elif line.startswith("===="):
                        BUILD_PROGRESS["vis"] = ""
            write_log(new, append=True)
            shown = [ln for ln in new if isinstance(ln, str) and not ln.startswith("CDynamicFunction")]
            if shown:                                         # (not the compilers' DLL loading chatter)
                context.workspace.status_text_set(f"Hammerless: {shown[-1][:120]}")
        self._start_analysis_early(context)
        if not self._job.done:
            return {"PASS_THROUGH"}
        summ = self._job.summary
        if self._job.failed:
            msg = "Map leaks! Use 'Load Leak' to see where." if summ and summ.leaked else \
                  (summ.errors[0] if summ and summ.errors else "Compile failed, see the log")
            if summ and summ.errors and not summ.leaked:     # listed, so named brushes can be selected
                from .problems import store
                failed = Report()
                failed.errors = [f"Compiler: {e}" for e in summ.errors[:20]]
                store(context, failed)
            self.report({"ERROR"}, msg)
            return self._finish(context, {"CANCELLED"})
        import time
        owner = bpy.data.scenes.get(self._owner[0])     # (the scene that was built, even if another is shown now)
        s = (owner or context.scene).hammerless
        compiled = ("Map unchanged, skipped compiling" if self._job.skipped else
                    {"entities": "Updated entities only (geometry and lighting kept)",
                     "lighting": "Updated entities and relit (geometry kept)"}.get(self._job.plan, "Compiled"))
        timing = f"Export {self._export_s:.1f}s, " + (", ".join(f"{n} {t:.1f}s" for n, t in self._job.timings)
                                                      or "compile skipped")
        if self._job.lighting and not getattr(self, "_lighting_listed", False):
            from .problems import add_rows
            self._lighting_listed = True
            failed = "Bounce" in "".join(m for m, _l, _o in self._job.lighting) or any(
                "lighting failed" in m for m, _l, _o in self._job.lighting)
            add_rows(context, [("ERROR" if failed else "WARNING", m, o, loc) for m, loc, o in self._job.lighting])
            self.report({"WARNING"}, self._job.lighting[0][0][:200])
        if self._job.fallbacks and not getattr(self, "_fallbacks_listed", False):
            from .problems import add_rows
            self._fallbacks_listed = True       # (Valve's tool ran instead of ours: worth knowing, and reporting)
            add_rows(context, [("WARNING", m, "", None) for m in self._job.fallbacks])
            self.report({"WARNING"}, self._job.fallbacks[0][:200])
        if self._nav is not None:
            if self._nav["thread"].is_alive():
                context.workspace.status_text_set(f"Hammerless: building the nav mesh: {self._nav['stage']}...")
                return {"PASS_THROUGH"}
            if self._nav["mesh"] is None:
                self.report({"WARNING"}, f"Nav mesh couldn't be built in Blender ({self._nav['error']}); "
                                         "the game will make it")
                self._nav = None
            elif s.nav_analysis == "BLENDER" and not self._nav.get("analysis_finished"):
                an = self._nav.get("analysis")
                if an is None:
                    an = self._nav["analysis"] = _start_nav_analysis(self._nav["mesh"], self._job.vmf,
                                                                     self._job.base + ".bsp", self._root)
                if an["thread"].is_alive():
                    context.workspace.status_text_set(f"Hammerless: analyzing the nav mesh: {an['stage']}...")
                    return {"PASS_THROUGH"}
                self._nav["analysis_finished"] = True
                if self._job.vis_bsp and os.path.exists(self._job.vis_bsp):
                    try:
                        os.remove(self._job.vis_bsp)
                    except OSError:
                        pass
                if not an["done"]:
                    self.report({"WARNING"}, f"Nav analysis in Blender failed ({an['error']}); the game will "
                                             "analyze the nav mesh")
                return {"PASS_THROUGH"}
            else:
                analyzed = bool(self._nav.get("analysis") and self._nav["analysis"]["done"])
                self._nav["analyzed"] = analyzed
                cc.write_generated_nav(cc.Tools(self._root), self._owner[1], self._nav["mesh"], analyzed=analyzed)
                if analyzed:
                    timing += f", nav analysis {self._nav['analysis']['seconds']:.1f}s" + (
                        " (alongside the lighting)" if self._nav.get("analysis_early") else "")
                for problem in self._nav["mesh"].problems:
                    self.report({"WARNING"}, problem)
                if self._nav["mesh"].problems:
                    from .problems import add_rows
                    add_rows(context, [("WARNING", p, _quoted_object(p), None) for p in self._nav["mesh"].problems],
                             kind="navgen")
                if self._nav["mesh"].problems:
                    write_log(self._nav["mesh"].problems, append=True)
                s.generate_nav = False
                timing += f", nav mesh {self._nav['seconds']:.1f}s (during the compile)"
        write_log([timing], append=True)       # (once: the waits above return before this)
        if self.play and (context.scene.name, context.scene.hammerless.map_name) != self._owner:
            self.report({"WARNING"}, f"{compiled} '{self._owner[1]}', but the scene or Map Name changed meanwhile: "
                                     "not launching (press Play)")
            return self._finish(context, {"FINISHED"})
        if self.play:
            written = self._nav is not None
            analyzed = written and self._nav.get("analyzed", False)
            nav = needs_nav(context, self._root, by_game=True) and not written
            nav_note = ("" if analyzed else " (the game adds its visibility data: one reload)" if written else
                        " (building its nav mesh first: the map reloads twice)" if nav else "")
            launch(context, self._root, nav_written=written, analyzed=analyzed)
            _watch_load(time.time() - self._t0, timing, nav)
            self.report({"INFO"}, f"{compiled}. Launching L4D2 on {s.map_name}{nav_note}  [{timing}]")
        elif self.vis_only:
            self.report({"INFO"}, f"{compiled}: visibility worked out. Build and Build & Play reuse it  [{timing}]")
        elif self.bake:
            from . import lightview
            err = lightview.load(context)
            if err:
                self.report({"WARNING"}, err)
            else:
                s.show_lightmap = True
                self.report({"INFO"}, f"{compiled}: lighting baked and shown. Build & Play reuses it  [{timing}]")
        else:
            made = self._nav is not None and self._nav.get("mesh") is not None
            note = (" Nav mesh made" + (" and analyzed" if self._nav.get("analyzed") else "") if made else "")
            self.report({"INFO"}, f"{compiled}.{note}  [{timing}]")
        return self._finish(context, {"FINISHED"})

    def _eye_points(self, context):
        """Where players can see from, for skipping faces nobody sees: over this build's nav mesh (waiting
        for it if it's still being made), else the map's nav file in the game. None: bake every face."""
        from ..core.lightbake import eye_points
        mesh = None
        if self._nav is not None:
            self._nav["thread"].join()
            mesh = self._nav.get("mesh")
        if mesh is None and self._root:
            from ..core.navfile import load_nav
            path = os.path.join(cc.Tools(self._root).maps_dir, self._owner[1] + ".nav")
            try:
                mesh = load_nav(path) if os.path.exists(path) else None
            except Exception:            # an unreadable nav: just bake everything
                mesh = None
        if mesh is None or not mesh.areas:
            return None
        return eye_points(mesh.areas)

    def _cycles_bake(self, context):
        """The compile waits after vrad: bake its lighting with Cycles here (Blender's main thread)."""
        bsp = self._job.bake_request
        ok = True
        try:
            from .cyclesbake import bake_bsp
            opts = self._job._opts
            lines = bake_bsp(bsp, context.scene.hammerless.units_per_meter, opts.cycles_samples,
                             opts.cycles_denoise, opts.cycles_stitch, self._eye_points(context))
        except Exception as ex:          # the map still has vrad's lighting
            import traceback
            traceback.print_exc()
            lines = [f"!! Cycles bake failed ({ex}): the map keeps vrad's lighting"]
            self.report({"WARNING"}, lines[0][3:])
            ok = False
        self._job.bake_finished(lines, ok)

    def cancel(self, context):
        """Blender ended the operator itself (File > Open / New, the window closed): let the job end too."""
        if self._job is not None:
            self._job.bake_abandoned = True
        _BUILDING.discard(getattr(self, "_key", None))
        try:
            context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        BUILD_PROGRESS["vis"] = ""

    def _finish(self, context, result):
        _BUILDING.discard(getattr(self, "_key", None))
        if self._job is not None:
            self._job.bake_abandoned = True      # stopped watching: nobody is left to bake
        snap = self._job.base + ".analysis.bsp" if self._job is not None else None
        if snap and os.path.exists(snap) and not (self._nav and self._nav.get("analysis")
                                                  and self._nav["analysis"]["thread"].is_alive()):
            try:
                os.remove(snap)              # the analysis copy is only needed while the build runs
            except OSError:
                pass
        context.window_manager.event_timer_remove(self._timer)
        BUILD_PROGRESS["vis"] = ""
        context.workspace.status_text_set(None)
        return result


class HL_OT_start_fresh(bpy.types.Operator):
    bl_idname = "hammerless.start_fresh"
    bl_label = "Start Fresh"
    bl_description = ("Delete this map's whole build at once: the compiled map with its baked lighting, and the nav "
                      "mesh with its analysis, and forget them, so the next Build & Play does everything from "
                      "scratch. Your .blend isn't touched")

    def invoke(self, context, event):
        if bpy.app.version < (4, 1, 0):         # (4.0's confirm popup takes no title or message)
            return context.window_manager.invoke_confirm(self, event)
        return context.window_manager.invoke_confirm(
            self, event, title="Start Fresh?",
            message=f"Delete the build of '{context.scene.hammerless.map_name}' (compiled map, lighting, nav mesh)? "
                    "The next Build & Play rebuilds everything", confirm_text="Delete Build")

    def execute(self, context):
        s = context.scene.hammerless
        base = os.path.join(work_dir(context), s.map_name)
        if cc.compile_running(base + ".vmf"):
            self.report({"ERROR"}, "The map is building: wait for it to finish")
            return {"CANCELLED"}
        root = game_root(context)
        tools = cc.Tools(root) if root else None
        locked = cc.clear_build(tools, base, s.map_name) + (cc.clear_nav(tools, s.map_name) if tools else [])
        from ..core import navanalyze, navpredict
        from . import lightview, navview
        navpredict._last.update(key=None, mesh=None)       # no reusing a nav mesh made earlier this session
        navanalyze._HULL_CACHE.clear()
        lightview.clear()
        navview._state.update(path=None, mtime=None, mesh=None, report=None, batches=None, key=None, source=None,
                              vis=None)
        navview._redraw()
        if locked:
            self.report({"WARNING"}, f"Couldn't delete {', '.join(locked)}: the game has the map loaded. Load "
                                     "another map (or close the game) and press Start Fresh again")
        else:
            self.report({"INFO"}, "Build deleted: the next Build & Play does everything from scratch")
        return {"FINISHED"}


class HL_OT_launch(bpy.types.Operator):
    bl_idname = "hammerless.launch"
    bl_label = "Play"
    bl_description = "Start Left 4 Dead 2 on the last build (without building again)"

    def execute(self, context):
        import time
        root = game_root(context)
        if not root:
            self.report({"ERROR"}, "Left 4 Dead 2 not found: set Settings > Game & Folders > L4D2 Folder to the 'Left 4 Dead 2' folder (the one with left4dead2.exe)")
            return {"CANCELLED"}
        s = context.scene.hammerless
        if not os.path.exists(os.path.join(cc.Tools(root).maps_dir, f"{s.map_name}.bsp")):
            self.report({"ERROR"}, f"'{s.map_name}' hasn't been built yet: press Build & Play (or Build)")
            return {"CANCELLED"}
        if cc.compile_running(os.path.join(work_dir(context), f"{s.map_name}.vmf")):
            self.report({"ERROR"}, "The map is still compiling: launch when it has finished")
            return {"CANCELLED"}
        nav = needs_nav(context, root, by_game=True)
        launch(context, root)
        _watch_load(0.0, "Play", nav)
        return {"FINISHED"}


class HL_OT_load_leak(bpy.types.Operator):
    bl_idname = "hammerless.load_leak"
    bl_label = "Load Leak"
    bl_description = ("The map has a hole to the outside (only possible with Auto Seal off). Draws a red line "
                      "from inside the map to the hole")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.hammerless
        lin = os.path.join(work_dir(context), f"{s.map_name}.lin")
        if not os.path.exists(lin):
            self.report({"INFO"}, "No leak file. The last compile didn't leak")
            return {"CANCELLED"}
        pts = cc.read_pointfile(lin)
        if len(pts) < 2:
            self.report({"INFO"}, "The leak file has no path in it (the last compile probably didn't leak)")
            return {"CANCELLED"}
        for c in [c for c in bpy.data.curves if c.name.startswith("HL_leak") and c.users == 0]:
            bpy.data.curves.remove(c)        # earlier leak lines
        curve = bpy.data.curves.new("HL_leak", "CURVE")
        curve.dimensions = "3D"
        curve.bevel_depth = 2 / s.units_per_meter
        spline = curve.splines.new("POLY")
        spline.points.add(len(pts) - 1)
        for p, (x, y, z) in zip(spline.points, pts):
            p.co = (x / s.units_per_meter, y / s.units_per_meter, z / s.units_per_meter, 1)
        old = bpy.data.objects.get("HL_leak")
        if old:
            old_curve = old.data
            bpy.data.objects.remove(old)
            if old_curve.users == 0:
                bpy.data.curves.remove(old_curve)
        obj = bpy.data.objects.new("HL_leak", curve)
        obj.hammerless.role = "IGNORE"
        obj.color = (1, 0, 0, 1)
        obj.show_in_front = True
        context.scene.collection.objects.link(obj)
        self.report({"INFO"}, "Leak line added (HL_leak). Follow it from inside the map to the hole")
        return {"FINISHED"}


_SKY_ITEMS: list[tuple[str, str, str]] = []


_SKY_TRIED = [0.0]


def _sky_items(self, context):
    import time
    if _SKY_ITEMS and _SKY_ITEMS[0][0] != "sky_day01_09_hdr":
        return _SKY_ITEMS          # filled from the game: keep it (Blender holds on to its strings)
    if _SKY_ITEMS and time.monotonic() - _SKY_TRIED[0] < 10.0:
        return _SKY_ITEMS          # (no game yet: looked for it a moment ago, not on every redraw)
    _SKY_TRIED[0] = time.monotonic()
    root = game_root(context)
    content = game_content(root)
    names = sorted({m[len("skybox/"):-2] for m in content.materials("skybox/") if m.endswith("bk")}) if content else []
    if names or not _SKY_ITEMS:
        _SKY_ITEMS.clear()
        _SKY_ITEMS.extend((n, n, "Skybox " + n) for n in names)
        if not _SKY_ITEMS:
            _SKY_ITEMS.append(("sky_day01_09_hdr", "sky_day01_09_hdr", ""))
    return _SKY_ITEMS


class HL_OT_pick_sky(bpy.types.Operator):
    bl_idname = "hammerless.pick_sky"
    bl_label = "Pick Sky"
    bl_description = "Choose from every sky in Left 4 Dead 2"
    bl_options = {"REGISTER", "UNDO"}
    bl_property = "sky"

    sky: EnumProperty(name="Sky", items=_sky_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.scene.hammerless.skyname = self.sky
        return {"FINISHED"}


_MODEL_ITEMS: list[tuple[str, str, str]] = []
_MATERIAL_ITEMS: list[tuple[str, str, str]] = []


def _model_items(self, context):
    if not _MODEL_ITEMS:
        content = game_content(game_root(context))
        if content:
            for f in sorted(f for f in content.files if f.endswith(".mdl")):
                _MODEL_ITEMS.append((f, f[len("models/"):], f))
    return _MODEL_ITEMS or [("", "(Left 4 Dead 2 not found)", "")]


def _material_items(self, context):
    if not _MATERIAL_ITEMS:
        content = game_content(game_root(context))
        if content:
            for m in content.materials():
                _MATERIAL_ITEMS.append((m, m, m))
    return _MATERIAL_ITEMS or [("", "(Left 4 Dead 2 not found)", "")]


class HL_OT_pick_model(bpy.types.Operator):
    bl_idname = "hammerless.pick_model"
    bl_label = "Pick Game Model"
    bl_description = "Search all Left 4 Dead 2 models and use one for this entity"
    bl_property = "model"
    bl_options = {"REGISTER", "UNDO"}

    model: EnumProperty(name="Model", items=_model_items)

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.model:
            return {"CANCELLED"}
        s = context.scene.hammerless
        active = context.object
        if active is None or not active.hammerless.classname:
            self.report({"WARNING"}, "Select the prop to give this model")
            return {"CANCELLED"}
        for obj in {active, *[o for o in context.selected_objects
                              if o.hammerless.classname == active.hammerless.classname]}:
            hs = obj.hammerless
            kv = next((x for x in hs.keyvalues if x.key == "model"), None) or hs.keyvalues.add()
            kv.key, kv.value = "model", self.model
            if obj.type == "MESH" and hs.role in ("ENTITY", "AUTO"):
                obj.data = preview_mesh(hs.classname, s.units_per_meter, self.model)
                style_entity_object(obj)
        self.report({"INFO"}, self.model)
        return {"FINISHED"}


class HL_OT_pick_material(bpy.types.Operator):
    bl_idname = "hammerless.pick_material"
    bl_label = "Pick Game Material"
    bl_description = "Search all Left 4 Dead 2 materials and use one on the active material slot"
    bl_property = "material"
    bl_options = {"REGISTER", "UNDO"}

    material: EnumProperty(name="Material", items=_material_items)

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "MESH"

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.material:
            return {"CANCELLED"}
        mat = game_material(self.material)
        for obj in context.selected_objects or [context.object]:
            if obj.type != "MESH" or obj.hammerless.role == "ENTITY" or obj.data.name.startswith("HL_"):
                continue                  # entity previews share their mesh: not walls
            if not obj.material_slots:
                obj.data.materials.append(mat)
            else:
                obj.material_slots[obj.active_material_index].material = mat
        self.report({"INFO"}, self.material)
        return {"FINISHED"}


class HL_OT_refresh_previews(bpy.types.Operator):
    bl_idname = "hammerless.refresh_previews"
    bl_label = "Refresh Previews"
    bl_description = ("Show real game textures on game materials (Material Preview view) and real "
                      "3D models on props and items")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        done = failed = 0
        for mat in bpy.data.materials:
            hs = mat.hammerless
            if mat.name.startswith("HL_"):
                continue                  # Hammerless's own preview materials
            if not hs.source_material and "/" in mat.name and not mat.name.startswith("hammerless/"):
                hs.source_material = mat.name.lower()
            if not hs.source_material or hs.source_material.startswith("tools/"):
                continue
            if refresh_material_preview(mat):
                done += 1
            else:
                failed += 1
        models = 0
        s = context.scene.hammerless
        for obj in context.scene.objects:
            hs = obj.hammerless
            if obj.type != "MESH" or hs.role != "ENTITY" or not hs.classname:
                continue
            model = preview_model(hs.classname, {kv.key: kv.value for kv in hs.keyvalues})
            if not model:
                continue
            mesh = preview_mesh(hs.classname, s.units_per_meter, model)
            if mesh is not obj.data:
                obj.data = mesh
                style_entity_object(obj)
                models += 1
        self.report({"INFO"} if not failed else {"WARNING"},
                    f"Previewed {done} material(s), {models} model(s)"
                    + (f"; {failed} material(s) not found in the game" if failed else ""))
        return {"FINISHED"}


class HL_OT_load_game_data(bpy.types.Operator):
    bl_idname = "hammerless.load_game_data"
    bl_label = "Load Game Data"
    bl_description = "Read the game's surface list (friction etc.) and sky names"

    def execute(self, context):
        root = game_root(context)
        if not game_content(root):
            self.report({"ERROR"}, "Left 4 Dead 2 not found: set Settings > Game & Folders > L4D2 Folder to the 'Left 4 Dead 2' folder (the one with left4dead2.exe)")
            return {"CANCELLED"}
        from .props import SURFACE_ITEMS
        self.report({"INFO"}, f"Loaded {len(SURFACE_ITEMS) - 1} surfaces")
        return {"FINISHED"}


class HL_OT_kv_add(bpy.types.Operator):
    bl_idname = "hammerless.kv_add"
    bl_label = "Add Keyvalue"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        context.object.hammerless.keyvalues.add()
        return {"FINISHED"}


class HL_OT_kv_remove(bpy.types.Operator):
    bl_idname = "hammerless.kv_remove"
    bl_label = "Remove Keyvalue"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        hs = context.object.hammerless
        if 0 <= hs.keyvalues_index < len(hs.keyvalues):
            hs.keyvalues.remove(hs.keyvalues_index)
        return {"FINISHED"}


class HL_OT_output_add(bpy.types.Operator):
    bl_idname = "hammerless.output_add"
    bl_label = "Add Output"
    bl_description = "Add an output: when this entity's event fires, tell another entity to do something"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        hs = context.object.hammerless
        hs.outputs.add()
        hs.outputs_index = len(hs.outputs) - 1
        return {"FINISHED"}


class HL_OT_output_remove(bpy.types.Operator):
    bl_idname = "hammerless.output_remove"
    bl_label = "Remove Output"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        hs = context.object.hammerless
        if 0 <= hs.outputs_index < len(hs.outputs):
            hs.outputs.remove(hs.outputs_index)
        return {"FINISHED"}


class HL_OT_kv_set(bpy.types.Operator):
    bl_idname = "hammerless.kv_set"
    bl_label = "Set"
    bl_description = "Set this on the selected volumes"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    key: StringProperty()
    value: StringProperty()

    def execute(self, context):
        obj = context.object
        if obj is None:
            return {"CANCELLED"}
        cls = obj.hammerless.classname
        for o in {obj, *[o for o in context.selected_objects if o.hammerless.classname == cls]}:
            kv = next((k for k in o.hammerless.keyvalues if k.key == self.key), None)
            if kv is None:
                kv = o.hammerless.keyvalues.add()
                kv.key = self.key
            kv.value = self.value
        from .props import forget_bake_volumes
        forget_bake_volumes()                # (a Control Volume's mode or ticks: the Bake dropdown's choices)
        return {"FINISHED"}


class HL_OT_reset_keyvalues(bpy.types.Operator):
    bl_idname = "hammerless.reset_keyvalues"
    bl_label = "Reset to Defaults"
    bl_options = {"UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        hs = context.object.hammerless
        hs.keyvalues.clear()
        for k, v in default_keyvalues(hs.classname).items():
            kv = hs.keyvalues.add()
            kv.key, kv.value = k, v
        return {"FINISHED"}


CLASSES = (HL_OT_pick_sky, HL_OT_pick_model, HL_OT_pick_material, HL_OT_refresh_previews, HL_OT_load_game_data, HL_OT_add_entity, HL_OT_set_entity_class, HL_OT_set_brush_entity, HL_OT_add_preset, HL_OT_validate,
           HL_OT_export_vmf, HL_OT_build, HL_OT_start_fresh, HL_OT_launch, HL_OT_load_leak,
           HL_OT_kv_add, HL_OT_kv_remove, HL_OT_output_add, HL_OT_output_remove, HL_OT_reset_keyvalues, HL_OT_kv_set)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for t in _TIMERS:
        if bpy.app.timers.is_registered(t):
            bpy.app.timers.unregister(t)
    _TIMERS.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
