"""Valve's campaign maps: entities (latest entity patch) and nav meshes, newest content first."""
import glob, os, re, struct, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))   # the repo
G = os.environ.get("L4D2", r"C:/Program Files (x86)/Steam/steamapps/common/Left 4 Dead 2")
DIRS = ["update", "left4dead2_dlc3", "left4dead2_dlc2", "left4dead2_dlc1", "left4dead2"]

def find(name):
    for d in DIRS:
        p = f"{G}/{d}/maps/{name}"
        if os.path.exists(p):
            return p
    return None

def maps():
    names = set()
    for d in DIRS:
        for p in glob.glob(f"{G}/{d}/maps/c*m*_*.bsp"):
            n = os.path.basename(p)[:-4]
            if re.match(r"c\d+m\d+_", n) and not n.endswith("_sndscape"):
                names.add(n)
    return sorted(names, key=lambda n: (int(re.match(r"c(\d+)m(\d+)", n).group(1)), int(re.match(r"c(\d+)m(\d+)", n).group(2))))

def entity_text(name):
    lmp = find(f"{name}_l_0.lmp")
    if lmp:
        d = open(lmp, "rb").read()
        _o, lid, _v, ln, _r = struct.unpack_from("<5i", d)
        if lid == 0:
            return d[20:20 + ln].decode("latin-1")
    d = open(find(f"{name}.bsp"), "rb").read()
    _v, off, ln, _c = struct.unpack_from("<iiii", d, 8)
    return d[off:off + ln].decode("latin-1")

def entities(name):
    return [dict(re.findall(r'"([^"]*)"\s+"([^"]*)"', b)) for b in re.findall(r"\{([^{}]*)\}", entity_text(name))]

def nav(name):
    from hammerless.core.navfile import load_nav
    p = find(f"{name}.nav")
    return load_nav(p) if p else None
