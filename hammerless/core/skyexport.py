"""Map-local sky material brightness; original textures and bake intensity stay unchanged."""
import hashlib
import math
import re
from pathlib import Path
from .gamematerials import _read_text
from .vmf import Block, parse

SIDES = ('rt', 'lf', 'bk', 'ft', 'up', 'dn')


def _scale_color(block, scale, default=False):
    """Scale each explicit shader override, leaving inherited colours inherited."""
    color = block.get('$color')
    if color is not None or default:
        color = color or '[1 1 1]'
        values = [float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?', color)]
        if len(values) != 3 or not all(math.isfinite(v) for v in values):
            raise ValueError(f'Invalid sky colour: {color}')
        if '{' in color:
            values = [v / 255 for v in values]
        block.items = [x for x in block.items if not (isinstance(x, tuple) and x[0].lower() == '$color')]
        block.kv('$color', '[' + ' '.join(f'{v * scale:.9g}' for v in values) + ']')
    for child in block.items:
        if isinstance(child, Block) and child.name.lower() != 'proxies':
            _scale_color(child, scale)


def _material(content, name, game_dir, depth=0):
    if depth > 8:
        raise ValueError('Sky material patch includes are recursive')
    text = _read_text(content, 'materials/' + name + '.vmt', game_dir)
    blocks = parse(text) if text else []
    if not blocks:
        raise ValueError(f"Sky material '{name}' could not be read")
    block = blocks[0]
    if block.name.lower() != 'patch':
        return block
    include = (block.get('include') or '').replace('\\', '/').lower()
    include = include.removeprefix('materials/').removesuffix('.vmt')
    base = _material(content, include, game_dir, depth + 1)
    for section in ('insert', 'replace'):
        for patch in block.blocks(section):
            for item in patch.items:
                key = item[0] if isinstance(item, tuple) else item.name
                base.items = [x for x in base.items if (x[0] if isinstance(x, tuple) else x.name).lower() != key.lower()]
                base.items.append(item)
    return base


def sky_materials(content, game_dir, map_name, sky, stops):
    """Return exported sky name and six VMTs, or the original name at zero stops."""
    if not math.isfinite(stops) or abs(stops) > 32:
        raise ValueError('Sky brightness must be between -32 and 32 stops')
    if stops == 0:
        return sky, {}
    if not re.fullmatch(r'[a-z0-9_]+', map_name):
        raise ValueError('Invalid map name for sky materials')
    scale = 2.0 ** stops
    materials = {}
    for side in SIDES:
        block = _material(content, 'skybox/' + sky + side, game_dir)
        if block.name.lower() not in ('sky', 'sky_hdr_dx9', 'sky_dx9', 'unlitgeneric'):
            raise ValueError(f"Unsupported sky shader '{block.name}'")
        _scale_color(block, scale, default=True)
        lines = []
        block.write(lines)
        materials[side] = ''.join(lines)
    digest = hashlib.sha256(''.join(materials.values()).encode()).hexdigest()[:16]
    name = f'hammerless/{map_name}/sky_{digest}'
    return name, {f'materials/skybox/{name}{side}.vmt': text for side, text in materials.items()}


def write_materials(game_dir, files):
    for relative, text in files.items():
        path = Path(game_dir) / relative
        data = text.encode('utf-8')
        if path.exists() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def pack_sky(bsp_path, game_dir, map_name):
    """Embed only the six generated materials used by this BSP, for standalone map delivery."""
    from .bspdecompile import Bsp
    from .stringtables import put_files
    entities = [dict(e) for e in Bsp(Path(bsp_path).read_bytes()).entities()]
    world = next((b for b in entities if b.get('classname') == 'worldspawn'), None)
    sky = world.get('skyname', '') if world else ''
    if not re.fullmatch(r'hammerless/' + re.escape(map_name) + r'/sky_[0-9a-f]{16}', sky):
        return
    files = {}
    for side in SIDES:
        relative = f'materials/skybox/{sky}{side}.vmt'
        files[relative] = (Path(game_dir) / relative).read_bytes()
    put_files(bsp_path, files)
