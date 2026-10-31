import sys,pathlib,math
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
import bpy,numpy as np,hammerless
from mathutils import Matrix
hammerless.register()
from hammerless.blender.emission import area_lights,mesh_lights,MaterialEmission
from hammerless.core.build import Report
from hammerless.blender.extract import extract_scene
for o in list(bpy.data.objects):bpy.data.objects.remove(o,do_unlink=True)
scale=52.49
lamp=bpy.data.lights.new('Panel','AREA');lamp.energy=100;lamp.shape='RECTANGLE';lamp.size=2;lamp.size_y=1
obj=bpy.data.objects.new('Panel',lamp);bpy.context.collection.objects.link(obj);obj.location.z=3
bpy.context.view_layer.update()
a=area_lights(obj,scale)
assert len(a)>4 and len({e.origin for e in a})==len(a)
assert all(abs(e.angles[0]+90)<.001 for e in a)
expected=100*scale**2/(100*math.pi)
assert abs(sum(float(e.keyvalues['_light'].split()[3]) for e in a)-expected)<1e-4
obj.rotation_euler.y=math.pi/2;bpy.context.view_layer.update()
b=area_lights(obj,scale)
assert all(abs(abs(e.angles[1])-180)<.001 for e in b),b[0].angles
for shape in ('SQUARE','RECTANGLE','DISK','ELLIPSE'):
 lamp.shape=shape
 assert area_lights(obj,scale)
lamp.energy=0
assert not area_lights(obj,scale)
# An active Principled emission, not a disconnected red emission node.
mat=bpy.data.materials.new('Emitter');mat.use_nodes=True
bsdf=mat.node_tree.nodes.get('Principled BSDF');bsdf.inputs['Emission Color'].default_value=(.25,1,.5,1);bsdf.inputs['Emission Strength'].default_value=2
stray=mat.node_tree.nodes.new('ShaderNodeEmission');stray.inputs['Strength'].default_value=100
em=MaterialEmission(mat)
assert np.allclose(em((.5,.5)),(.5,2,1))
bpy.ops.mesh.primitive_plane_add(size=2)
mesh_obj=bpy.context.object;mesh_obj.data.materials.append(mat)
bpy.context.view_layer.update()
r=Report();lights=mesh_lights(mesh_obj,bpy.context.evaluated_depsgraph_get(),scale,r,{})
assert lights and not r.errors
assert all(e.origin[2]>.09 and abs(e.angles[0]-90)<.001 for e in lights)
# Radiance power scales with emitter area, independent of tessellation.
def power(es):return sum(float(e.keyvalues['_light'].split()[3]) for e in es)
assert abs(power(lights)-2*4*scale**2/100)<.001
bsdf.inputs['Emission Strength'].default_value=0
assert not mesh_lights(mesh_obj,bpy.context.evaluated_depsgraph_get(),scale,r,{})
# Image emission samples linear color through UVs.
bsdf.inputs['Emission Strength'].default_value=1
image=bpy.data.images.new('Emission texture',width=2,height=1,float_buffer=True)
image.pixels[:]=[1,0,0,1,0,0,1,1]
tex=mat.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image;tex.interpolation='Closest'
mat.node_tree.links.new(tex.outputs['Color'],bsdf.inputs['Emission Color'])
em=MaterialEmission(mat)
assert np.allclose(em((.25,.5)),(1,0,0))
assert np.allclose(em((.75,.5)),(0,0,1))
# Full scene extraction includes samples and doesn't leak bpy handles to worker.
ir,_=extract_scene(bpy.context,Report(),defer_textures=True)
assert any(e.source==mesh_obj.name and e.classname=='light_spot' for e in ir.entities)
assert '_emission_cache' not in ir.__dict__
print('SURFACE LIGHTS PASS',len(lights))
# Add-menu factory, modifiers, mirror and sample-budget regression.
from hammerless.blender.lamps import add_lamp
added=add_lamp(bpy.context,'AREA');assert added.type=='LIGHT' and added.data.type=='AREA'
assert area_lights(added,scale)
# Return to a constant emitter for conservation checks.
mat.node_tree.links.remove(bsdf.inputs['Emission Color'].links[0])
bsdf.inputs['Emission Color'].default_value=(1,1,1,1)
mesh_obj.scale=(-1,1,1);bpy.context.view_layer.update()
es=mesh_lights(mesh_obj,bpy.context.evaluated_depsgraph_get(),scale,Report(),{})
assert es and all(e.angles[0]>89 for e in es)
mod=mesh_obj.modifiers.new('Refine emitter','SUBSURF');mod.subdivision_type='SIMPLE';mod.levels=4
bpy.context.view_layer.update()
sub=mesh_lights(mesh_obj,bpy.context.evaluated_depsgraph_get(),scale,Report(),{})
assert abs(power(sub)-power(es))<.001,(power(sub),power(es))
mesh_obj.scale=(100,100,1);bpy.context.view_layer.update()
huge=mesh_lights(mesh_obj,bpy.context.evaluated_depsgraph_get(),scale,Report(),{})
assert 0<len(huge)<=2048
# Live mixing must not allocate hundreds of layers for one surface emitter.
from hammerless.core.build import tag_light_layers
assert tag_light_layers(huge)==0
print('SURFACE LIGHT EDGE CASES PASS')
