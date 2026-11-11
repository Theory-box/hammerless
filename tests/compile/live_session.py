"""hlvrad -live against real bakes: the session's lightmaps after edits must be a fresh bake's.

    python tests/compile/live_session.py <map.bsp> --game <left4dead2 folder> [--modeldir <folder>]
        [--edit "<entity> <key> <value>"]... [--threads 4] [--rays 1024] [--args "<more hlvrad options>"]

The map is copied (never written). Steps, each timed:
  1. a normal bake of the copy (the reference), and the session started on another copy;
  2. the session's settled lightmaps against the bake (close: its passes go on from where they are);
  3. COLD: the session's lightmaps must be the bake's lighting lump to the byte;
  4. the edits (default: the first point light moved 48 units and tinted), GO: time to the first result, to the
     exact direct light, to settled;
  5. COLD again, against a normal bake of a copy whose entity lump has the same edits: to the byte.
Prints a JSON summary; exits 1 when a to-the-byte check fails.
"""
import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import importlib.util

_spec = importlib.util.spec_from_file_location("livelight", os.path.join(HERE, "..", "..", "hammerless", "core", "livelight.py"))
livelight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(livelight)

HLVRAD = os.path.join(HERE, "..", "..", "hammerless", "core", "hlvrad.exe")
LUMP_ENTITIES, LUMP_LIGHTING_HDR, LUMP_FACES_HDR = 0, 53, 58


def lump(data, i):
    """L4D2's lump header: version, offset, length, fourCC."""
    ver, ofs, ln, _ = struct.unpack_from("<iiii", data, 8 + 16 * i)
    return data[ofs:ofs + ln]


def entity_blocks(text):
    return re.findall(r"\{[^{}]*\}", text)


def with_edits(src, dst, edits):
    """A copy of the map whose entity lump has the edits (entity index, key, value)."""
    data = bytearray(open(src, "rb").read())
    ver, ofs, ln, four = struct.unpack_from("<iiii", data, 8)
    text = bytes(data[ofs:ofs + ln]).rstrip(b"\0").decode("latin1")
    blocks = entity_blocks(text)
    for ent, key, value in edits:
        b = blocks[ent]
        pat = re.compile(r'"%s"\s+"[^"]*"' % re.escape(key))
        new = '"%s" "%s"' % (key, value)
        blocks[ent] = pat.sub(new, b, count=1) if pat.search(b) else b[:b.rindex("}")] + new + "\n}"
    out = ("\n".join(blocks) + "\n").encode("latin1") + b"\0"
    while len(data) % 4:
        data.append(0)
    struct.pack_into("<iiii", data, 8, ver, len(data), len(out), four)
    data += out
    open(dst, "wb").write(bytes(data))


