"""A Hammer map that uses a mapper's own content folder (one added to gameinfo.txt's SearchPaths), imported.

    blender --background --factory-startup --python tests/blender/check_custom_content.py

Needs the game. Makes a content folder outside the game holding a copy of one game material and one game model
under new names, a copy of gameinfo.txt that lists it (the game's own file is not touched), and a small map that
uses both. Checks the imported brush shows the texture and the prop shows the model. Prints CUSTOM lines; exit 1 on
a failure.
"""
import os
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless

hammerless.register()
from hammerless.blender import ops
from hammerless.core import vpk
from hammerless.core.gamematerials import base_texture

root = ops.game_root(bpy.context)
if not root:
    print("CUSTOM skipped: no game")
    sys.exit(0)
stock = vpk.GameContent(root)
tmp = tempfile.mkdtemp(prefix="hl_test_custom_")
custom = os.path.join(tmp, "left4dead2_custom")


def put(rel, data):
    full = os.path.join(custom, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)


SRC_MAT = "concrete/concrete_floor_01"
tex = base_texture(stock, SRC_MAT)
put("materials/hl_test_custom/Wall.vmt", b'"LightmappedGeneric"\n{\n\t"$basetexture" "hl_test_custom/wall_d"\n}\n')
put("materials/hl_test_custom/wall_d.vtf", stock.read(f"materials/{tex}.vtf"))
SRC_MODEL = "models/props_junk/dumpster"
for ext in (".mdl", ".vvd", ".dx90.vtx", ".phy"):
    data = stock.read(SRC_MODEL + ext)
    if data:
        put("models/hl_test_custom/thing" + ext, data)

with open(vpk.gameinfo_path(root), encoding="latin-1") as f:
    text = f.read()
i = text.index("{", text.lower().index("searchpaths")) + 1
fake = os.path.join(tmp, "gameinfo.txt")
with open(fake, "w", encoding="latin-1") as f:
    f.write(text[:i] + '\n\t\t\tGame\t"%s"\n' % custom.replace("\\", "/") + text[i:])
vpk.gameinfo_path = lambda _root: fake


def side(k, plane):
    return ('\t\tside\n\t\t{\n\t\t\t"id" "%d"\n\t\t\t"plane" "%s"\n\t\t\t"material" "HL_TEST_CUSTOM/WALL"\n'
            '\t\t\t"uaxis" "[1 0 0 0] 0.25"\n\t\t\t"vaxis" "[0 -1 0 0] 0.25"\n\t\t\t"rotation" "0"\n'
            '\t\t\t"lightmapscale" "16"\n\t\t\t"smoothing_groups" "0"\n\t\t}\n' % (k, plane))


PLANES = ["(-64 64 16) (64 64 16) (64 -64 16)", "(-64 -64 0) (64 -64 0) (64 64 0)",
          "(-64 64 16) (-64 -64 16) (-64 -64 0)", "(64 64 0) (64 -64 0) (64 -64 16)",
          "(64 64 16) (-64 64 16) (-64 64 0)", "(64 -64 0) (-64 -64 0) (-64 -64 16)"]
vmf = ('versioninfo\n{\n\t"mapversion" "1"\n}\nworld\n{\n\t"id" "1"\n\t"classname" "worldspawn"\n'
       '\tsolid\n\t{\n\t\t"id" "2"\n' + "".join(side(3 + k, p) for k, p in enumerate(PLANES)) + '\t}\n}\n'
       'entity\n{\n\t"id" "20"\n\t"classname" "prop_static"\n\t"model" "models/hl_test_custom/thing.mdl"\n'
       '\t"origin" "0 0 16"\n\t"angles" "0 0 0"\n}\n')
path = os.path.join(tmp, "hl_test_custom_map.vmf")
with open(path, "w", encoding="latin-1") as f:
    f.write(vmf)

for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
r = bpy.ops.hammerless.import_vmf(filepath=path)
fails = []
mat = bpy.data.materials.get("hl_test_custom/wall")
img = next((n.image for n in (mat.node_tree.nodes if mat and mat.node_tree else []) if n.type == "TEX_IMAGE"), None)
print("CUSTOM import", r, "material", bool(mat), "texture", tuple(img.size) if img else None)
if img is None or img.size[0] < 4:
    fails.append("the brush's custom material has no texture")
prop = next((o for o in bpy.data.objects if o.hammerless.classname == "prop_static"), None)
verts = len(prop.data.vertices) if prop and prop.type == "MESH" else 0
stock_verts = 0
try:
    stock_verts = len(ops.preview_mesh("prop_static", bpy.context.scene.hammerless.units_per_meter,
                                       SRC_MODEL + ".mdl").vertices)
except Exception as ex:
    print("CUSTOM (stock model mesh:", ex, ")")
print("CUSTOM prop", bool(prop), "vertices", verts, "the same model from the game", stock_verts)
if verts < 20 or (stock_verts and verts != stock_verts):
    fails.append("the custom model isn't shown")
content = ops.game_content(root)
print("CUSTOM listed:", "hl_test_custom/wall" in content.materials("hl_test_custom"),
      content.has_model("models/hl_test_custom/thing.mdl"))
print("CUSTOM", "FAILED: " + "; ".join(fails) if fails else "OK")
sys.exit(1 if fails else 0)
