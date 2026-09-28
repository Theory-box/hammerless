"""MapIR: the plain-Python description of a map.

The Blender layer extracts a scene into these dataclasses; everything after that
(geometry checks, VMF writing, compiling) never touches bpy. All coordinates here
are already in Hammer units.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]


@dataclass
class Polygon:
    """One face of a brush. Vertices are counter-clockwise seen from outside
    (Blender's convention); the VMF writer converts to Hammer's winding."""
    verts: list[Vec3]
    material: str = "tools/toolsnodraw"
    texture_scale: float = 0.25
    lightmap_scale: int = 16


@dataclass
class Brush:
    faces: list[Polygon]
    source: str = ""  # Blender object name, for error messages


@dataclass
class Terrain:
    """A heightfield sampled on a regular grid, turned into displacement patches.

    heights[row][col] is the terrain height at (origin_x + col*spacing,
    origin_y + row*spacing). `None` means no terrain there (ray missed).
    """
    origin: tuple[float, float]
    spacing: float
    heights: list[list[float | None]]
    power: int = 3                 # 2, 3 or 4 -> 5, 9 or 17 verts per patch side
    material: str = "nature/blend_grass_grass_01"
    alphas: list[list[float]] | None = None  # 0..255 blend per sample (optional)
    source: str = ""


@dataclass
class Output:
    """One Hammer I/O connection: when `output` fires, send `input` to `target`."""
    output: str            # e.g. OnTrigger, OnPressed
    target: str            # targetname of the receiving entity, e.g. director
    input: str             # e.g. ForcePanicEvent
    parameter: str = ""
    delay: float = 0.0
    times: int = -1        # -1 = every time, 1 = only once

    def vmf_value(self) -> str:
        delay = f"{self.delay:g}"
        return f"{self.target},{self.input},{self.parameter},{delay},{self.times}"


@dataclass
class Entity:
    classname: str
    origin: Vec3 | None = None
    angles: Vec3 = (0.0, 0.0, 0.0)  # pitch yaw roll, Hammer order
    keyvalues: dict[str, str] = field(default_factory=dict)
    brushes: list[Brush] = field(default_factory=list)  # brush entities
    source: str = ""
    outputs: list[Output] = field(default_factory=list)


@dataclass
class MapSettings:
    name: str = "untitled"
    skyname: str = "sky_day01_09_hdr"
    detail_material: str = "detail/detailsprites_overgrown"
    detail_vbsp: str = "detail.vbsp"
    auto_seal: bool = True
    auto_detail: str = "SMART"        # OFF / SMART (round and small brushes) / ALL
    seal_padding: float = 256.0
    seal_thickness: float = 16.0
    auto_director: bool = True
    auto_light_environment: bool = True

    # sun / sky light (used when the scene has no Blender sun)
    sun_color: tuple[int, int, int] = (255, 245, 225)
    sun_brightness: int = 400
    sun_pitch: float = -50.0          # degrees, negative = pointing down
    sun_yaw: float = 30.0
    ambient_color: tuple[int, int, int] = (140, 160, 190)
    ambient_brightness: int = 80
    lightmap_scale: int = 16          # default for brush faces (lower = sharper, slower)
    wall_climbs: bool = False         # Zombies Climb Walls (nav made in Blender)

    # fog
    fog_enabled: bool = False
    fog_color: tuple[int, int, int] = (110, 120, 130)
    fog_start: float = 512.0
    fog_end: float = 4096.0
    fog_max_density: float = 0.8

    # map-wide Director options (written to a Director script)
    director_enabled: bool = False
    dir_common_limit: int = 30
    dir_mob_min: int = 10
    dir_mob_max: int = 30
    dir_mob_interval_min: int = 90
    dir_mob_interval_max: int = 180
    dir_max_specials: int = 4
    dir_special_interval: int = 45
    dir_tank_limit: int = 1
    dir_witch_limit: int = 1
    dir_no_mobs: bool = False
    dir_no_wanderers: bool = False

    # debug
    debug_log: bool = False
    debug_interval: float = 5.0
    autotest: bool = False


@dataclass
class MapIR:
    settings: MapSettings = field(default_factory=MapSettings)
    brushes: list[Brush] = field(default_factory=list)
    terrains: list[Terrain] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    crescendos: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
