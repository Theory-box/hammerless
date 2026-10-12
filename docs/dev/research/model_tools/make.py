"""Tiny skinned test model for studiomdl experiments."""
import os, math
here = os.path.dirname(os.path.abspath(__file__))

NODES = 'nodes\n0 "root" -1\n1 "arm" 0\n2 "tip" 1\n3 "empty" 0\nend\n'
SKEL0 = 'skeleton\ntime 0\n0 0 0 0 0 0 0\n1 10 20 30 0.3 0.5 0.7\n2 8 0 0 0 0 0\n3 0 0 5 0 0 0\nend\n'

def box(cx, cy, cz, s, links):
    """A cube's 12 triangles; links: list of (bone, weight)."""
    out = []
    v = [(cx + dx * s, cy + dy * s, cz + dz * s) for dx in (-1, 1) for dy in (-1, 1) for dz in (-1, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    lk = f"{len(links)} " + " ".join(f"{b} {w}" for b, w in links)
    for a, b, c, d in faces:
        for tri in ((a, b, c), (a, c, d)):
            out.append("mat\n")
            for i in tri:
                x, y, z = v[i]
                out.append(f"0 {x} {y} {z} 0 0 1 0 0 {lk}\n")
    return "".join(out)

if __name__ == "__main__":
    ref = "version 1\n" + NODES + SKEL0 + "triangles\n" + box(0, 0, 0, 4, [(0, 1.0)]) + \
          box(10, 20, 30, 3, [(1, 0.5), (2, 0.2), (0, 0.2), (3, 0.1)]) + box(20, 20, 30, 2, [(2, 1.0)]) + "end\n"
    open(os.path.join(here, "ref.smd"), "w").write(ref)

    frames = []
    for t in range(0, 11):
        a = 0.7 + t * 0.1
        frames.append(f"time {t}\n0 0 0 0 0 0 0\n1 10 20 30 0.3 0.5 {a}\n2 8 0 0 0 0 0\n3 0 0 5 0 0 0\n")
    open(os.path.join(here, "wave.smd"), "w").write("version 1\n" + NODES + "skeleton\n" + "".join(frames) + "end\n")

    qc = '''$modelname "hammerless_research/skin_test.mdl"
    $cdmaterials "models/hammerless_research/"
    $surfaceprop "flesh"
    $body body "ref.smd"
    $sequence idle "ref.smd" fps 30
    $sequence wave "wave.smd" fps 20 loop activity ACT_IDLE 1 {
      { event AE_CL_PLAYSOUND 2 "Weapon_Pistol.Single" }
      { event 1100 5 "FireUser1" }
      { event 9999 7 "custom options here" }
    }
    $attachment "hand" "tip" 1 2 3 rotate 0 90 0
    $attachment "world" "root" 0 0 40 absolute
    $includemodel "infected/anim_common_male01.mdl"
    '''
    open(os.path.join(here, "test.qc"), "w").write(qc)
    qc2 = qc.replace("skin_test", "skin_test_hb") + '$hboxset "default"\n$hbox 1 "tip" -2 -2 -2 2 2 2\n$hbox 3 "root" -4 -4 -4 4 4 4\n'
    open(os.path.join(here, "test_hb.qc"), "w").write(qc2)
