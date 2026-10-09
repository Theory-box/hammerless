"""Build the release zip: one zip for Blender 4.0 to 4.5. The add-on sits in a top-level
'hammerless' folder with both bl_info (old-style add-ons, Blender 4.0 / 4.1: Preferences > Add-ons >
Install) and blender_manifest.toml (extensions, 4.2+: Get Extensions > Install from Disk; the old
Add-ons route works there too). Checked: installs, enables and draws its panels in 4.0, 4.2, 4.3,
4.4 and 4.5 through both routes.

Run:  python scripts/build_release_zip.py      -> dist/hammerless-<version>.zip
"""
import os
import re
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "hammerless")
manifest = open(os.path.join(SRC, "blender_manifest.toml"), encoding="utf-8").read()
version = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
out = os.path.join(ROOT, "dist", f"hammerless-{version}.zip")
os.makedirs(os.path.dirname(out), exist_ok=True)
count = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for folder, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for f in files:
            if f.endswith(".pyc") or f.startswith("."):
                continue
            # the only DLL the add-on ships is the nav library; others in core/ (embree4.dll, tbb12.dll for
            # hlvrad's shelved -embree build) are gitignored local leftovers
            if f.lower().endswith(".dll") and f != "_hlnav.dll":
                continue
            full = os.path.join(folder, f)
            z.write(full, os.path.join("hammerless", os.path.relpath(full, SRC)))
            count += 1
print(f"built {out} ({count} files)")
