"""Read the parts of a v49 .mdl we care about (layout from Source SDK 2013 public/studio.h)."""
import struct, sys, math

def cstr(b, o):
    return b[o:b.index(b"\0", o)].decode("latin-1")

def read(path):
    b = open(path, "rb").read()
    I = lambda o: struct.unpack_from("<i", b, o)[0]
    print(path, "version", I(4), "flags 0x%x" % I(152))
    nb, bi = I(156), I(160)
    nseq, si = I(188), I(192)                 # localseq
    # studiohdr_t offsets: 164 numbonecontrollers,168 idx,172 numhitboxsets,176 idx,180 numlocalanim,184 idx,
    # 188 numlocalseq,192 idx,196 activitylistversion,200 eventsindexed,204 numtextures...
    nhs, hsi = I(172), I(176)
    for i in range(nb):
        o = bi + i * 216
        name = cstr(b, o + I(o))
        parent = I(o + 4)
        pos = struct.unpack_from("<3f", b, o + 32)
        quat = struct.unpack_from("<4f", b, o + 44)
        rot = struct.unpack_from("<3f", b, o + 60)
        flags = I(o + 160)
        print(f" bone {i} {name!r} parent {parent} pos {tuple(round(x,3) for x in pos)} quat {tuple(round(x,4) for x in quat)} rot {tuple(round(x,4) for x in rot)} flags 0x{flags:x}")
    for h in range(nhs):
        o = hsi + h * 12
        print(" hitboxset", cstr(b, o + I(o)), "n", I(o + 4))
        for k in range(I(o + 4)):
            ho = o + I(o + 8) + k * 68
            bone, group = I(ho), I(ho + 4)
            mn = struct.unpack_from("<3f", b, ho + 8); mx = struct.unpack_from("<3f", b, ho + 20)
            print(f"  hbox group {group} bone {bone} {tuple(round(x,2) for x in mn)} {tuple(round(x,2) for x in mx)}")
    for s in range(nseq):
        o = si + s * 212
        label = cstr(b, o + I(o + 4)); act = cstr(b, o + I(o + 8))
        flags, activity, actweight, numevents, eventindex = I(o + 12), I(o + 16), I(o + 20), I(o + 24), I(o + 28)
        print(f" seq {label!r} act {act!r} ({activity}) w {actweight} flags 0x{flags:x} events {numevents}")
        for e in range(numevents):
            eo = o + eventindex + e * 80
            cycle = struct.unpack_from("<f", b, eo)[0]
            ev, typ = I(eo + 4), I(eo + 8)
            opts = b[eo + 12:eo + 76].split(b"\0")[0].decode("latin-1")
            name = cstr(b, eo + I(eo + 76)) if I(eo + 76) else ""
            print(f"   event {ev} type {typ} cycle {cycle:.3f} opts {opts!r} name {name!r}")
    # attachments: 236 numlocalattachments 240 idx? (after numskinref 220,numskinfamilies 224,skinindex 228,numbodyparts 232, bodypartindex 236)
    na, ai = I(240), I(244)
    for a in range(na):
        o = ai + a * 92
        print(" attachment", cstr(b, o + I(o)), "flags", I(o + 4), "bone", I(o + 8),
              "mat", [round(x, 3) for x in struct.unpack_from("<12f", b, o + 12)])
    # includemodels: numincludemodels 336? search printable
    for key in (b"infected/anim_common",):
        print(" includemodel string present:", key in b)

for p in sys.argv[1:]:
    read(p)
