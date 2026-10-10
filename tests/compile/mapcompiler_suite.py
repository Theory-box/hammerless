"""Hammerless's map compiler (hlvbsp) against Valve's vbsp, map by map.

Each test map below is generated here (no game files), compiled by Valve's vbsp (the reference)
and by hlvbsp, and compared lump by lump (bspdump) and by portal file. The displacement physics
lump (28) is skipped: vbsp's own runs differ there.

    python tests/compile/mapcompiler_suite.py              # every map
    python tests/compile/mapcompiler_suite.py water cube   # maps whose name contains these
    python tests/compile/mapcompiler_suite.py --list

Needs Left 4 Dead 2 (for vbsp.exe, vphysics.dll and the materials/models the maps use). Output goes
to %TEMP%/hammerless_mapcompiler_suite; Valve's reference is reused while a map's .vmf is unchanged.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import bspdump  # noqa: E402
from hammerless.core.compile import HLVBSP, find_game_root  # noqa: E402

OUT = os.path.join(tempfile.gettempdir(), "hammerless_mapcompiler_suite")
SKIP_LUMPS = {28}         # displacement physics: differs between vbsp's own runs


# ---------------------------------------------------------------- building blocks
class Map:
    """A .vmf being built: brushes with known side ids (sides["tag.top"] etc.), entities."""

    def __init__(self):
        self._id = 0
        self.sides: dict[str, int] = {}
        self.solids: list[str] = []
        self.ents: list[str] = []

    def nid(self) -> int:
        self._id += 1
        return self._id

    def side(self, key, p1, p2, p3, mat, u, v, extra=""):
        i = self.nid()
        if key:
            self.sides[key] = i
        return (f'side {{ "id" "{i}" "plane" "({p1[0]} {p1[1]} {p1[2]}) ({p2[0]} {p2[1]} {p2[2]}) '
                f'({p3[0]} {p3[1]} {p3[2]})" "material" "{mat}" "uaxis" "[{u} 0] 0.25" "vaxis" "[{v} 0] 0.25" '
                f'"rotation" "0" "lightmapscale" "16" "smoothing_groups" "0" {extra}}}')

    def box(self, lo, hi, mat="DEV/DEV_MEASUREGENERIC01B", tag="", mats=None, top_extra=""):
        """mats: per side (top, bottom, -x, +x, +y, -y); top_extra: e.g. a dispinfo block."""
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        m = mats or [mat] * 6
        s = [self.side(tag + "top", (x0, y1, z1), (x1, y1, z1), (x1, y0, z1), m[0], "1 0 0", "0 -1 0", top_extra),
             self.side(tag + "bottom", (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), m[1], "1 0 0", "0 -1 0"),
             self.side(tag + "-x", (x0, y1, z1), (x0, y0, z1), (x0, y0, z0), m[2], "0 1 0", "0 0 -1"),
             self.side(tag + "+x", (x1, y1, z0), (x1, y0, z0), (x1, y0, z1), m[3], "0 1 0", "0 0 -1"),
             self.side(tag + "+y", (x1, y1, z1), (x0, y1, z1), (x0, y1, z0), m[4], "1 0 0", "0 0 -1"),
             self.side(tag + "-y", (x1, y0, z0), (x0, y0, z0), (x0, y0, z1), m[5], "1 0 0", "0 0 -1")]
        return 'solid { "id" "%d" %s }' % (self.nid(), " ".join(s))

    def room(self, lo, hi, t=16, tag="", mat="DEV/DEV_MEASUREGENERIC01B"):
        x0, y0, z0 = lo
        x1, y1, z1 = hi
        return [self.box((x0 - t, y0 - t, z0 - t), (x1 + t, y1 + t, z0), mat, tag + "floor."),
                self.box((x0 - t, y0 - t, z1), (x1 + t, y1 + t, z1 + t), mat, tag + "ceil."),
                self.box((x0 - t, y0 - t, z0), (x0, y1 + t, z1), mat, tag + "wallW."),
                self.box((x1, y0 - t, z0), (x1 + t, y1 + t, z1), mat, tag + "wallE."),
                self.box((x0, y0 - t, z0), (x1, y0, z1), mat, tag + "wallS."),
                self.box((x0, y1, z0), (x1, y1 + t, z1), mat, tag + "wallN.")]

    def ent(self, cls, origin=None, extra="", solids=()):
        o = f'"origin" "{origin}" ' if origin else ""
        self.ents.append('entity { "id" "%d" "classname" "%s" %s%s %s }' % (self.nid(), cls, o, extra, " ".join(solids)))

    def player_and_light(self, player="100 100 64", light="512 512 200"):
        self.ent("info_player_start", player)
        self.ent("light", light, '"_light" "255 255 255 300"')

    def text(self, world_extra=""):
        w = ('world { "id" "1" "mapversion" "1" "classname" "worldspawn" "skyname" "sky_l4d_rural02_hdr" %s %s }'
             % (world_extra, " ".join(self.solids)))
        return 'versioninfo { "editorversion" "400" "mapversion" "1" "formatversion" "100" }\n' + w + "\n" + \
            "\n".join(self.ents) + "\n"


def dispinfo(power: int, start, height) -> str:
    """A displacement block: height(u, v) in 0..1 gives the offset along +z; alpha follows u."""
    n = (1 << power) + 1
    rows = lambda f: " ".join(f'"row{r}" "{" ".join(f(r, c) for c in range(n))}"' for r in range(n))
    return ('dispinfo { "power" "%d" "startposition" "[%g %g %g]" "flags" "0" "elevation" "0" "subdiv" "0" '
            'normals { %s } distances { %s } offsets { %s } offset_normals { %s } alphas { %s } '
            'triangle_tags { %s } allowed_verts { "10" "-1 -1 -1 -1 -1 -1 -1 -1 -1 -1" } }' % (
                power, start[0], start[1], start[2],
                rows(lambda r, c: "0 0 1"),
                rows(lambda r, c: "%g" % round(height(c / (n - 1), r / (n - 1)), 3)),
                rows(lambda r, c: "0 0 0"),
                rows(lambda r, c: "0 0 1"),
                rows(lambda r, c: "%g" % round(255 * c / (n - 1))),
                " ".join(f'"row{r}" "{" ".join(["9"] * (2 * (n - 1)))}"' for r in range(n - 1))))


# ---------------------------------------------------------------- the maps
def m_box(m):
    m.solids = m.room((0, 0, 0), (512, 512, 256))
    m.player_and_light("256 256 64", "256 256 200")


def m_props(m):
    def prop(model, o, a="0 0 0", extra=""):
        m.ents.append('entity { "id" "%d" "classname" "prop_static" "model" "%s" "origin" "%s" "angles" "%s" %s }'
                      % (m.nid(), model, o, a, extra))
    m.solids = m.room((0, 0, 0), (1024, 1024, 256))
    m.solids += [m.box((450, 250, 0), (530, 330, 200)), m.box((150, 650, 0), (260, 760, 256)),
                 m.box((760, 760, 0), (840, 840, 120)), m.box((600, 100, 0), (1000, 140, 64))]
    m.player_and_light("256 256 64", "256 256 200")
    m.ent("info_lighting", "600 600 100", '"targetname" "lo1"')
    prop("models/props_junk/wood_crate001a.mdl", "100 100 0")
    prop("models/props_vehicles/cara_95sedan.mdl", "500 300 0", "0 37 0",
         '"skin" "2" "solid" "6" "fademindist" "-1" "fademaxdist" "3000" "fadescale" "0.5" "disableshadows" "1"')
    prop("models/props_interiors/table_kitchen.mdl", "800 800 0", "0 90 0",
         '"rendercolor" "255 128 64" "renderamt" "200" "mincpulevel" "1" "maxcpulevel" "2" "mingpulevel" "1" '
         '"maxgpulevel" "3" "disableX360" "1" "lightingorigin" "lo1" "ignorenormals" "1" '
         '"disablevertexlighting" "1" "disableselfshadowing" "1" "screenspacefade" "1"')
    prop("models/props_junk/propanecanister001a.mdl", "300 800 0")
    prop("models/props_furniture/hotel_chair.mdl", "700 150 0", "0 -45 0",
         '"fademindist" "500" "fademaxdist" "800" "solid" "0"')
    prop("models/props_c17/oildrum001.mdl", "900 120 0")
    prop("models/props_street/garbage_can.mdl", "1020 1020 0", "15 30 5")


def m_leak(m):
    m.solids = m.room((0, 0, 0), (512, 512, 256))
    m.ent("info_player_start", "256 256 64")
    m.ent("light", "2000 300 100", '"_light" "255 255 255 200"')


def _ap_walls(m):
    m.solids = m.room((0, 0, 0), (1040, 512, 256))
    m.solids += [m.box((512, 0, 0), (528, 192, 256)), m.box((512, 320, 0), (528, 512, 256)),
                 m.box((512, 192, 128), (528, 320, 256))]
    m.ent("info_player_start", "256 256 64")
    m.ent("light", "256 256 200", '"_light" "255 255 255 200"')
    m.ent("light", "800 256 200", '"_light" "255 200 200 200"')


def m_areaportal(m):
    _ap_walls(m)
    m.ent("func_areaportal", None, '"target" "door1" "StartOpen" "0" "portalversion" "1"',
          [m.box((516, 192, 0), (524, 320, 128), "TOOLS/TOOLSAREAPORTAL")])
    m.ent("func_door", None, '"targetname" "door1" "movedir" "0 90 0" "speed" "100"',
          [m.box((518, 192, 0), (522, 320, 128))])


def m_areaportal_leak(m):
    _ap_walls(m)
    m.ent("func_areaportal", None, '"target" "door1" "StartOpen" "0" "portalversion" "1"',
          [m.box((200, 192, 0), (208, 320, 128), "TOOLS/TOOLSAREAPORTAL")])
    m.ent("func_door", None, '"targetname" "door1" "movedir" "0 90 0" "speed" "100"',
          [m.box((518, 192, 0), (522, 320, 128))])


def m_areaportal_window(m):
    _ap_walls(m)
    m.ent("func_areaportalwindow", None, '"target" "glass" "FadeStartDist" "128" "FadeDist" "512" '
          '"TranslucencyLimit" "0.2" "portalversion" "1"', [m.box((516, 192, 0), (524, 320, 128), "TOOLS/TOOLSAREAPORTAL")])
    m.ent("func_brush", None, '"targetname" "glass" "solidity" "2"', [m.box((518, 192, 0), (522, 320, 128))])


GRASS = "NATURE/BLEND_GRASS_GRASS_02"     # a blend material with a %detailtype


def _terrain(m, x0, y0, size, power=3):
    """Two neighbouring displacements with hills (grass on them through the material)."""
    import math
    for i in range(2):
        x = x0 + i * size
        h = (lambda u, v, i=i: 40 * math.sin(math.pi * (u + i) / 2) * math.sin(math.pi * v))
        m.solids.append(m.box((x, y0, -32), (x + size, y0 + size, 0), "TOOLS/TOOLSNODRAW", tag=f"disp{i}.",
                              mats=[GRASS] + ["TOOLS/TOOLSNODRAW"] * 5,
                              top_extra=dispinfo(power, (x, y0, 0), h)))


def m_grass(m):
    m.solids = m.room((0, 0, -64), (1600, 1024, 256))
    _terrain(m, 100, 100, 512)
    # a flat face with the same material (a blend copy: vbsp puts no grass on it)
    m.solids.append(m.box((1200, 200, -64), (1500, 500, 10), GRASS))
    m.player_and_light("1300 800 64", "800 512 200")


def m_detail_entities(m):
    m_grass(m)
    m.ent("prop_detail", "300 800 -60", '"model" "models/props_furniture/hotel_chair.mdl" "angles" "0 37 0"')
    m.ent("prop_detail", "500 800 -60", '"model" "models/props_interiors/table_kitchen.mdl" "angles" "0 -12.5 3"')
    m.ent("prop_detail", "700 800 -60", '"model" "models/props_junk/wood_crate001a.mdl" "angles" "0 0 0"')
    m.ent("prop_detail_sprite", "900 800 -50", '"angles" "0 90 0" "detailOrientation" "1" "position_ul" "-10 20" '
          '"position_lr" "10 0" "tex_ul" "0 0" "tex_size" "128 128" "tex_total_size" "512"')


def m_overlays(m):
    t = 16
    m.solids = [m.box((-t, -t, -t), (512, 512 + t, 0), tag="floorA."),
                m.box((512, -t, -t), (1024 + t, 512 + t, 0), tag="floorB."),
                m.box((-t, -t, 256), (1024 + t, 512 + t, 256 + t)),
                m.box((-t, -t, 0), (0, 512 + t, 256), tag="wallW."),
                m.box((1024, -t, 0), (1024 + t, 512 + t, 256), tag="wallE."),
                m.box((0, -t, 0), (1024, 0, 256), tag="wallS."), m.box((0, 512, 0), (1024, 512 + t, 256), tag="wallN.")]
    m.player_and_light("200 256 64", "512 256 200")
    crate = m.box((600, 300, 0), (664, 364, 64), tag="crate.")
    m.ent("func_brush", None, '"solidity" "2"', [crate])

    def overlay(mat, origin, sides, normal, u, v, uv, extra=""):
        m.ent("info_overlay", origin, f'"material" "{mat}" "sides" "{" ".join(map(str, sides))}" "RenderOrder" "0" '
              f'"StartU" "0" "EndU" "1" "StartV" "0" "EndV" "1" "BasisOrigin" "{origin}" "BasisU" "{u}" '
              f'"BasisV" "{v}" "BasisNormal" "{normal}" ' + " ".join(f'"uv{i}" "{p}"' for i, p in enumerate(uv)) +
              " " + extra)
    sq = ["-64 -64 0", "-64 64 0", "64 64 0", "64 -64 0"]
    s = m.sides
    overlay("decals/asphalt/asphalt1", "256 256 0", [s["floorA.top"]], "0 0 1", "1 0 0", "0 1 0", sq)
    overlay("concrete/concrete_crack_decal01a", "300 200 0", [s["floorA.top"]], "0 0 1", "1 0 0", "0 -1 0", sq,
            '"fademindist" "300" "fademaxdist" "900" "RenderOrder" "1"')
    overlay("decals/apt1", "1024 256 128", [s["wallE.-x"]], "-1 0 0", "0 1 0", "0 0 1",
            ["-32 -32 0", "-32 32 0", "32 32 0", "32 -32 0"], '"targetname" "sign1"')
    overlay("decals/apt2", "700 0 0", [s["floorB.top"], s["wallS.+y"]], "0 0 1", "1 0 0", "0 1 0",
            ["-48 -16 0", "-48 48 0", "48 48 0", "48 -16 0"], '"StartU" "0.25" "EndU" "0.75"')
    overlay("decals/apt3", "632 332 64", [s["crate.top"]], "0 0 1", "1 0 0", "0 1 0",
            ["-16 -16 0", "-16 16 0", "16 16 0", "16 -16 0"])


def m_overlays_on_terrain(m):
    m_grass(m)
    m.ent("info_overlay", "300 300 0", f'"material" "decals/asphalt/asphalt1" "sides" "{m.sides["disp0.top"]}" '
          '"RenderOrder" "0" "StartU" "0" "EndU" "1" "StartV" "0" "EndV" "1" "BasisOrigin" "300 300 0" '
          '"BasisU" "1 0 0" "BasisV" "0 1 0" "BasisNormal" "0 0 1" "uv0" "-128 -128 0" "uv1" "-128 128 0" '
          '"uv2" "128 128 0" "uv3" "128 -128 0"')


W = "liquids/urbanwater_river"
W2 = "dev/dev_water2"
N = "TOOLS/TOOLSNODRAW"


def m_water(m):
    m.solids = m.room((0, 0, -128), (1024, 512, 256))
    m.solids += [m.box((0, 0, -128), (400, 512, 0)), m.box((800, 0, -128), (1024, 512, 0)),
                 m.box((400, 0, -128), (800, 100, 0)), m.box((400, 400, -128), (800, 512, 0))]
    m.solids += [m.box((400, 100, -128), (800, 400, -16), mats=[W, N, N, N, N, N]),
                 m.box((100, 100, 0), (300, 200, 32), mats=[W2, N, N, N, N, N])]
    m.player_and_light("200 300 64", "512 256 200")


def m_water_overlays(m):
    """Water overlays: Hammer saves an info_overlay_transition's shore strips in an "overlaytransition" block
    (vectors in brackets there, as chunk keys are written)."""
    m.solids = m.room((0, 0, -128), (1024, 512, 256))
    m.solids += [m.box((0, 0, -128), (400, 512, 0), tag="shoreW."), m.box((800, 0, -128), (1024, 512, 0)),
                 m.box((400, 0, -128), (800, 100, 0), tag="shoreS."), m.box((400, 400, -128), (800, 512, 0),
                                                                          tag="shoreN.")]
    m.solids += [m.box((400, 100, -128), (800, 400, -16), tag="pool.", mats=[W, N, N, N, N, N])]
    m.player_and_light("200 300 64", "512 256 200")
    s = m.sides

    def strip(origin, sides, u, v, uv, mat="decals/asphalt/asphalt1", su="0", eu="1"):
        return ('overlaydata { "material" "%s" "StartU" "%s" "EndU" "%s" "StartV" "0" "EndV" "1" '
                '"BasisOrigin" "[%s]" "BasisU" "[%s]" "BasisV" "[%s]" "BasisNormal" "[0 0 1]" %s "sides" "%s" }'
                % (mat, su, eu, origin, u, v, " ".join(f'"uv{i}" "[{p}]"' for i, p in enumerate(uv)),
                   " ".join(map(str, sides))))
    blocks = [strip("600 400 -16", [s["pool.top"], s["shoreN.top"]], "1 0 0", "0 1 0",
                    ["-200 -24 0", "-200 24 0", "200 24 0", "200 -24 0"]),
              strip("600 100 -16", [s["pool.top"], s["shoreS.top"]], "-1 0 0", "0 -1 0",
                    ["-200 -24 0", "-200 24 0", "200 24 0", "200 -24 0"], su="0.5", eu="0.25"),
              strip("400 250 -16", [s["pool.top"], s["shoreW.top"]], "0 1 0", "1 0 0",
                    ["-150 -16 0", "-150 16 0", "150 16 0", "150 -16 0"])]
    m.ents.append('entity { "id" "%d" "classname" "info_overlay_transition" "material" "decals/asphalt/asphalt1" '
                  '"sides" "%d" "sides2" "%d" "LengthTexcoordStart" "0" "LengthTexcoordEnd" "1" '
                  '"WidthTexcoordStart" "0" "WidthTexcoordEnd" "1" "Width1" "25" "Width2" "25" "DebugDraw" "0" '
                  '"origin" "600 250 -16" overlaytransition { %s } }'
                  % (m.nid(), s["pool.top"], s["shoreN.top"], " ".join(blocks)))


def m_water_lake(m):
    """A big subdivided lake with a pillar through it, connected pools at two heights, detail and entity water."""
    m.solids = m.room((0, 0, -256), (4096, 4096, 512))
    m.solids += [m.box((0, 0, -256), (4096, 4096, -192)),
                 m.box((0, 0, -192), (4096, 4096, -32), mats=[W, N, N, N, N, N]),
                 m.box((1000, 1000, -192), (1100, 1100, 300)),
                 m.box((0, 0, -32), (4096, 600, 0)),
                 m.box((200, 100, 0), (400, 300, 40), mats=[W2, N, N, N, N, N]),
                 m.box((400, 100, 0), (600, 300, 60), mats=[W2, N, N, N, N, N]),
                 m.box((180, 80, 0), (620, 100, 80)), m.box((180, 300, 0), (620, 320, 80)),
                 m.box((180, 100, 0), (200, 300, 80)), m.box((600, 100, 0), (620, 300, 80))]
    m.player_and_light("2000 300 64", "2048 2048 400")
    m.ent("func_detail", None, "", [m.box((800, 100, 0), (900, 200, 30), mats=[W, N, N, N, N, N])])
    m.ent("func_brush", None, '"solidity" "0"', [m.box((1200, 100, 0), (1300, 200, 30), mats=[W2, N, N, N, N, N])])


def m_water_cubemap(m):
    m.solids = m.room((0, 0, -128), (1024, 512, 256))
    m.solids += [m.box((0, 0, -128), (400, 512, 0)), m.box((800, 0, -128), (1024, 512, 0)),
                 m.box((400, 0, -128), (800, 100, 0)), m.box((400, 400, -128), (800, 512, 0)),
                 m.box((400, 100, -128), (800, 400, -16), mats=["liquids/ruralwater_river", N, N, N, N, N])]
    m.player_and_light("200 300 64", "512 256 200")
    m.ent("env_cubemap", "600 250 50", "")


def m_cubemaps(m):
    C1, G, MW, P = "concrete/parkingwallb", "glass/glass01cull", "metal/metalwall001a", "metal/metalpipe003a"
    m.solids = m.room((0, 0, -128), (2048, 1024, 384))
    m.solids += [m.box((0, 0, -128), (800, 1024, 0), C1), m.box((1200, 0, -128), (2048, 1024, 0), C1),
                 m.box((800, 0, -128), (1200, 300, 0), C1), m.box((800, 700, -128), (1200, 1024, 0), C1),
                 m.box((800, 300, -128), (1200, 700, -32), mats=["liquids/ruralwater_river", N, N, N, N, N]),
                 m.box((100, 100, 0), (300, 300, 120), MW, tag="mw."), m.box((1500, 600, 0), (1700, 800, 200), P),
                 m.box((400, 900, 0), (600, 916, 200), G)]
    m.player_and_light("200 500 64", "1024 512 300")
    m.ent("env_cubemap", "200 200 160", '"cubemapsize" "0" "sides" "%d %d"' % (m.sides["mw.top"], m.sides["mw.+x"]))
    m.ent("env_cubemap", "1000 500 100", '"cubemapsize" "0" "sides" ""')
    m.ent("env_cubemap", "1700 300 100", '"cubemapsize" "6"')


BLENDS = ["brick/blend_brick_brick01", "concrete/blend_blacktop_01", "nature/blenddirtparkinglot01",
          "nature/blendroadleaves01b", "nature/blend_milltowngrass01wet"]


def m_blend_on_brushes(m, cubemap=False):
    m.solids = m.room((0, 0, 0), (1024, 1024, 256))
    for i, mat in enumerate(BLENDS):
        x = 60 + 190 * i
        m.solids.append(m.box((x, 100, 0), (x + 120, 220, 40 + 20 * i), mat))
    m.player_and_light("800 600 64", "512 512 200")
    if cubemap:
        m.ent("env_cubemap", "450 160 100", "")


def m_no_dynamic_shadow(m):
    m.solids = m.room((0, 0, 0), (1024, 512, 256), tag="r.")
    m.solids.append(m.box((300, 200, 0), (400, 300, 100), tag="b."))
    m.player_and_light("100 100 64", "512 256 200")
    m.ent("func_brush", None, '"solidity" "0"', [m.box((600, 200, 0), (700, 300, 100), tag="c.")])
    s = m.sides
    m.ent("info_no_dynamic_shadow", "0 0 0", '"sides" "%d %d %d"' % (s["b.top"], s["r.floor.top"], s["b.top"]))
    m.ent("info_no_dynamic_shadow", "0 0 0", '"sides" "%d"' % s["c.+x"])


def m_viscluster(m):
    T = "TOOLS/TOOLSTRIGGER"
    m.solids = m.room((0, 0, 0), (2048, 1024, 256))
    for x in range(256, 2048, 384):
        for y in (256, 640):
            m.solids.append(m.box((x, y, 0), (x + 96, y + 96, 200)))
    m.player_and_light("100 100 64", "1024 512 200")
    m.ent("func_viscluster", None, "", [m.box((0, 0, 0), (900, 1024, 256), T)])
    m.ent("func_viscluster", None, "", [m.box((700, 0, 0), (1400, 500, 256), T), m.box((1400, 0, 0), (1600, 300, 256), T)])


def m_occluders(m):
    O = "TOOLS/TOOLSOCCLUDER"
    m.solids = m.room((0, 0, 0), (2064, 1024, 256))
    m.solids += [m.box((1024, 0, 0), (1040, 400, 256)), m.box((1024, 600, 0), (1040, 1024, 256)),
                 m.box((1024, 400, 160), (1040, 600, 256))]
    m.player_and_light("100 100 64", "512 512 200")
    m.ent("light", "1500 512 200", '"_light" "255 255 255 300"')
    m.ent("func_areaportal", None, '"target" "door1" "StartOpen" "0" "portalversion" "1"',
          [m.box((1028, 400, 0), (1036, 600, 160), "TOOLS/TOOLSAREAPORTAL")])
    m.ent("func_door", None, '"targetname" "door1" "movedir" "0 90 0" "speed" "100"', [m.box((1030, 400, 0), (1034, 600, 160))])
    m.ent("func_occluder", None, '"targetname" "occ1" "StartActive" "1"', [m.box((1500, 100, 0), (1516, 900, 200), O)])
    m.ent("func_occluder", None, '"StartActive" "1"', [m.box((400, 100, 0), (416, 700, 220), O),
                                                       m.box((300, 600, 0), (500, 616, 150), O)])


def m_skybox(m):
    S = "TOOLS/TOOLSSKYBOX"
    m.solids = m.room((0, 0, 0), (1024, 1024, 512))
    m.solids[1] = m.box((-16, -16, 512), (1040, 1040, 528), S)
    m.solids += m.room((3000, 3000, 0), (3512, 3512, 256), tag="sky.")
    m.solids[-5] = m.box((2984, 2984, 256), (3528, 3528, 272), S)
    m.solids += [m.box((3100, 3100, 0), (3200, 3300, 60)), m.box((3300, 3150, 0), (3450, 3250, 120))]
    m.player_and_light("100 100 64", "512 512 300")
    m.ent("sky_camera", "3256 3256 128", '"scale" "16" "fogenable" "0"')
    m.ent("light", "3256 3256 200", '"_light" "255 255 255 100"')


def m_grass_water(m):
    """L4D2 places no grass under water (in a map with water) or inside a func_detail_blocker."""
    m.solids = m.room((0, 0, -64), (1600, 1024, 256))
    _terrain(m, 100, 100, 512)
    m.solids.append(m.box((100, 100, -32), (400, 612, 20), mats=[W, N, N, N, N, N]))
    m.player_and_light("1300 800 64", "800 512 200")
    m.ent("func_detail_blocker", None, "", [m.box((700, 200, -40), (900, 400, 80), "TOOLS/TOOLSTRIGGER")])


def m_ladders(m):
    L, N = "TOOLS/TOOLSINVISIBLELADDER", "TOOLS/TOOLSNODRAW"
    m.solids = m.room((0, 0, 0), (1024, 512, 256))
    m.solids.append(m.box((500, 0, 0), (516, 512, 200)))
    m.player_and_light("100 100 64", "512 256 200")
    # a ladder face on one side; one all ladder; a zombies-only one (team 2 is kept by build.py)
    m.ent("func_ladder", None, "", [m.box((484, 100, 0), (500, 132, 200), mats=[N, N, L, N, N, N])])
    m.ent("func_ladder", None, "", [m.box((516, 300, 0), (520, 332, 200), L)])
    m.ent("func_simpleladder", None, '"team" "2" "normal.x" "1" "normal.y" "0" "normal.z" "0"',
          [m.box((516, 400, 0), (520, 432, 200), mats=[N, N, N, L, N, N])])


MAPS = {
    "box": m_box, "props": m_props, "leak": m_leak, "areaportal": m_areaportal,
    "areaportal_leak": m_areaportal_leak, "areaportal_window": m_areaportal_window,
    "grass": m_grass, "detail_entities": m_detail_entities, "overlays": m_overlays,
    "overlays_on_terrain": m_overlays_on_terrain, "water": m_water, "water_overlays": m_water_overlays, "water_lake": m_water_lake,
    "water_cubemap": m_water_cubemap, "cubemaps": m_cubemaps, "blend_on_brushes": m_blend_on_brushes,
    "blend_cubemap": lambda m: m_blend_on_brushes(m, True), "no_dynamic_shadow": m_no_dynamic_shadow,
    "viscluster": m_viscluster, "occluders": m_occluders, "skybox": m_skybox,
    "ladders": m_ladders, "grass_water": m_grass_water,
}


# ---------------------------------------------------------------- running
def compile_map(name: str, vmf_text: str, root: str, instances=()) -> tuple[str, str]:
    """instances: (the func_instance's "file" key, its path), copied next to both maps as the key names them."""
    from hammerless.core.mapcompiler import (write_cubemap_materials, write_detail_file, write_fgd_table,
                                             write_material_table, write_prop_table, write_surfaceprops)
    from hammerless.core.vpk import GameContent
    game = os.path.join(root, "left4dead2")
    mapname = "hl_test_" + name
    ours_dir, valve_dir = os.path.join(OUT, name), os.path.join(OUT, name, "valve")
    os.makedirs(valve_dir, exist_ok=True)
    base, vbase = os.path.join(ours_dir, mapname), os.path.join(valve_dir, mapname)
    stamp = hashlib.sha1(vmf_text.encode()).hexdigest()
    reuse = os.path.exists(vbase + ".bsp") and os.path.exists(vbase + ".stamp") and open(vbase + ".stamp").read() == stamp
    for b in (base, vbase):
        with open(b + ".vmf", "w", encoding="utf-8") as f:
            f.write(vmf_text)
        for key, src in instances:
            rel = os.path.splitext(key.replace("\\", "/"))[0] + ".vmf"
            dst = os.path.join(os.path.dirname(b), rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
    if instances:
        stamp = hashlib.sha1((vmf_text + "".join(open(p, encoding="latin-1").read() for _k, p in instances))
                             .encode("utf-8", "replace")).hexdigest()
        reuse = os.path.exists(vbase + ".bsp") and os.path.exists(vbase + ".stamp") and open(vbase + ".stamp").read() == stamp
    if not reuse:
        for ext in (".bsp", ".prt", ".lin"):
            if os.path.exists(vbase + ext):
                os.remove(vbase + ext)
        r = subprocess.run([os.path.join(root, "bin", "vbsp.exe"), "-game", game, vbase], capture_output=True, text=True)
        with open(vbase + ".log", "w") as f:
            f.write(r.stdout + r.stderr)
        with open(vbase + ".stamp", "w") as f:
            f.write(stamp)
    content = GameContent(root)
    write_material_table(base + ".hlvbsp_materials.txt", base + ".vmf", content, game)
    write_surfaceprops(base + ".hlvbsp_surfaceprops.txt", content)
    detail = write_detail_file(base + ".hlvbsp_detail.txt", base + ".vmf", content, game)
    write_prop_table(base + ".hlvbsp_props.txt", base + ".vmf", content, game, detail)
    write_cubemap_materials(base + ".hlvbsp_cubemaps.txt", base + ".vmf", content, game)
    write_fgd_table(base + ".hlvbsp_fgd.txt", root)
    for ext in (".bsp", ".prt", ".lin"):
        if os.path.exists(base + ext):
            os.remove(base + ext)
    r = subprocess.run([HLVBSP, "-game", game, "-materials", base + ".hlvbsp_materials.txt",
                        "-surfaceprops", base + ".hlvbsp_surfaceprops.txt", "-props", base + ".hlvbsp_props.txt",
                        "-detail", base + ".hlvbsp_detail.txt", "-cubemaps", base + ".hlvbsp_cubemaps.txt",
                        "-fgd", base + ".hlvbsp_fgd.txt", base],
                       capture_output=True, text=True)
    with open(base + ".log", "w") as f:
        f.write(r.stdout + r.stderr)
    return base, vbase


def compare(base: str, vbase: str) -> list[str]:
    """What differs (empty: the same map)."""
    problems = []
    if not os.path.exists(vbase + ".bsp"):
        # a map vbsp doesn't finish (a leak): the leak trace must match
        if os.path.exists(base + ".bsp"):
            problems.append("vbsp made no map but hlvbsp did")
        for ext in (".lin",):
            a, b = base + ext, vbase + ext
            if os.path.exists(a) != os.path.exists(b) or (os.path.exists(a) and open(a).read() != open(b).read()):
                problems.append(ext + " differs")
        return problems
    if not os.path.exists(base + ".bsp"):
        return ["hlvbsp made no map"]
    ours, valve = bspdump.dump(base + ".bsp"), bspdump.dump(vbase + ".bsp")
    for i in ours:
        if i not in SKIP_LUMPS and ours[i] != valve[i]:
            problems.append(f"lump {i} {bspdump.NAMES[i]}")
    a, b = base + ".prt", vbase + ".prt"
    if os.path.exists(a) != os.path.exists(b) or (os.path.exists(a) and open(a).read() != open(b).read()):
        problems.append(".prt differs")
    return problems


def main(argv: list[str]) -> int:
    if "--list" in argv:
        print("\n".join(MAPS))
        return 0
    root = find_game_root()
    if not root:
        print("Left 4 Dead 2 not found")
        return 2
    wanted = [a for a in argv if not a.startswith("--")]
    names = [n for n in MAPS if not wanted or any(w in n for w in wanted)]
    failed = 0
    for name in names:
        m = Map()
        MAPS[name](m)
        base, vbase = compile_map(name, m.text(), root)
        problems = compare(base, vbase)
        print(f"{'PASS' if not problems else 'FAIL'} {name}" + (": " + ", ".join(problems) if problems else ""), flush=True)
        failed += bool(problems)
    print(f"\n{len(names) - failed} of {len(names)} maps the same as vbsp's (output in {OUT})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
