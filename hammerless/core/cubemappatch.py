"""Cubemap material patches for hlvbsp (as vbsp's cubemap.cpp / materialpatch.cpp make them).

vbsp gives every surface whose material has `$envmap env_cubemap` a one-off material per cubemap:
maps/<map>/<material>_<x>_<y>_<z>, a "patch" .vmt that includes the original and replaces
$envmap with that cubemap's texture (and points a dependent material - $bottommaterial,
$crackmaterial, $fallbackmaterial - at its own patched copy). The patch's "replace" section
mirrors the original's block tree, written by KeyValues' rules: a block appears only when it has
something in it, and names are spelled the way the KeyValues symbol table first saw them.

Python knows the materials, hlvbsp knows which surface gets which cubemap, so this writes, per
material, whether it is specular and the patch text with @CUBE@ / @DEP@ for the names hlvbsp fills in.
"""
from __future__ import annotations

import re

DEPENDENT_VARS = ("$bottommaterial", "$crackmaterial", "$fallbackmaterial")


class KV:
    """A KeyValues node: a name and either a string value or ordered children."""
    __slots__ = ("name", "value", "children")

    def __init__(self, name: str, value: str | None = None):
        self.name = name
        self.value = value
        self.children: list[KV] = []

    def is_block(self) -> bool:
        return self.value is None

    def get_string(self, key: str) -> str | None:
        """KeyValues::GetString: the first child with the name; a block has no string."""
        for c in self.children:
            if c.name.lower() == key.lower():
                return c.value
        return None

    def find(self, key: str, create: bool = False) -> "KV | None":
        for c in self.children:
            if c.name.lower() == key.lower():
                return c
        if create:
            c = KV(key)
            self.children.append(c)
            return c
        return None

    def set_string(self, key: str, value: str) -> None:
        c = self.find(key, True)
        c.value = value
        c.children = []


def _condition_true(cond: str) -> bool:
    """A KeyValues [$...] conditional, evaluated as on Windows PC."""
    platform = {"$WIN32": True, "$WINDOWS": True, "$X360": False, "$PS3": False, "$OSX": False,
                "$LINUX": False, "$POSIX": False, "$GAMECONSOLE": False}
    cond = cond.strip("[]").strip()
    result = False
    for part in cond.split("||"):
        ok = True
        for term in part.split("&&"):
            term = term.strip()
            neg = term.startswith("!")
            term = term.lstrip("!").upper()
            v = platform.get(term, False)
            ok = ok and (not v if neg else v)
        result = result or ok
    return result


_TOKEN = re.compile(r'"([^"]*)"|(\{)|(\})|(\[[^\]]*\])|([^\s{}"\[\]]+)')


def parse_kv(text: str) -> KV | None:
    """The first top-level block of a KeyValues text (a .vmt), with conditionals applied."""
    text = re.sub(r"//[^\n]*", "", text)
    toks: list[tuple[str, str]] = []
    for m in _TOKEN.finditer(text):
        if m.group(1) is not None:
            toks.append(("s", m.group(1)))
        elif m.group(2):
            toks.append(("{", "{"))
        elif m.group(3):
            toks.append(("}", "}"))
        elif m.group(4):
            toks.append(("c", m.group(4)))
        else:
            toks.append(("s", m.group(5)))
    pos = 0

    def cond_ok() -> bool:
        nonlocal pos
        if pos < len(toks) and toks[pos][0] == "c":
            ok = _condition_true(toks[pos][1])
            pos += 1
            return ok
        return True

    def block(node: KV) -> None:
        nonlocal pos
        while pos < len(toks):
            kind, val = toks[pos]
            if kind == "}":
                pos += 1
                return
            if kind != "s":
                pos += 1
                continue
            pos += 1
            name = val
            if pos < len(toks) and toks[pos][0] == "c":     # [cond] before a block
                ok = cond_ok()
                if pos < len(toks) and toks[pos][0] == "{":
                    pos += 1
                    child = KV(name)
                    block(child)
                    if ok:
                        node.children.append(child)
                continue
            if pos < len(toks) and toks[pos][0] == "{":
                pos += 1
                child = KV(name)
                block(child)
                node.children.append(child)
                continue
            if pos < len(toks) and toks[pos][0] == "s":
                value = toks[pos][1]
                pos += 1
                if cond_ok():
                    node.children.append(KV(name, value))
                continue

    while pos < len(toks):
        kind, val = toks[pos]
        pos += 1
        if kind == "s" and pos < len(toks):
            cond_ok()
            if pos < len(toks) and toks[pos][0] == "{":
                pos += 1
                root = KV(val)
                block(root)
                return root
    return None


def _has_key(kv: KV, key: str) -> bool:
    if kv.get_string(key) is not None:
        return True
    return any(_has_key(c, key) for c in kv.children if c.is_block())


def _has_pair(kv: KV, key: str, value: str) -> bool:
    v = kv.get_string(key)
    if v is not None and v.lower() == value.lower():
        return True
    return any(_has_pair(c, key, value) for c in kv.children if c.is_block())


