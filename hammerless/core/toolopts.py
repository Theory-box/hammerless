"""Valve's compile options typed into Extra vbsp / vvis / vrad Options, sorted for Hammerless's own compilers so the
build never switches to Valve's tool because of them: the ones ours does are passed on; the rest are left out, with
a note in the build log (those that make no difference to the map, and those that would but ours doesn't do).

Each table: option (lower case) -> (how many values follow it, what happens). The options and their value counts
are the tools' own (Valve's published source, and the options L4D2's vbsp.exe, vvis_dll.dll and vrad_dll.dll know).
"""
from __future__ import annotations

OURS, NO_EFFECT, NOT_DONE = "ours", "no effect", "not done"

# (threads, logging, Valve's network compiling, crash dumps, paths, debug dumps: none of it changes the map)
_COMMON = {"-threads": (1, NO_EFFECT), "-v": (0, NO_EFFECT), "-verbose": (0, NO_EFFECT), "-low": (0, NO_EFFECT),
           "-steam": (0, NO_EFFECT), "-vproject": (1, NO_EFFECT), "-game": (1, NO_EFFECT),
           "-insert_search_path": (1, NO_EFFECT), "-fullminidumps": (0, NO_EFFECT), "-allowdebug": (0, NO_EFFECT),
           "-ds": (0, NO_EFFECT), "-lv": (0, NO_EFFECT), "-novconfig": (0, NO_EFFECT), "-tempcontent": (0, NO_EFFECT),
           "-xlsp": (0, NO_EFFECT), "-mpi": (0, NO_EFFECT)}

VBSP = dict(_COMMON, **{
    "-verboseentities": (0, NO_EFFECT), "-tmpout": (0, NO_EFFECT), "-glview": (0, NO_EFFECT),
    "-physlog": (0, NO_EFFECT), "-physstats": (0, NO_EFFECT), "-dumpcollide": (0, NO_EFFECT),
    "-dumpstaticprop": (0, NO_EFFECT), "-exportmoppobj": (0, NO_EFFECT), "-leaktest": (0, NO_EFFECT),
    "-keepstalezip": (0, NO_EFFECT), "-embed": (1, NO_EFFECT),
    # (switches that change the compiled map: debugging and legacy ones; Hammerless's compiler does what vbsp does
    # without them)
    "-onlyprops": (0, NOT_DONE), "-nodetail": (0, NOT_DONE), "-fulldetail": (0, NOT_DONE), "-nowater": (0, NOT_DONE),
    "-nomerge": (0, NOT_DONE), "-nomergewater": (0, NOT_DONE), "-noprune": (0, NOT_DONE), "-nosubdiv": (0, NOT_DONE),
    "-notjunc": (0, NOT_DONE), "-noweld": (0, NOT_DONE), "-nocsg": (0, NOT_DONE), "-noshare": (0, NOT_DONE),
    "-noopt": (0, NOT_DONE), "-micro": (1, NOT_DONE), "-block": (2, NOT_DONE), "-blocks": (4, NOT_DONE),
    "-bumpall": (0, NOT_DONE), "-lightifmissing": (0, NOT_DONE), "-forceskyvis": (0, NOT_DONE),
    "-nodrawtriggers": (0, NOT_DONE), "-allowdetailcracks": (0, NOT_DONE), "-novirtualmesh": (0, NOT_DONE),
    "-snapaxial": (0, NOT_DONE), "-writewatervolumes": (0, NOT_DONE), "-replacematerials": (0, NOT_DONE),
    "-luxelscale": (1, NOT_DONE), "-minluxelscale": (1, NOT_DONE), "-defaultluxelsize": (1, NOT_DONE),
    "-maxlightmapdim": (1, NOT_DONE), "-dxlevel": (1, NOT_DONE), "-xbox": (0, NOT_DONE), "-mopp": (0, NOT_DONE),
    "-polysoup": (0, NOT_DONE), "-polytope": (0, NOT_DONE), "-localphysx": (0, NOT_DONE),
    "-onlyents": (0, NO_EFFECT),          # (the build decides when it's entities only)
})

VVIS = dict(_COMMON, **{
    "-fast": (0, OURS), "-radius_override": (1, OURS),
    "-tmpin": (0, NO_EFFECT), "-mpi_listparams": (0, NO_EFFECT), "-trace": (2, NO_EFFECT),
    "-nosort": (0, NO_EFFECT),            # (the order vvis works through the portals in: speed, not the result)
})

