"""Build the zip for Blender 4.0 / 4.1, which predate extensions: an old-style add-on (bl_info in
__init__.py) inside a top-level 'hammerless' folder, installed with Edit > Preferences > Add-ons >
Install. Blender 4.2+ uses the extension zip from `blender --command extension build` instead.

Run:  python scripts/build_legacy_zip.py      -> dist/hammerless-<version>-blender4.0.zip
"""
import os
import re
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "hammerless")
manifest = open(os.path.join(SRC, "blender_manifest.toml"), encoding="utf-8").read()
version = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
out = os.path.join(ROOT, "dist", f"hammerless-{version}-blender4.0.zip")
os.makedirs(os.path.dirname(out), exist_ok=True)
count = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for folder, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
        for f in files:
            if f.endswith(".pyc") or f.startswith("."):
                continue
            full = os.path.join(folder, f)
            z.write(full, os.path.join("hammerless", os.path.relpath(full, SRC)))
            count += 1
print(f"built {out} ({count} files)")
