"""A prop whose see-through material lives in a mapper's own content folder (gameinfo.txt's SearchPaths) casts a
see-through shadow, not a solid one: the light compiler is told the content folders (-contentdir).

    blender --background --factory-startup --python tests/blender/check_custom_texture_shadow.py

Needs the game and the compilers. Takes a game prop that has an alpha-tested material, makes a copy of it whose
materials are in a folder outside the game (a copy of gameinfo.txt lists it: the game's file isn't touched), and
bakes a small map twice: as the build does now, and without the content folders (how it was). The light compiler
must read the material's see-through texture the first time only. Prints TEXSHADOW lines; exit 1 on a failure.
"""
import os
import struct
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless

hammerless.register()
from hammerless.blender import ops
from hammerless.core import compile as cc
from hammerless.core import mdl, vpk
from hammerless.core.gamematerials import read_vmt

root = ops.game_root(bpy.context)
if not root:
    print("TEXSHADOW skipped: no game")
    sys.exit(0)
stock = vpk.GameContent(root)
tmp = tempfile.mkdtemp(prefix="hl_test_texshadow_")
custom = os.path.join(tmp, "left4dead2_custom")
NEW = "hl_test_texshadow"


def put(rel, data):
    full = os.path.join(custom, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)


def pick():
    """(model path, its bytes, the material folder as the model spells it, [material paths]) of a game prop with an
    alpha-tested material, big enough to throw a shadow."""
    for path in sorted(f for f in stock.files if f.startswith(("models/props_foliage/", "models/props_urban/fence"))
                       and f.endswith(".mdl")):
        data = stock.read(path)
        mesh = mdl.load_model(stock, path)
        if not data or mesh is None:
            continue
        mats = [m for m in (mdl.find_material(stock, mesh, t) for t in mesh.materials) if m]
        if not mats or not any("$alphatest" in read_vmt(stock, m) for m in mats):
            continue
        folders = {m.rsplit("/", 1)[0] for m in mats}
        if len(folders) != 1:
            continue
        folder = folders.pop()
        for spelled in (folder.replace("/", "\\") + "\\", folder + "/", folder.replace("/", "\\"), folder):
            for cased in (spelled, spelled.lower()):
                k = data.lower().count(cased.lower().encode())
                if k == 1:
                    at = data.lower().index(cased.lower().encode())
                    lo, hi = vpk.model_bounds(data)
                    if max(hi[i] - lo[i] for i in range(3)) < 48:
                        break
                    return path, data, data[at:at + len(cased)].decode("latin-1"), mats
    return None


found = pick()
if not found:
    print("TEXSHADOW skipped: no game prop with one alpha-tested material folder found")
    sys.exit(0)
src_model, data, spelled, mats = found
sep = "\\" if "\\" in spelled else "/"
body = spelled.strip("\\/")
tail = spelled[len(body):] if spelled.startswith(body) else ""
new_folder = ("models" + sep + NEW).ljust(len(body), "x")[:len(body)]
assert len(new_folder) == len(body) and len(body) > len("models" + sep) + 4, (body, new_folder)
at = data.lower().index(spelled.lower().encode())
patched = data[:at] + (new_folder + tail).encode("latin-1") + data[at + len(spelled):]
new_dir = new_folder.replace("\\", "/")
base = src_model[:-4]
for ext in (".vvd", ".dx90.vtx", ".phy"):
    extra = stock.read(base + ext)
    if extra:
        put(f"models/{NEW}/prop{ext}", extra)
put(f"models/{NEW}/prop.mdl", patched)
for m in mats:
    put(f"materials/{new_dir}/{m.rsplit('/', 1)[1]}.vmt", stock.read(f"materials/{m}.vmt"))
print("TEXSHADOW prop:", src_model, "| materials", [m.rsplit("/", 1)[1] for m in mats], "| now in", new_dir)

with open(vpk.gameinfo_path(root), encoding="latin-1") as f:
    text = f.read()
i = text.index("{", text.lower().index("searchpaths")) + 1
fake = os.path.join(tmp, "gameinfo.txt")
with open(fake, "w", encoding="latin-1") as f:
    f.write(text[:i] + '\n\t\t\tGame\t"%s"\n' % custom.replace("\\", "/") + text[i:])
vpk.gameinfo_path = lambda _root: fake

for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
s = bpy.context.scene.hammerless
out = os.path.join(tmp, "build")
s.map_name, s.output_dir, s.sound_mode = "hl_test_texshadow", out, "OFF"
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.25))
bpy.context.object.scale = (24, 24, 0.5)
bpy.ops.object.empty_add(location=(0, 0, 0))
prop = bpy.context.object
prop.hammerless.role, prop.hammerless.classname = "ENTITY", "prop_static"
for kv in prop.hammerless.keyvalues:
    if kv.key == "model":
        kv.value = f"models/{NEW}/prop.mdl"
        break
else:
    kv = prop.hammerless.keyvalues.add()
    kv.key, kv.value = "model", f"models/{NEW}/prop.mdl"
bpy.ops.object.light_add(type="POINT", location=(-3, 0, 1.5))
bpy.context.object.data.energy = 3000
bpy.ops.object.empty_add(location=(4, 4, 0.2))
sp = bpy.context.object
sp.hammerless.role, sp.hammerless.classname = "ENTITY", "info_survivor_position"
try:
    bpy.ops.hammerless.export_vmf()
except RuntimeError as ex:
    print("TEXSHADOW export:", str(ex).splitlines()[0][:200])
vmf = os.path.join(out, "hl_test_texshadow.vmf")
tools = cc.Tools(root)


def light_sum(bsp):
    data = open(bsp, "rb").read()
    total = 0.0
    for lump in (53, 8):
        _v, ofs, ln, _f = struct.unpack_from("<iiii", data, 8 + 16 * lump)
        if ln:
            for k in range(ofs, ofs + ln - 3, 4):
                r, g, b, e = data[k], data[k + 1], data[k + 2], struct.unpack_from("b", data, k + 3)[0]
                total += (r + g + b) * 2.0 ** e
            return total, ln
    return 0.0, 0


def bake(label, with_folders):
    real = cc.content_dir_args
    if not with_folders:
        cc.content_dir_args = lambda _tools: []
    try:
        job = cc.CompileJob(tools, vmf, "FAST").start().wait()
    finally:
        cc.content_dir_args = real
    loaded = [l.strip() for l in job.log if "loaded alpha texture" in l.lower()]
    if job.failed:
        print("TEXSHADOW", label, "FAILED TO BUILD:", job.summary.errors[:3])
        print("\n".join(job.log[-25:]))
        return None
    total, size = light_sum(os.path.join(out, "hl_test_texshadow.bsp"))
    print(f"TEXSHADOW {label}: light on the map {total:.0f} ({size} bytes of lightmaps); see-through textures read:",
          loaded[:2])
    return total, bool(loaded)


now = bake("with the content folders (now)", True)
was = bake("without them (before)", False)
for folder in ("maps",):                         # (the test map out of the game again)
    for f in os.listdir(os.path.join(tools.gamedir, folder)):
        if f.startswith("hl_test_texshadow"):
            try:
                os.remove(os.path.join(tools.gamedir, folder, f))
            except OSError:
                pass
# (the light compiler says when it reads a see-through texture: it must for the custom material now, and it
# could not before; the light on the map then differs)
ok = now is not None and was is not None and now[1] and not was[1] and now[0] != was[0]
print("TEXSHADOW", "OK" if ok else "FAILED: the custom material's texture wasn't read for the shadow")
sys.exit(0 if ok else 1)
