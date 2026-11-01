"""Run test_behaviortemplates.py's compiled functions in the real Squirrel VM.

Usage: python run_temporal_vm.py path/to/temporal-functions.json
SQUIRREL_PYTHONPATH can point at the optional squirrel-lang installation.
"""
import os, sys, json
from pathlib import Path
if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0, os.environ['SQUIRREL_PYTHONPATH'])
import squirrel
data=json.loads(Path(sys.argv[1]).read_text())
vm=squirrel.SQVM()
for kind, spec in data.items():
    vm.execute('::step <- function(ctx) {'+spec['body']+'};')
    common='''
    local a={state={},params={reset=false,delta=0.5,condition=true,duration=1.0,rate=2.0,decay=0.5,maximum=2.0,target=3.0,speed=2.0}};
    local b={state={},params=clone a.params};
    local key="Group/value"; local other="Group.001/value";
    function near(a,b) { if(fabs(a-b)>0.0001) throw "Incorrect value "+a+" expected "+b; }
    '''
    cases={
      'SUSTAINED': '''
        step(a); near(a.state[key],0.5); assert(!a.state.observed0);
        step(a); near(a.state[key],1.0); assert(a.state.observed0);
        a.params.condition=false; step(a); near(a.state[key],0); assert(!a.state.observed0);
        a.params.condition=true; a.params.duration=0; step(a); assert(a.state.observed0);
        a.params.reset=true; step(a); near(a.state[key],0); assert(!a.state.observed0);
      ''',
      'ACCUMULATE': '''
        step(a); near(a.state[key],0.75); near(a.state.observed0,0.75);
        step(a); step(a); near(a.state[key],2);
        a.params.rate=0; a.params.delta=10; step(a); near(a.state[key],0);
        a.params.rate=3; a.params.decay=-5; a.params.delta=0.5; step(a); near(a.state[key],1.5);
      ''',
      'RATE': '''
        step(a); near(a.state[key],1); near(a.state.observed0,1);
        step(a); step(a); step(a); near(a.state[key],3);
        a.params.target=-4; step(a); near(a.state[key],2);
        a.params.speed=-1; step(a); near(a.state[key],2);
      '''}
    vm.execute(common+cases[kind]+'''
      assert(!(key in b.state));
      local beforeOther=a.state[key]; step(b); near(a.state[key],beforeOther);
      near(b.state[key],b.state[other]);
      a.params.reset=false; a.params.delta=-1;
      local previous=a.state[key]; step(a); near(a.state[key],previous);
      a.params.reset=true; step(a); near(a.state[key],0); near(a.state[other],0);
      b.state[key] <- 99; assert(a.state[key]==0);
    ''')
print('TEMPORAL_VM_OK')
