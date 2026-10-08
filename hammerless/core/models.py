"""Custom models: a mesh from Blender becomes a game model (.mdl, .vvd, .dx90.vtx, .phy).

For now Valve's own compiler does the last step: Hammerless writes the model as SMD text (Valve's
interchange format: a skeleton of one bone, then triangles with position, normal and UV), a collision
model as SMD (convex pieces), and a QC script that tells studiomdl what kind of prop it is, then runs
studiomdl from the game's bin folder. studiomdl writes straight into the game's models/ folder.

Measured in L4D2 (2026-10-08):
- prop_static / prop_dynamic: a model compiled with $staticprop works for both.
- prop_physics: needs a model WITHOUT $staticprop and with a prop_data block naming a physics class
  from the game's scripts/propdata.txt (Wooden.Small...); without one the game deletes the prop at once.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
Vertex = tuple[Vec3, Vec3, tuple[float, float]]          # position, normal, uv (Hammer units, model space)

STATIC, DYNAMIC, PHYSICS = "STATIC", "DYNAMIC", "PHYSICS"


@dataclass
class ModelSpec:
    name: str                                   # "hammerless/<map>/<name>": models/<name>.mdl
    triangles: list[tuple[str, tuple[Vertex, Vertex, Vertex]]]   # (material name, three vertices)
    collision: list[tuple[list[Vec3], list[tuple[int, int, int]]]] = field(default_factory=list)
    kind: str = STATIC                          # STATIC / DYNAMIC (one compile serves both) / PHYSICS
    surfaceprop: str = "default"
    mass: float = 0.0                           # PHYSICS: kg (0: studiomdl works it out from the volume)
    physics_class: str = "Wooden.Medium"        # PHYSICS: a class from the game's scripts/propdata.txt
    materials_dir: str = ""                     # "models/hammerless/<map>/" (materials/<this><name>.vmt)


def model_files(name: str) -> list[str]:
    """What studiomdl writes for a model, relative to the game folder."""
    base = f"models/{name}"
    return [base + ext for ext in (".mdl", ".vvd", ".dx90.vtx", ".phy")]


def safe_name(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "model"


# ------------------------------------------------------------------ SMD / QC text

def _vertex_line(v: Vertex) -> str:
    (x, y, z), (nx, ny, nz), (u, w) = v
    return f"0 {x:.6f} {y:.6f} {z:.6f} {nx:.6f} {ny:.6f} {nz:.6f} {u:.6f} {w:.6f}"


SMD_HEADER = "version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\n"


def reference_smd(triangles) -> str:
    """The visible mesh: every triangle with its material, in one bone."""
    out = [SMD_HEADER, "triangles\n"]
    for material, verts in triangles:
        out.append(material + "\n")
        out.extend(_vertex_line(v) + "\n" for v in verts)
    out.append("end\n")
    return "".join(out)


def collision_smd(pieces) -> str:
    """The collision model: each convex piece's triangles (normals pointing out of the piece)."""
    out = [SMD_HEADER, "triangles\n"]
    for points, tris in pieces:
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        cz = sum(p[2] for p in points) / len(points)
        for a, b, c in tris:
            pa, pb, pc = points[a], points[b], points[c]
            ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
            n = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
            if n[0] * (pa[0] - cx) + n[1] * (pa[1] - cy) + n[2] * (pa[2] - cz) < 0:   # inward: turn it round
                pb, pc, n = pc, pb, (-n[0], -n[1], -n[2])
            ln = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
            n = (n[0] / ln, n[1] / ln, n[2] / ln)
            out.append("phys\n")
            out.extend(_vertex_line((p, n, (0.0, 0.0))) + "\n" for p in (pa, pb, pc))
    out.append("end\n")
    return "".join(out)


def qc_text(spec: ModelSpec) -> str:
    lines = [f'$modelname "{spec.name}.mdl"']
    if spec.kind != PHYSICS:
        lines.append("$staticprop")
    lines += ['$body body "model.smd"',
              f'$surfaceprop "{spec.surfaceprop}"',
              f'$cdmaterials "{spec.materials_dir}"',
              '$sequence idle "model.smd" fps 30']
    if spec.collision:
        opts = []
        if len(spec.collision) > 1:
            opts += ["$concave", f"$maxconvexpieces {max(len(spec.collision), 1)}"]
        if spec.kind == PHYSICS and spec.mass > 0:
            opts.append(f"$mass {spec.mass:g}")
        lines.append('$collisionmodel "collision.smd" { ' + " ".join(opts) + " }")
    if spec.kind == PHYSICS:
        lines.append('$keyvalues { prop_data { "base" "%s" } }' % spec.physics_class)
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ materials

def model_material_vmt(base_texture: str, translucent: bool = False, alphatest: bool = False) -> str:
    """A model material (VertexLitGeneric: lit per vertex and by the map's light, as models are)."""
    extra = ""
    if translucent:
        extra += '\t"$translucent" "1"\n'
    if alphatest:
        extra += '\t"$alphatest" "1"\n'
    return f'"VertexLitGeneric"\n{{\n\t"$basetexture" "{base_texture}"\n{extra}}}\n'


def base_texture_of(vmt_text: str) -> str | None:
    """The $basetexture of a material's VMT text."""
    m = re.search(r'"?\$basetexture"?\s+"?([^"\s]+)"?', vmt_text, re.I)
    return m.group(1).replace("\\", "/") if m else None


def write_model_materials(game_dir: str, materials_dir: str, materials: dict[str, tuple[str, bool, bool]]) -> list[str]:
    """materials: model material name -> (base texture, translucent, alphatest). Writes their VMTs under
    materials/<materials_dir>. Returns the files written (relative to game_dir)."""
    written = []
    folder = os.path.join(game_dir, "materials", *materials_dir.strip("/").split("/"))
    os.makedirs(folder, exist_ok=True)
    for name, (tex, translucent, alphatest) in materials.items():
        path = os.path.join(folder, name + ".vmt")
        text = model_material_vmt(tex, translucent, alphatest)
        if not (os.path.exists(path) and open(path, encoding="utf-8", errors="replace").read() == text):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        written.append(f"materials/{materials_dir.strip('/')}/{name}.vmt")
    return written


# ------------------------------------------------------------------ compiling

@dataclass
class ModelResult:
    ok: bool
    files: list[str]
    log: str


def compile_model(studiomdl: str, game_dir: str, spec: ModelSpec, work_dir: str) -> ModelResult:
    """Write the SMDs and QC into work_dir and run studiomdl; the model lands in game_dir/models/."""
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "model.smd"), "w", encoding="utf-8") as f:
        f.write(reference_smd(spec.triangles))
    if spec.collision:
        with open(os.path.join(work_dir, "collision.smd"), "w", encoding="utf-8") as f:
            f.write(collision_smd(spec.collision))
    qc = os.path.join(work_dir, "model.qc")
    with open(qc, "w", encoding="utf-8") as f:
        f.write(qc_text(spec))
    run = subprocess.run([studiomdl, "-game", game_dir, "-nop4", "-nox360", qc], capture_output=True, text=True,
                         cwd=work_dir, timeout=300,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    log = (run.stdout or "") + (run.stderr or "")
    files = [f for f in model_files(spec.name) if os.path.exists(os.path.join(game_dir, *f.split("/")))]
    ok = 'Completed "model.qc"' in log and any(f.endswith(".mdl") for f in files)
    return ModelResult(ok, files, log)


def studiomdl_errors(log: str) -> list[str]:
    return [l.strip() for l in log.splitlines() if l.strip().startswith("ERROR")]
