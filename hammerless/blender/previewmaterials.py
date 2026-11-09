"""Read-only VRAD bounce material data; shares compiler/export conversion rules.

Use one resolver for a material snapshot. Discard it when materials/images or
game content change. No pixel reads or disk access belong in the draw callback.
"""
from dataclasses import dataclass
from ..core.mapcompiler import material_row, SURF_NOLIGHT
from ..core.textures import texture_reflectivity


@dataclass(frozen=True)
class BounceMaterial:
    reflectivity: tuple
    receives_light: bool
    verified: bool
    source: str


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
            path = game_path(mat)
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
