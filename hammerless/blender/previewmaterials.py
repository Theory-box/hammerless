"""Read-only VRAD bounce material data; shares compiler/export conversion rules.

Use one resolver for a material snapshot. Discard it when materials/images or
game content change. No pixel reads or disk access belong in the draw callback.
"""
from dataclasses import dataclass
from ..core.mapcompiler import material_row, SURF_NOLIGHT
from ..core.textures import texture_reflectivity


def _source_path(mat, content, game_dir):
    """Resolve model provenance before the generic material-name fallback.

    Older model previews used a reversible HL_mdl_ prefix but stored no path.
    Accept that legacy form only if it round-trips through cache_name and the
    material actually exists. Hashed/truncated names cannot recover a path.
    """
    from .extract import game_path
    if mat is None:
        return ''
    if mat.hammerless.source_material.strip():
        return game_path(mat)
    provenance = str(mat.get('hl_source_material', '')).strip()
    if provenance:
        return provenance.lower().replace('\\', '/').removeprefix('materials/').removesuffix('.vmt')
    if mat.name.startswith('HL_mdl_') and content is not None:
        from .ops import cache_name
        from ..core.gamematerials import read_vmt
        candidate=mat.name[len('HL_mdl_'):]
        if '~' not in candidate and cache_name('HL_mdl_',candidate)==mat.name and read_vmt(content,candidate,game_dir):
            return candidate.lower()
    return game_path(mat)


@dataclass(frozen=True)
class BounceMaterial:
    reflectivity: tuple
    receives_light: bool
    verified: bool
    source: str


class SurfaceMaterials:
    """Reuse assigned Blender images; load game textures only when images are absent."""
    def __init__(self, content, game_dir, default_material):
        self.content, self.game_dir = content, game_dir
        import os
        self.cache_scope = 'l4d2-renderer:' + (os.path.normcase(os.path.abspath(game_dir)) if game_dir else 'content:' + str(id(content)))
        self.default_material = default_material
        self.materials, self.paths = {}, {}
        self.textures = {}
        self.unlit, self.path_unlit = {}, {}
        self.warnings = []
        self.metadata = {}

    def resolve(self, mat):
        from .extract import _base_color_image, texture_size
        key = mat.as_pointer() if mat else 0
        if key in self.materials:
            return self.materials[key]
        path = _source_path(mat, self.content, self.game_dir)
        image = _base_color_image(mat) if mat else None
        if image is not None and not all(image.size):
            image = None
        if image is not None:
            if path:
                self._classify(path)
            self.unlit[key] = self.path_unlit.get(path, False)
            result = (image, texture_size(mat))
        else:
            path = path or self.default_material
            result = self.resolve_path(path)
            self.unlit[key] = self.path_unlit[path]
        self.materials[key] = result
        return result

    def _classify(self, path):
        from ..core.gamematerials import read_vmt
        if path not in self.metadata:
            vmt = read_vmt(self.content,path,self.game_dir) if self.content else {}
            self.metadata[path] = vmt
            self.path_unlit[path] = str(vmt.get('shader','')).startswith('unlitgeneric')
            unsupported=[]
            for parameter,label in (('$alphatest','alpha cutout'),('$translucent','transparency'),('$selfillum','self-illumination')):
                try:enabled=float(vmt.get(parameter,0))!=0
                except (TypeError,ValueError):enabled=False
                if enabled:unsupported.append(label)
            if unsupported:
                self.warnings.append(f"{path}: preview does not yet reproduce {', '.join(unsupported)}")
        return self.metadata[path]

    def resolve_path(self,path):
        """Resolve a preserved VMF face material without creating Blender data."""
        from .preview import _image_for_texture
        from ..core.gamematerials import texture_path
        if path not in self.paths:
            vmt = self._classify(path)

            texture = texture_path(vmt['$basetexture']) if vmt.get('$basetexture') else None
            if texture and texture not in self.textures:
                self.textures[texture] = _image_for_texture(texture, self.content, self.game_dir, cache_scope=self.cache_scope, verify_content=True)
            image, w, h = self.textures[texture] if texture else (None, 0, 0)
            if image is None:
                self.warnings.append(f'{path}: exported base texture unavailable in preview')
            self.paths[path] = (image, (w or 512, h or 512))
        return self.paths[path]


class BounceMaterials:
    def __init__(self, content, game_dir, default_material):
        self.content = content
        self.game_dir = game_dir
        self.default_material = default_material
        self.paths = {}
        self.materials = {}
        self.warnings = []

    def source(self, path):
        path = path.lower().replace('\\','/').removeprefix('materials/').removesuffix('.vmt')
        if path not in self.paths:
            row = material_row(self.content,path,self.game_dir,{}).split('\t')
            found = row[9]=='1'
            if not found:
                self.warnings.append(f'{path}: missing game material; bounce reflectivity unavailable')
            self.paths[path] = BounceMaterial(tuple(float(x) for x in row[5:8]),
                found and not int(row[2]) & SURF_NOLIGHT,found,path)
        return self.paths[path]

    def resolve(self, mat):
        from .extract import game_path,_base_color_image,image_to_rgba8
        if mat is None:return self.source(self.default_material)
        key = mat.as_pointer()
        if key not in self.materials:
            path = _source_path(mat, self.content, self.game_dir)
            if path:
                value = self.source(path)
            else:
                image = _base_color_image(mat)
                if image is None:
                    # Export uses the scene default, not the Blender diffuse color.
                    value = self.source(self.default_material)
                else:
                    value = BounceMaterial(texture_reflectivity(image_to_rgba8(image)),True,True,
                                           'custom texture export: '+image.name)
            self.materials[key] = value
        return self.materials[key]