VRAD = dict(_COMMON, **{
    "-bounce": (1, OURS), "-extra": (0, OURS), "-chop": (1, OURS), "-maxchop": (1, OURS), "-dispchop": (1, OURS),
    "-extrasky": (1, OURS), "-final": (0, OURS), "-noextra": (0, OURS), "-smooth": (1, OURS), "-lights": (1, OURS),
    "-staticproplighting": (0, OURS), "-staticproppolys": (0, OURS), "-hdr": (0, NO_EFFECT),
    "-fast": (0, NOT_DONE),               # (vrad's rough preview: Hammerless's Fast quality is its own, quality.py)
    "-ldr": (0, NO_EFFECT), "-both": (0, NO_EFFECT),      # (always HDR: L4D2 only uses HDR lighting)
    "-stoponexit": (0, NO_EFFECT), "-rederrors": (0, NO_EFFECT), "-dump": (0, NO_EFFECT),
    "-dumpnormals": (0, NO_EFFECT), "-dumptrace": (0, NO_EFFECT), "-dumppropmaps": (0, NO_EFFECT),
    "-debugextra": (0, NO_EFFECT), "-loghash": (0, NO_EFFECT), "-mpi_listparams": (0, NO_EFFECT),
    "-textureshadows": (0, OURS), "-softsun": (1, OURS), "-fastambient": (0, NOT_DONE),
    "-noskyboxrecurse": (0, NOT_DONE), "-nodetaillight": (0, NOT_DONE), "-onlystaticprops": (0, NOT_DONE),
    "-onlydetail": (0, NOT_DONE), "-staticproplighting3": (0, NOT_DONE), "-staticpropnormals": (0, NOT_DONE),
    "-luxeldensity": (1, NOT_DONE), "-finitefalloff": (0, NOT_DONE), "-disppatchradius": (1, NOT_DONE),
    "-coring": (1, NOT_DONE),
    # (debug options of Valve's source that L4D2's vrad.exe refuses; hlvrad does them as that source does; -sky's
    # value is read by nothing there)
    "-scale": (1, OURS), "-ambient": (3, OURS), "-sky": (1, NO_EFFECT),
    "-dlight": (1, NOT_DONE), "-dlightmap": (0, NOT_DONE), "-centersamples": (0, NOT_DONE),
    "-largedispsampleradius": (0, NOT_DONE), "-maxdispsamplesize": (1, NOT_DONE), "-notexscale": (0, NOT_DONE),
    "-nossprops": (0, NOT_DONE),
})

TABLES = {"vbsp": VBSP, "vvis": VVIS, "vrad": VRAD}

# (options of Valve's source that L4D2's own tools refuse, with how many values follow them)
VALVE_REFUSES = {"vrad": {"-scale": 1, "-ambient": 3, "-sky": 1}}


def valve_accepts(tool: str, words: list[str]) -> list[str]:
    """Extra options for Valve's own tool without the ones it refuses (and their values): when Valve's vrad runs
    instead of ours, -scale / -ambient / -sky would stop it."""
    refused = VALVE_REFUSES.get(tool, {})
    out, i = [], 0
    while i < len(words):
        n = refused.get(words[i].lower())
        if n is None:
            out.append(words[i])
            i += 1
        else:
            i += 1 + n
    return out


def sort_options(tool: str, text: str) -> tuple[list[str], list[str]]:
    """Extra options typed for Valve's tool ("vbsp", "vvis", "vrad") -> (the ones to pass to ours, notes for the
    build log about the ones left out). An option the table doesn't know is left out too (with its values: the
    words after it that don't start with '-')."""
    table = TABLES[tool]
    words = text.split()
    keep, no_effect, not_done, unknown, short = [], [], [], [], []
    i = 0
    while i < len(words):
        w = words[i]
        entry = table.get(w.lower())
        if entry is None:
            j = i + 1
            while j < len(words) and not words[j].startswith("-"):
                j += 1
            unknown.append(" ".join(words[i:j]))
            i = j
            continue
        n, kind = entry
        group = words[i:i + 1 + n]
        i += 1 + n
        if len(group) < 1 + n:                 # (a value missing at the end: leave it out)
            short.append(" ".join(group))
        elif kind == OURS:
            keep += group
        elif kind == NO_EFFECT:
            no_effect.append(" ".join(group))
        else:
            not_done.append(" ".join(group))
    notes = []
    if no_effect:
        notes.append(f"Extra {tool} options left out (they make no difference to the map): " + ", ".join(no_effect))
    if not_done:
        notes.append(f"Extra {tool} options left out (Hammerless's compiler doesn't do them yet): "
                     + ", ".join(not_done))
    if short:
        notes.append(f"Extra {tool} options left out (a value is missing after them): " + ", ".join(short))
    if unknown:
        notes.append(f"Extra {tool} options left out (not options {tool} has): " + ", ".join(unknown))
    return keep, notes
