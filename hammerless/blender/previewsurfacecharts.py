"""Read-only chart extraction through the existing brush export policy."""
import copy
from types import SimpleNamespace
import bpy
from .extract import MaterialResolver,mesh_to_brushes,game_path,effective_role,texture_size,_base_color_image
from . import vmfimport
from ..core import vmfimport as vi
from ..core.ir import Polygon
from ..core.vmf import VMFWriter
from ..core.surfacecharts import chart_from_side
from ..core.lightmap import NON_RENDERING_MATERIALS

class ChartMaterials(MaterialResolver):
    def __init__(self,*args):
        super().__init__(*args)
        self.bindings={}

    def _resolve_path(self,mat):
        # Chart construction needs density/axes, not generated VMT/VTF files.
        path=game_path(mat)
        if not path and _base_color_image(mat) is not None:
            # Distinct custom materials must not collapse onto the scene default.
            # This identifier exists only in the read-only preview snapshot.
            path=f'hammerless_preview/{mat.as_pointer()}'
        path=path or self.settings.default_material
        self.bindings[path]=mat
        return path

class ChartSnapshot:
    def __init__(self,context):
        self.scene=context.scene
        self.settings=context.scene.hammerless
        self.report=SimpleNamespace(warnings=[])
        self.materials=ChartMaterials(self.settings,None,self.report)
        self.writer=VMFWriter()
        self.document=None;self.originals=set()
        if vmfimport.imported(self.scene):
            self.document=vmfimport._document(bpy.data.texts[self.scene[vmfimport.SOURCE_KEY]],self.settings.lightmap_scale_imported)
            self.originals=vmfimport.written_back(self.scene.objects)
            self.writer._next_id=self.document.max_id()+1

    def object_charts(self,obj,depsgraph,index_start=0):
        if obj.type!='MESH' or effective_role(obj)=='MODEL' or obj.data.get('hl_model'):
            raise ValueError('Surface lightmap charts currently require brush geometry')
        if obj.get(vmfimport.KIND)=='disp':
            raise ValueError('Displacement surface parameterization is not implemented')
        pairs=[]
        if self.document is not None and obj.name in self.originals and obj.get(vmfimport.KIND)=='solid':
            original=self.document.solids.get(obj.get(vmfimport.ID))
            if original is None:raise ValueError('Imported source brush is missing')
            # solid_block may return the original; scale overrides must not mutate
            # shared import state used by the actual build or later snapshots.
            original=copy.deepcopy(original)
            def default_side(vertices,material,tex=None):
                return self.writer.side(Polygon(list(vertices),material or 'tools/toolsnodraw',tex_axes=tex))
            rebuilt=vi.solid_block(original,vmfimport._faces(obj,self.settings.units_per_meter,self.materials,depsgraph),self.writer.new_id,default_side)
            if self.settings.lightmap_scale_imported:
                def scale_of(path):
                    mat=bpy.data.materials.get(path.lower().replace('\\','/'))
                    return (mat.hammerless.lightmap_scale if mat else 0) or self.settings.lightmap_scale
                vi.set_lightmap_scale(rebuilt,scale_of)
            pairs=vi.solid_faces(rebuilt)
        else:
            for brush in mesh_to_brushes(obj,depsgraph,self.settings.units_per_meter,self.materials):
                pairs.extend((self.writer.side(face),face.verts) for face in brush.faces)
        charts=[]
        for side,vertices in pairs:
            path=side.get('material','').lower().replace('\\','/')
            if path in NON_RENDERING_MATERIALS:continue
            charts.append(chart_from_side(side,vertices,index_start+len(charts)))
        return charts

class CachedObjectCharts:
    """Reuse chart layout across camera changes; caller owns geometry revisions.

    source_revision must change when imported VMF text changes. Geometry tokens
    must change after evaluated topology/UV edits, as SceneCache already does.
    """
    def __init__(self):
        self.entries={};self.builds=0

    def get(self,context,obj,depsgraph,geometry_token,source_revision=0):
        s=context.scene.hammerless
        materials=tuple((slot.material.as_pointer(),slot.material.hammerless.lightmap_scale,
                         slot.material.hammerless.texture_scale,game_path(slot.material),texture_size(slot.material))
                        if slot.material else None for slot in obj.material_slots)
        signature=(geometry_token,source_revision,tuple(v for row in obj.matrix_world for v in row),
                   s.units_per_meter,s.lightmap_scale,s.lightmap_scale_imported,s.default_material,
                   materials,obj.name,obj.get(vmfimport.ID),obj.get(vmfimport.KIND),
                   obj.hammerless.use_convex_hull)
        key=obj.as_pointer()
        entry=self.entries.get(key)
        if entry is None or entry[0]!=signature:
            charts=ChartSnapshot(context).object_charts(obj,depsgraph)
            self.entries[key]=(signature,charts);self.builds+=1
        return self.entries[key][1]

    def retain(self,objects):
        keep={obj.as_pointer() for obj in objects}
        self.entries={key:entry for key,entry in self.entries.items() if key in keep}
