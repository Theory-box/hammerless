"""Export shared pools passed through public action inputs and typed grants."""
import runpy
from pathlib import Path
state=runpy.run_path(str(Path(__file__).with_name('test_candidate_steps.py')))
globals().update({key:value for key,value in state.items() if not key.startswith('__')})
from hammerless.blender.logicgroups import new_item, sync
from hammerless.blender.workbudgets import repair_candidate_sockets

# A saved node from the previous addon version gains sockets without losing
# authored values or event/data links. Repeating migration is harmless.
step.inputs.remove(socket(step,'grant'))
step.outputs.remove(socket(step,'waiting',True))
socket(step,'budget').value=7
before_links=len(group.links)
repair_candidate_sockets();repair_candidate_sockets()
assert socket(step,'budget').value==7 and len(group.links)==before_links
assert sum(s.identifier=='grant' for s in step.inputs)==1
assert sum(s.identifier=='waiting' for s in step.outputs)==1

new_item(group,'Shared Pool','work_budget',False,'pool')
sync(group)
parameter=group.nodes.new('HL_BAParam');parameter.uid='pool'
request=group.nodes.new('HL_RequestWorkGrant')
assert socket(request,'wanted').value==8 and socket(request,'enabled').value
for link in list(group.links):
    if link.to_node==step and link.to_socket.identifier=='run':group.links.remove(link)
wire(group,entry,'start',request,'run');wire(group,entry,'update',request,'run')
wire(group,request,'next',step,'run');wire(group,request,'grant',step,'grant')
wire(group,parameter,'value',request,'pool')
# Reassign to refresh the public action interface after adding its input.
action.group=None;action.group=group
pool=tree.nodes.new('HL_WorkBudgetPool');pool.limit=16
wire(tree,pool,'pool',action,'pool')
assert socket(step,'grant').bl_idname=='HL_WorkGrantSocket'
assert socket(action,'pool').bl_idname=='HL_WorkBudgetSocket'
report=SimpleNamespace(warnings=[]);flat=flatten(bpy.context,tree,report)
assert not report.warnings,report.warnings
ir=MapIR();errors=compile_graph(flat.nodes,flat.links,ir);assert not errors,errors
script='\n'.join(ir.extra_scripts.values())
assert 'HLWB.Request(ctx,' in script and '.grant, true)' in script
assert 'ctx.params["pool"]' in script
print('WORK_BUDGET_BLENDER_OK')
if os.environ.get('SQUIRREL_PYTHONPATH'):
    squirrel.SQVM().execute('compilestring('+json.dumps(script)+');')
    print('WORK_BUDGET_GENERATED_SQUIRREL_OK')
    import re
    start_fn=re.search(r'::(HL_BA_Start_\w+) <-',script).group(1)
    prelude=r'''
    ::clock <- 0.0;
    function Time(){return clock;}
    function printl(s){throw s;}
    function Vector(x,y,z){return {x=x,y=y,z=z};}
    function IsPlayerABot(b){return true;}
    class TestBot {
        function IsValid(){return true;}
        function GetHealth(){return 100;}
        function GetClassname(){return "infected";}
        function GetOrigin(){return Vector(0,0,0);}
    }
    ::testbots <- [];
    for(local i=0;i<10;++i)testbots.append(TestBot());
    ::Entities <- {FindByClassname=function(previous,name){
        if(name!="infected")return null;
        local i=previous==null?0:testbots.find(previous)+1;
        return i<testbots.len()?testbots[i]:null;
    }};
    '''
    cases=r'''
    local previous=0;
    for(local cycle=0;cycle<20;++cycle) {
        clock=cycle*0.2;HLBA.TickAll(clock);
        local processed=0;
        foreach(loop in HLBA.loops) foreach(a in loop.active)
            foreach(job in a.ctx._candidateJobs)processed+=job.processed;
        if(processed-previous>16)throw "Generated graph exceeded its shared pool";
        if(cycle==0 && processed!=0)throw "First request skipped queue latency";
        previous=processed;
    }
    if(previous!=100)throw "Generated fair graph failed to complete every bot scan";
    HLBA.StopAll();
    if(HLWB.claims.len()!=0)throw "Generated graph retained claims after stop";
    '''
    squirrel.SQVM().execute(prelude+script+'\n'+start_fn+'();\n'+cases)
    print('WORK_BUDGET_GENERATED_EXECUTION_OK')