def bake(bsp, args, timeout=600):
    t = time.time()
    p = subprocess.run([HLVRAD] + args + [bsp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                       creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
    if p.returncode:
        sys.exit("bake failed:\n" + p.stdout.decode("utf-8", "replace")[-2000:])
    data = open(bsp, "rb").read()
    return lump(data, LUMP_FACES_HDR), lump(data, LUMP_LIGHTING_HDR), time.time() - t


def compare(faces_a, light_a, faces_b, light_b):
    """How two lightings differ: the same layout?, bytes the same?, and the luxels' values (linear light units)."""
    import numpy as np
    same_faces = faces_a == faces_b
    out = {"same_layout": bool(same_faces), "identical": bool(same_faces and light_a == light_b)}
    if len(light_a) != len(light_b) or not light_a:
        return out
    a = np.frombuffer(light_a, np.uint8).reshape(-1, 4)
    b = np.frombuffer(light_b, np.uint8).reshape(-1, 4)
    va = a[:, :3].astype(np.float64) * np.exp2(a[:, 3].astype(np.int8).astype(np.float64))[:, None]
    vb = b[:, :3].astype(np.float64) * np.exp2(b[:, 3].astype(np.int8).astype(np.float64))[:, None]
    d = np.abs(va - vb)
    rel = d / (np.maximum(va, vb) + 1.0)
    out.update(luxels=int(len(a)), differing=int((a != b).any(axis=1).sum()), max_abs=float(d.max()),
               mean_abs=float(d.mean()), within_2pct=float((rel.max(axis=1) <= 0.02).mean()),
               worst_rel=float(rel.max()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bsp")
    ap.add_argument("--game", required=True)
    ap.add_argument("--modeldir")
    ap.add_argument("--edit", action="append", default=[])
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--rays", type=int, default=1024)
    ap.add_argument("--args", default="-hdr -StaticPropPolys -textureshadows")
    ap.add_argument("--keep", action="store_true")
    o = ap.parse_args()
    args = o.args.split() + ["-gpu", "-gi", "100", "-girays", str(o.rays), "-threads", str(o.threads), "-game", o.game]
    if o.modeldir:
        args += ["-modeldir", o.modeldir]
    work = tempfile.mkdtemp(prefix="hl_test_live_")
    result = {"map": o.bsp, "args": " ".join(args)}
    ok = True
    try:
        src = open(o.bsp, "rb").read()
        blocks = entity_blocks(lump(src, LUMP_ENTITIES).rstrip(b"\0").decode("latin1"))
        edits = []
        for e in o.edit:
            ent, key, value = e.split(" ", 2)
            edits.append((int(ent), key, value))
        if not edits:
            for i, b in enumerate(blocks):
                m = re.search(r'"origin"\s+"([^"]*)"', b)
                if re.search(r'"classname"\s+"light"', b) and m and not re.search(r'"style"\s+"[1-9]', b):
                    x, y, z = (float(v) for v in m.group(1).split())
                    edits = [(i, "origin", "%g %g %g" % (x + 48, y, z + 16)), (i, "_light", "140 200 255 300")]
                    break
        if not edits:
            sys.exit("no plain point light to edit: give --edit")
        result["edits"] = edits
        ref, live_bsp, ref2 = (os.path.join(work, n) for n in ("hl_test_ref.bsp", "hl_test_live.bsp", "hl_test_ref2.bsp"))
        shutil.copyfile(o.bsp, ref)
        shutil.copyfile(o.bsp, live_bsp)
        faces_ref, light_ref, t = bake(ref, args)
        result["bake_seconds"] = round(t, 2)

        t0 = time.time()
        with livelight.LiveLight(HLVRAD, args + [live_bsp]) as live:
            first = live.wait()
            result["start_to_first_lightmaps"] = round(time.time() - t0, 2)
            live.wait(state=livelight.SETTLED)
            result["start_to_settled"] = round(time.time() - t0, 2)
            result["settled_vs_bake"] = compare(*live.snapshot()[:2], faces_ref, light_ref)
            s = live.cold()
            live.wait(s, livelight.BAKED)
            result["cold_vs_bake"] = c = compare(*live.snapshot()[:2], faces_ref, light_ref)
            ok &= c["identical"]

            for ent, key, value in edits:
                live.set(ent, key, value)
            t1 = time.time()
            s = live.go()
            u = live.wait(s)
            result["edit_to_first_result"] = round(time.time() - t1, 3)
            result["edit_first_state"] = u[2]
            rough = live.snapshot()
            while u[2] < livelight.EXACT or u[3] < 1:       # (a bounce pass and the exact direct light in)
                u = live.wait(s)
            result["edit_to_exact_direct"] = round(time.time() - t1, 3)
            u = live.wait(s, livelight.SETTLED)
            result["edit_to_settled"] = round(time.time() - t1, 3)
            result["edit_passes"] = u[3]
            settled = live.snapshot()

            with_edits(o.bsp, ref2, edits)
            faces_ref2, light_ref2, t = bake(ref2, args)
            result["rough_vs_bake"] = compare(*rough[:2], faces_ref2, light_ref2)
            result["settled_edit_vs_bake"] = compare(*settled[:2], faces_ref2, light_ref2)
            s = live.cold()
            live.wait(s, livelight.BAKED)
            result["cold_edit_vs_bake"] = c = compare(*live.snapshot()[:2], faces_ref2, light_ref2)
            ok &= c["identical"]
            result["unchanged_vs_edit_bake"] = compare(faces_ref, light_ref, faces_ref2, light_ref2)
            result["log_tail"] = [l for l in live.log if l.startswith("HLLIVE")][-6:]
        result["input_unchanged"] = open(o.bsp, "rb").read() == src
        ok &= result["input_unchanged"]
    finally:
        if not o.keep:
            shutil.rmtree(work, ignore_errors=True)
    result["ok"] = bool(ok)
    print(json.dumps(result, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
