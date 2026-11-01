import os,sys
from pathlib import Path
from types import SimpleNamespace
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.routes import follow_route, PARAMETERS
from hammerless.blender.logicgroups import flatten
from hammerless.blender.actiontemplates import _Builder
from hammerless.core.logic import compile_graph
from hammerless.core.ir import MapIR
scene=bpy.context.scene
scene.hammerless.units_per_meter=16
col=bpy.data.collections.new('TestRoute');scene.collection.children.link(col)
parent=bpy.data.objects.new('Parent',None);scene.collection.objects.link(parent);parent.location=(10,0,0)
for i in (2,0,1):
    obj=bpy.data.objects.new('Point'+str(i),None);col.objects.link(obj);obj.parent=parent;obj.location=(i,0,0);obj['hl_route_order']=i
bpy.context.view_layer.update()
tree=bpy.data.node_groups.new('Route Test','HL_LogicTree');tree.scene_name=scene.name
start,find,loop,apply,action,route=[tree.nodes.new(k) for k in ('HL_NodeMapStart','HL_BAFind','HL_BALoop','HL_BAApply','HL_BAAction','HL_RouteData')]
find.selection='COMMONS';route.collection=col;action.group=follow_route('ONCE')
for key,label,kind,default in PARAMETERS:
    if default is not None:_Builder.socket(action,key).value=default
for a,out,b,inp in ((start,'On Map Start',loop,'Start'),(find,'Bots',loop,'Bots'),(loop,'Each Bot',apply,'Run'),(loop,'Current Bot',apply,'Bot'),(action,'Action',apply,'Action'),(route,'Route',action,'Route')):tree.links.new(a.outputs[out],b.inputs[inp])
ln=route.to_lnode(bpy.context);assert ln.settings['points']==[(160,0,0),(176,0,0),(192,0,0)],ln.settings
assert not any(getattr(n,'kind','') in ('SCRIPT_CODE','SCRIPT_VALUE') for n in action.group.nodes)
report=SimpleNamespace(warnings=[]);flat=flatten(bpy.context,tree,report);assert not report.warnings,report.warnings
ir=MapIR();errors=compile_graph(flat.nodes,flat.links,ir);assert not errors,errors
script='\n'.join(ir.extra_scripts.values())
assert 'HLRoute.Advance' in script and 'route_index' in script
(Path(__file__).resolve().parents[4]/'outputs'/'routes-generated.nut').write_text(script)
col.objects['Point2']['hl_route_order']=1
assert 'distinct' in route.to_lnode(bpy.context).settings['error']
# Exercise the authoring operator, including an unrelated collection guard.
col.objects['Point2']['hl_route_order']=2
area=bpy.context.screen.areas[0];area.type='NODE_EDITOR';area.ui_type='HL_LogicTree'
area.spaces.active.node_tree=tree;area.spaces.active.pin=True
with bpy.context.temp_override(area=area):
    scene.cursor.location=(5,6,7)
    assert bpy.ops.hammerless.route_point(node_name=route.name)=={'FINISHED'}
    added=bpy.context.view_layer.objects.active
    assert added['hl_route_order']==3 and tuple(added.location)==(5,6,7)
    other=bpy.data.collections.new('NotInScene');route.collection=other
    assert bpy.ops.hammerless.route_point(node_name=route.name)=={'CANCELLED'}
    assert not other.objects
route.collection=col
print('ROUTE_BLENDER_OK')
if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH']);import squirrel
    prelude=Path(__file__).with_name('routes_prelude.nut').read_text()
    cases=Path(__file__).with_name('routes_generated_cases.nut').read_text()
    squirrel.SQVM().execute(prelude+script+cases)
    print('ROUTE_GENERATED_SQUIRREL_OK')
