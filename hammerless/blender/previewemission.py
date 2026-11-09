"""Cached Blender material emission using the bake exporter's sampling rules."""
from types import SimpleNamespace
import bpy
from .emission import MaterialEmission, UnsupportedEmission, mesh_lights
from .previewscene import included_instance

class EmissionCache:
    def __init__(self):
        self.materials = {}
        self.samples = {}
        self.builds = 0
        self.revision = None

    def update(self, scene_cache, depsgraph, revision):
        changed = revision != self.revision or any(
            isinstance(u.id, (bpy.types.Material, bpy.types.Image, bpy.types.ShaderNodeTree))
            for u in depsgraph.updates)
        if changed:
            self.materials.clear(); self.samples.clear(); self.revision = revision
        warnings = []; groups = []; used = set()
        scale = depsgraph.scene.hammerless.units_per_meter
        for instance in depsgraph.object_instances:
            obj = instance.object
            if obj.type != 'MESH' or not instance.show_self or not included_instance(instance, depsgraph.view_layer):
                continue
            geometry = scene_cache.geometry.get(obj.original.as_pointer())
            if geometry is None:
                continue
            mats = tuple(slot.material for slot in obj.material_slots)
            active = False
            for mat in mats:
                if mat is None:
                    continue
                key = (mat.as_pointer(), obj.hammerless.role == 'MODEL')
                if key not in self.materials:
                    try:
                        self.materials[key] = MaterialEmission(mat)
                    except UnsupportedEmission as exc:
                        self.materials[key] = None
                        warnings.append(f'{mat.name}: preview emission unavailable: {exc}')
                emitter = self.materials[key]
                active |= emitter is not None and emitter.enabled
            if not active:
                continue
            key = (obj.original.as_pointer(), id(geometry), scale,
                   tuple(v for row in instance.matrix_world for v in row),
                   tuple(m.as_pointer() if m else 0 for m in mats))
            used.add(key)
            if key not in self.samples:
                report = SimpleNamespace(warnings=[])
                try:
                    entities = mesh_lights(obj, depsgraph, scale, report, self.materials,
                                           matrix=instance.matrix_world)
                except (ValueError, ZeroDivisionError) as exc:
                    entities = []
                    report.warnings.append(f'{obj.name}: preview emission unavailable: {exc}')
                self.samples[key] = (entities, report.warnings)
                self.builds += 1
            entities, messages = self.samples[key]
            warnings.extend(messages)
            if entities:
                groups.append(entities)
        self.samples = {k:v for k,v in self.samples.items() if k in used}
        return groups, warnings
