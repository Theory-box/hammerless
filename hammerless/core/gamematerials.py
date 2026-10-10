"""Find the texture behind a game material (for viewport previews)."""
from __future__ import annotations

import os

from .vmf import parse


def _read_text(content, rel_path: str, game_dir: str | None) -> str | None:
    """rel_path like 'materials/concrete/x.vmt'. Loose files in the game folder win (our own
    exported materials live there), then the game's VPKs."""
    if game_dir:
        full = os.path.join(game_dir, *rel_path.split("/"))
        if os.path.exists(full):
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read()
    data = content.read(rel_path) if content else None
    return data.decode("utf-8", "replace") if data else None


def read_vmt(content, material: str, game_dir: str | None = None, depth: int = 0) -> dict[str, str]:
    """Material parameters (lowercase keys) with 'patch' materials resolved.
    Includes the shader name under the key 'shader'."""
    text = _read_text(content, f"materials/{material.lower()}.vmt", game_dir)
    if not text or depth > 4:
        return {}
    blocks = parse(text)
    if not blocks:
        return {}
    root = blocks[0]
    params: dict[str, str] = {}
    if root.name.lower() == "patch":
        include = (root.get("include") or "").lower().replace("\\", "/")
        if include.startswith("materials/"):
            include = include[len("materials/"):]
        if include.endswith(".vmt"):
            include = include[:-4]
        params = read_vmt(content, include, game_dir, depth + 1)
        for section in ("insert", "replace"):
            for b in root.blocks(section):
                for it in b.items:
                    if isinstance(it, tuple):
                        params[it[0].lower()] = it[1]
        return params
    params["shader"] = root.name.lower()
    for it in root.items:
        if isinstance(it, tuple):
            params[it[0].lower()] = it[1]
    return params


def base_texture(content, material: str, game_dir: str | None = None) -> str | None:
    """Texture path (no extension, under materials/) for a material, or None."""
    p = read_vmt(content, material, game_dir)
    tex = p.get("$basetexture")
    if not tex:
        return None
    return texture_path(tex)


def texture_path(tex: str) -> str:
    """A $basetexture as the engine finds it: it takes "x.vtf" and "materials/x" too (268 of the game's own
    materials say .vtf)."""
    tex = tex.strip().lower().replace("\\", "/")
    tex = tex[:-4] if tex.endswith(".vtf") else tex
    return tex[10:] if tex.startswith("materials/") else tex


def read_texture_bytes(content, texture: str, game_dir: str | None = None) -> bytes | None:
    rel = f"materials/{texture}.vtf"
    if game_dir:
        full = os.path.join(game_dir, *rel.split("/"))
        if os.path.exists(full):
            with open(full, "rb") as f:
                return f.read()
    return content.read(rel) if content else None
