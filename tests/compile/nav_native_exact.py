"""Whole nav (generation + analysis) with Python brush building vs native (native/hlbrush.c): the written .nav must
be byte-identical. Run with Blender's Python (the one builds use), on VMFs with their compiled .bsp beside them:

    "<Blender>/4.4/python/bin/python.exe" tests/compile/nav_native_exact.py <map.vmf> [...]
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from hammerless.core import collision, compile as cc, navanalyze, navpredict
from hammerless.core.navfile import write_nav
from hammerless.core.vpk import GameContent

native = collision._make_brush


def run(vmf, use_native):
    collision._make_brush = native if use_native else collision._make_brush_py
    collision._BRUSH_CACHE.clear()
    navanalyze._HULL_CACHE.clear()
    text = open(vmf, encoding="utf-8", errors="replace").read()
    t = time.time()
    mesh = navpredict.predict(text, [])
    tg = time.time() - t
    t = time.time()
    navanalyze.analyze(mesh, text, vmf[:-4] + ".bsp", GameContent(cc.find_game_root()))
    ta = time.time() - t
    return write_nav(mesh), tg, ta


for vmf in sys.argv[1:]:
    a, ga, aa = run(vmf, False)
    b, gb, ab = run(vmf, True)
    print(f"{os.path.basename(vmf)}: identical={a == b} ({len(a)} bytes); generation {ga:.1f}s -> {gb:.1f}s, "
          f"analysis {aa:.1f}s -> {ab:.1f}s")
