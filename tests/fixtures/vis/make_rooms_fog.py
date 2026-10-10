"""Builds rooms_fog.zip (needs L4D2's vvis): rooms_portal with an env_fog_controller (farz 500), plus Valve vvis's output for it and for rooms_portal -radius_override 400 (full and -fast)."""
import os
import struct
import subprocess
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = HERE
GAME = r"C:\Program Files (x86)\Steam\steamapps\common\Left 4 Dead 2"
VVIS = GAME + r"\bin\vvis.exe"
W = tempfile.mkdtemp()

src = zipfile.ZipFile(os.path.join(FIX, "rooms_portal.zip"))
bsp, prt = src.read("rooms_portal.bsp"), src.read("rooms_portal.prt")

# the entity lump, rewritten at the end of the file with the fog controller added
_v, off, ln, _c = struct.unpack_from("<iiii", bsp, 8)
ents = bsp[off:off + ln].rstrip(b"\0")
fog = (b'{\n"fogenable" "1"\n"fogstart" "100"\n"fogend" "400"\n"farz" "500"\n"origin" "0 0 64"\n'
       b'"classname" "env_fog_controller"\n}\n')
ents = ents + fog + b"\0"
# grow the entity lump in place (as vbsp would have written it): later lumps, and the game lump's own
# directory of file offsets, move along
delta = len(ents) - ln
delta += (-delta) % 4
fogbsp = bytearray(bsp[:off] + ents + b"\0" * (delta + ln - len(ents)) + bsp[off + ln:])
for i in range(64):
    lo, ll = struct.unpack_from("<ii", fogbsp, 8 + 16 * i + 4)
    if i == 0:
        struct.pack_into("<i", fogbsp, 8 + 4 + 4, len(ents))
    elif lo > off or (lo == off and ll and i != 0):
        struct.pack_into("<i", fogbsp, 8 + 16 * i + 4, lo + delta)
_v, go, gl, _c = struct.unpack_from("<iiii", fogbsp, 8 + 16 * 35)
n = struct.unpack_from("<i", fogbsp, go)[0]
for k in range(n):
    p = go + 4 + 16 * k + 8
    struct.pack_into("<i", fogbsp, p, struct.unpack_from("<i", fogbsp, p)[0] + delta)

cases = {"rooms_fog": (bytes(fogbsp), []), "rooms_portal": (bsp, ["-radius_override", "400"])}
out = {}
for name, (data, args) in cases.items():
    for mode in ("full", "fast"):
        base = os.path.join(W, f"{name}_{mode}")
        open(base + ".bsp", "wb").write(data)
        open(base + ".prt", "wb").write(prt)
        r = subprocess.run([VVIS, *(["-fast"] if mode == "fast" else []), *args, "-game", GAME + r"\left4dead2", base],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout
        out[f"{name}.{'radius400_' if args else ''}vvis_{mode}.bsp"] = open(base + ".bsp", "rb").read()
        print(name, mode, "ok")

# the culling must actually change something, or the test proves nothing
plain = src.read("rooms_portal.vvis_full.bsp")
def vis(b):
    _v, o, l, _ = struct.unpack_from("<iiii", b, 8 + 16 * 4)
    return b[o:o + l]
for k, b in out.items():
    print(k, "vis differs from no-radius:", vis(b) != vis(plain))

with zipfile.ZipFile(os.path.join(FIX, "rooms_fog.zip"), "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("rooms_fog.bsp", bytes(fogbsp))
    z.writestr("rooms_fog.prt", prt)
    for k, b in out.items():
        z.writestr(k, b)
print("written", os.path.getsize(os.path.join(FIX, "rooms_fog.zip")))
