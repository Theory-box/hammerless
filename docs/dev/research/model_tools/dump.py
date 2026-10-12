"""python dump.py models/x.mdl [nseq]  -> full text dump of one model."""
import sys
from collections import Counter
from gamefs import gc, which
from mdlparse import MDL, flagnames, STUDIOHDR_FLAGS, ANIM_FLAGS, BONE_FLAGS


def load(path):
    g = gc()
    data = g.read(path)
    ani = None
    m = MDL(data)
    if m.animblockname:
        ani = g.read(m.animblockname)
        m.ani = ani
    return m


def dump(path, nseq=40, out=sys.stdout):
    p = lambda *a: print(*a, file=out)
    g = gc()
    m = load(path)
    h = m.h
    p(f"##### {path}   (from {[x.split(chr(92))[-2] for x in which(path)]}, {len(m.b)} bytes)")
    p(f"name={h['name']} version={h['version']} checksum={h['checksum']:#x}")
    p(f"flags={h['flags']:#x} {flagnames(h['flags'], STUDIOHDR_FLAGS)}")
    p(f"hull {h['hull_min']} {h['hull_max']}  view {h['view_bbmin']} {h['view_bbmax']}  eye {h['eyeposition']}")
    p(f"surfaceprop={g and m.b[h['surfacepropindex']:h['surfacepropindex']+40].split(bytes(1))[0]} mass={h['mass']} contents={h['contents']:#x}")
    cnt = {k: h[k] for k in ["numbones", "numbonecontrollers", "numhitboxsets", "numlocalanim", "numlocalseq", "activitylistversion",
                             "numtextures", "numskinfamilies", "numbodyparts", "numlocalattachments", "numlocalnodes", "numflexdesc",
                             "numflexcontrollers", "numflexrules", "numikchains", "numlocalposeparameters",
                             "numlocalikautoplaylocks", "numincludemodels", "numanimblocks", "virtualModel", "animblockModel"]}
    p("counts:", cnt)
    p("hdr2:", m.hdr2)
    p("animblockname:", repr(m.animblockname), "animblocks:", len(m.animblocks),
      (m.animblocks[:3], "...", m.animblocks[-2:]) if m.animblocks else "")
    if m.ani is not None:
        p("  .ani size", len(m.ani), "head bytes", m.ani[:16])
    p("includemodels:", m.includemodels())
    kv = m.keyvalues()
    if kv:
        p("keyvalues:", kv[:600].replace("\n", " | "))
    bp = m.bodyparts()
    p("bodyparts:", bp)
    tex, cd = m.textures()
    p("textures:", tex[:12], "cdtex:", cd)
    if h["numflexdesc"]:
        p("flexdescs:", m.flexdescs()[:20], "...")
    p(f"--- bones ({len(m.bones)}) [i name parent pos | flags | proc]")
    for b in m.bones:
        par = m.bones[b["parent"]]["name"] if b["parent"] >= 0 else "-"
        p(f"  {b['i']:3d} {b['name']:<40s} <- {par:<34s} pos=({b['pos'][0]:.2f},{b['pos'][1]:.2f},{b['pos'][2]:.2f}) "
          f"fl={b['flags']:#x} proc={b['proctype']} phys={b['physicsbone']} {b['surfaceprop']}")
    p("--- hitboxsets")
    for name, boxes in m.hitboxsets():
        p(f"  set '{name}' {len(boxes)} boxes")
        for x in boxes:
            p(f"    bone {x['bone']:3d} {m.bones[x['bone']]['name']:<32s} group {x['group']} "
              f"min={tuple(round(v, 1) for v in x['bbmin'])} max={tuple(round(v, 1) for v in x['bbmax'])} {x['name']}")
    p("--- attachments")
    for a in m.attachments():
        p(f"  {a['name']:<28s} bone {a['bone']:3d} {m.bones[a['bone']]['name'] if 0 <= a['bone'] < len(m.bones) else '?'} "
          f"origin={tuple(round(v, 2) for v in a['origin'])} fl={a['flags']}")
    p("--- poseparams")
    for x in m.poseparams():
        p("  ", x)
    p("--- ikchains")
    for x in m.ikchains():
        p("  ", x["name"], x["linktype"], [m.bones[i]["name"] for i in x["bones"]])
    ads = m.animdescs()
    p(f"--- animdescs ({len(ads)})")
    fc = Counter()
    for a in ads:
        fc[(a["animblock"] != 0, a["sectionframes"] != 0, a["flags"] & 0x40 != 0)] += 1
    p("  (in .ani?, sectioned?, frameanim?) counts:", dict(fc))
    p("  fps values:", Counter(round(a["fps"], 2) for a in ads).most_common(6))
    p("  numframes total", sum(a["numframes"] for a in ads), "max", max((a["numframes"] for a in ads), default=0))
    for a in ads[:12]:
        p(f"  {a['i']:4d} {a['name']:<40s} fps={a['fps']:.1f} frames={a['numframes']} fl={flagnames(a['flags'], ANIM_FLAGS)} "
          f"blk={a['animblock']} idx={a['animindex']} sec={a['sectionframes']} ik={a['numikrules']} mov={a['nummovements']} "
          f"zero=({a['zeroframespan']},{a['zeroframecount']},{a['zeroframeindex']})")
    seqs = m.seqdescs()
    p(f"--- sequences ({len(seqs)})")
    p("  activities used:", len({s['activityname'] for s in seqs if s['activityname']}))
    for s in seqs[:nseq]:
        mods = (" mods=" + ",".join(s["activitymodifiers"])) if s["activitymodifiers"] else ""
        p(f"  {s['i']:4d} {s['label']:<36s} {s['activityname']:<34s} w={s['actweight']} blends={s['groupsize']} "
          f"anims={s['anims'][:4]}{'...' if len(s['anims']) > 4 else ''} fl={s['flags']:#x} ev={len(s['events'])} "
          f"layers={s['numautolayers']} ik={s['numikrules']}{mods}")
    return m


if __name__ == "__main__":
    dump(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 40)
