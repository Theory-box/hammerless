"""The nav mesh generator and analysis in their own process, so they don't share Python's lock with Blender (and the
build's other Python work) while vis and lighting run.

Parent: run(kind, args, on_stage, should_stop) starts `python -m <package>.core.navworker job result` with the same
Python (Blender's), and returns what the child returns. The child prints one "stage<TAB>name<TAB>count" line per
stage and pickles {"result": ...} or {"error": ...} to the result file. should_stop() is checked ten times a second;
true kills the child and raises RuntimeError("cancelled").

kinds: "predict" (navpredict.predict: args vmf_text, regions, climbs, wall_climbs) -> NavMesh;
"analyze" (navanalyze.analyze: args mesh, vmf_text, bsp_path, root) -> the analyzed NavMesh.
"""
from __future__ import annotations

import os
import pickle
import subprocess
import sys
import tempfile
import threading
import time

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # .../hammerless
# the add-on's import name: "bl_ext.user_default.hammerless" when installed as an extension. The child loads the
# package under exactly this name, so pickled classes (NavMesh, NavRegion...) mean the same both ways.
PACKAGE = __name__.rsplit(".core.navworker", 1)[0] if __name__.endswith(".core.navworker") else "hammerless"

# (the child: parent packages of the name as empty namespaces, then the add-on folder as the last one)
_BOOT = """import importlib.util, sys, types
name, folder = sys.argv[1], sys.argv[2]
parts = name.split(".")
for i in range(1, len(parts)):
    m = types.ModuleType(".".join(parts[:i])); m.__path__ = []
    sys.modules.setdefault(m.__name__, m)
spec = importlib.util.spec_from_file_location(name, folder + "/__init__.py", submodule_search_locations=[folder])
pkg = importlib.util.module_from_spec(spec); sys.modules[name] = pkg; spec.loader.exec_module(pkg)
importlib.import_module(name + ".core.navworker")._child(sys.argv[3], sys.argv[4])
"""


def python_exe() -> str:
    """Blender's own Python (sys.executable is it since Blender 2.92; Blender itself only on older builds)."""
    exe = sys.executable
    if os.path.basename(exe).lower().startswith("blender"):
        raise RuntimeError("no separate Python found next to Blender")
    return exe


def run(kind: str, args: dict, on_stage=None, should_stop=None):
    work = tempfile.mkdtemp(prefix="hammerless_nav_")
    job, out = os.path.join(work, "job.pickle"), os.path.join(work, "result.pickle")
    with open(job, "wb") as f:
        pickle.dump({"kind": kind, "args": args}, f, protocol=pickle.HIGHEST_PROTOCOL)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen([python_exe(), "-s", "-c", _BOOT, PACKAGE, PACKAGE_DIR, job, out],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
    from .childkill import adopt
    adopt(proc)                  # (ends with Blender)
    err: list[str] = []

    def read_stdout():
        for line in proc.stdout:
            parts = line.rstrip("\n").split("\t")
            if parts[0] == "stage" and len(parts) == 3 and on_stage is not None:
                try:
                    on_stage(parts[1], int(parts[2]))
                except Exception:        # (the callback is only for showing progress)
                    pass
    readers = [threading.Thread(target=read_stdout, daemon=True),
               threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)]
    for r in readers:
        r.start()
    try:
        while proc.poll() is None:
            if should_stop is not None and should_stop():
                proc.kill()
                proc.wait()
                raise RuntimeError("cancelled")
            time.sleep(0.1)
        for r in readers:
            r.join(5)
        if not os.path.exists(out):
            tail = "".join(err).strip().splitlines()[-1:] or [f"exit code {proc.returncode}"]
            raise RuntimeError(f"nav process failed: {tail[0]}")
        with open(out, "rb") as f:
            res = pickle.load(f)
        if "error" in res:
            raise RuntimeError(res["error"])
        return res["result"]
    finally:
        for p in (job, out):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass


def predict(vmf_text: str, regions, progress=None, climbs=(), wall_climbs=False, should_stop=None):
    """navpredict.predict in its own process (and remembered here for navpredict.cached)."""
    from .navpredict import remember
    mesh = run("predict", dict(vmf_text=vmf_text, regions=list(regions), climbs=list(climbs),
                               wall_climbs=wall_climbs), progress, should_stop)
    return remember(mesh, vmf_text, regions, climbs, wall_climbs)


def analyze(mesh, vmf_text: str, bsp_path: str, root: str, progress=None, should_stop=None) -> None:
    """navanalyze.analyze in its own process; `mesh` is updated in place (only once the analysis is complete)."""
    done = run("analyze", dict(mesh=mesh, vmf_text=vmf_text, bsp_path=bsp_path, root=root),
               (lambda stage, _n: progress(stage)) if progress else None, should_stop)
    mesh.__dict__.update(done.__dict__)


def _child(job_path: str, out_path: str) -> None:
    with open(job_path, "rb") as f:
        job = pickle.load(f)
    a = job["args"]

    def stage(name, count=0):
        print(f"stage\t{name}\t{int(count or 0)}", flush=True)
    try:
        if job["kind"] == "predict":
            from .navpredict import predict
            res = predict(a["vmf_text"], a["regions"], stage, a["climbs"], a["wall_climbs"])
        elif job["kind"] == "analyze":
            from .navanalyze import analyze
            from .vpk import GameContent
            res = a["mesh"]
            analyze(res, a["vmf_text"], a["bsp_path"], GameContent(a["root"]),
                    os.path.join(a["root"], "left4dead2"), progress=stage)
        else:
            raise ValueError(f"unknown nav job {job['kind']!r}")
        out = {"result": res}
    except Exception as ex:
        out = {"error": str(ex) or type(ex).__name__}
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        pickle.dump(out, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, out_path)


if __name__ == "__main__":
    _child(sys.argv[1], sys.argv[2])