class CubemapMaterials:
    """Reads the materials (in the order vbsp loads them) and builds their records."""

    def __init__(self, read_text):
        self._read = read_text                  # (material name) -> vmt text or None
        self._kv: dict[str, KV | None] = {}
        self.spelling: dict[str, str] = {}      # lowercase -> the KeyValues symbol table's spelling

    def load(self, name: str) -> KV | None:
        key = name.lower().replace("\\", "/")
        if key not in self._kv:
            text = self._read(key)
            kv = parse_kv(text) if text else None
            self._kv[key] = kv
            if kv:
                self._learn(kv)
        return self._kv[key]

    def _learn(self, kv: KV) -> None:
        self.spelling.setdefault(kv.name.lower(), kv.name)
        for c in kv.children:
            if c.is_block():
                self._learn(c)
            else:
                self.spelling.setdefault(c.name.lower(), c.name)

    def spell(self, s: str) -> str:
        return self.spelling.setdefault(s.lower(), s)

    def dependent(self, name: str) -> tuple[str, str] | None:
        """(material var, dependent material) as vbsp's FindDependentMaterial picks it."""
        kv = self.load(name)
        if not kv:
            return None
        for var in DEPENDENT_VARS:
            dep = kv.get_string(var)
            if dep is None:
                continue
            if dep.lower() == name.lower():
                continue
            return var, dep
        return None

    def specular(self, name: str, depth: int = 0) -> bool:
        """DoesMaterialOrDependentsUseEnvmap."""
        kv = self.load(name)
        if not kv or depth > 8:
            return False
        if _has_key(kv, "$envmap"):
            return True
        dep = self.dependent(name)
        return bool(dep) and self.specular(dep[1], depth + 1)

    def patchable(self, name: str, depth: int = 0) -> bool:
        kv = self.load(name)
        if not kv or depth > 8:
            return False
        if _has_pair(kv, "$envmap", "env_cubemap"):
            return True
        dep = self.dependent(name)
        return bool(dep) and self.patchable(dep[1], depth + 1)

    def template(self, name: str) -> list[str]:
        """The patch .vmt's lines for `name`, with @NAME@ (the material as referenced), @CUBE@ (the
        cubemap texture) and @DEP@ (the dependent's patched name) for hlvbsp to fill in."""
        kv = self.load(name)
        infos = []
        if _has_pair(kv, "$envmap", "env_cubemap"):
            infos.append((self.spell("$envmap"), "env_cubemap", "@CUBE@"))
        dep = self.dependent(name)
        if dep and self.patchable(dep[1]):
            infos.append((self.spell(dep[0]), None, "@DEP@"))
        section = KV(self.spell("replace"))

        def rec(orig: KV, patch: KV) -> None:
            for key, required, value in infos:
                v = orig.get_string(key)
                if v is None:
                    continue
                if required and v.lower() != required.lower():
                    continue
                patch.set_string(key, value)
            for c in orig.children:
                if c.is_block():
                    rec(c, patch.find(self.spell(c.name), True))

        rec(kv, section)
        lines: list[str] = []

        def write(node: KV, indent: int) -> None:
            lines.append("\t" * indent + f'"{node.name}"')
            lines.append("\t" * indent + "{")
            for c in node.children:
                if c.children:
                    write(c, indent + 1)
                elif c.value:
                    lines.append("\t" * (indent + 1) + f'"{c.name}"\t\t"{c.value}"')
            lines.append("\t" * indent + "}")

        lines.append(f'"{self.spell("patch")}"')
        lines.append("{")
        lines.append("\t" + f'"{self.spell("include")}"' + "\t\t" + '"materials/@NAME@.vmt"')
        write(section, 1)
        lines.append("}")
        return lines


def write_cubemap_table(path: str, material_names: list[str], read_text) -> int:
    """Records for the map's materials and their dependents:
    mat <tab> name <tab> specular <tab> patchable <tab> dependent var <tab> dependent <tab> line count,
    then the template lines (patchable ones only)."""
    cm = CubemapMaterials(read_text)
    order: list[str] = []
    seen: set[str] = set()

    def visit(name: str, depth: int = 0) -> None:
        if name in seen or depth > 8:
            return
        seen.add(name)
        order.append(name)
        cm.load(name)
    for n in material_names:
        visit(n)
    # dependents are loaded after the map's own materials (when vbsp looks for envmaps)
    i = 0
    while i < len(order):
        dep = cm.dependent(order[i])
        if dep:
            visit(dep[1])
        i += 1
    out: list[str] = []
    for name in order:
        dep = cm.dependent(name)
        patchable = cm.patchable(name)
        lines = cm.template(name) if patchable else []
        out.append("\t".join(["mat", name, str(int(cm.specular(name))), str(int(patchable)),
                              dep[0] if dep else "-", dep[1] if dep else "-", str(len(lines))]))
        out.extend(lines)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    return len(order)
