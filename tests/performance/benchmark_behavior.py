"""Offline actual-runtime microbenchmark. Not a game frame-time estimate."""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
if os.environ.get('SQUIRREL_PYTHONPATH'):
    sys.path.insert(0,os.environ['SQUIRREL_PYTHONPATH'])
from hammerless.core import candidates
from hammerless.core.logic import LNode as N, LLink as L, _Compiler
from hammerless.core.ir import MapIR


def generated_rule():
    """Compile the same arithmetic as the handwritten reference, through real nodes."""
    node=N('benchmark','CAND_RULE',{'definition':{
        'list_type':'bots',
        'nodes':[N('keep','COMPARE',consts={'a':1,'b':0}),
                 N('index','CAND_PARAM',{'uid':'index'}),
                 N('bias','CAND_PARAM',{'uid':'bias'}),
                 N('multiply','MATH',{'op':'MULTIPLY'},consts={'b':0.5}),
                 N('add','MATH',{'op':'ADD'})],
        'links':[L('index','value','multiply','a',True),
                 L('multiply','value','add','a',True),L('bias','value','add','b',True)],
        'inputs':[('candidate','bot'),('position','vec'),('index','num'),('bias','num')],
        'outputs':{'keep':('keep','result'),'score':('add','value')}}})
    compiler=_Compiler(MapIR(),[])
    name,_=candidates.compile_rule(compiler,node,'bots')
    if compiler.problems: raise RuntimeError(compiler.problems)
    return '\n'.join(compiler.functions)+'\n::BenchGeneratedRule <- '+name+';'


def positive(value):
    number=int(value)
    if not 1<=number<=100: raise argparse.ArgumentTypeError('must be between 1 and 100')
    return number


def run(args):
    try:
        import squirrel
        if not hasattr(squirrel,'SQVM'): raise ImportError('SQVM missing')
    except ImportError as error:
        raise SystemExit('Real squirrel-lang SQVM is required. Set SQUIRREL_PYTHONPATH to the existing interpreter package directory. '+str(error))
    paths=[ROOT/'hammerless/core'/name for name in
           ('list_tools_runtime.nut','candidate_runtime.nut','bot_actions_runtime.nut')]
    sources=[p.read_text(encoding='utf-8') for p in paths]
    harness=Path(__file__).with_name('behavior_workloads.nut').read_text(encoding='utf-8')
    compiled=generated_rule()
    vm=squirrel.SQVM()
    vm.execute('\n'.join(sources)+'\n'+compiled+'\n'+harness)
    try: version=importlib.metadata.version('squirrel-lang')
    except importlib.metadata.PackageNotFoundError: version='unknown'
    result={'schema':1,'timestamp_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'machine':{'platform':platform.platform(),'processor':platform.processor(),
                       'processor_identifier':os.environ.get('PROCESSOR_IDENTIFIER',''),
                       'logical_cpus':os.cpu_count(),'python':sys.version,'squirrel_lang':version},
            'config':{'repetitions':args.repetitions,'warmups':args.warmups,'updates_per_batch':args.updates},
            'measurement':'perf_counter_ns around SQVM.execute of a short function-call string; includes boundary, short-string parsing, validation and mock entity methods; excludes VM, compiler and workload setup',
            'limitations':['Offline mock entities; no native engine/nav/ambient/raycast costs.',
                           'Batch p95 is not single-tick p95 or in-game frame time.',
                           'Five-node arithmetic rule; not representative of every user graph.',
                           'Action fresh case includes ReleaseLoop harness cleanup; no overrides are present.'],
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(s.encode()).hexdigest() for p,s in zip(paths,sources)},
            'generated_rule_sha256':hashlib.sha256(compiled.encode()).hexdigest(),
            'harness_sha256':hashlib.sha256(harness.encode()).hexdigest(),
            'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'cases':[]}

    def measure(case,setup,call,expected):
        samples=[]
        for repeat in range(args.warmups+args.repetitions):
            vm.execute(setup)  # setup and retained prefill deliberately untimed
            start=time.perf_counter_ns()
            checksum=vm.execute(call)
            duration=(time.perf_counter_ns()-start)/1e6
            if checksum!=expected: raise RuntimeError(f'{case}: checksum {checksum} != {expected}')
            if repeat>=args.warmups: samples.append(duration)
        ordered=sorted(samples)
        case.update(samples_ms=samples,median_batch_ms=statistics.median(samples),
                    p95_batch_ms=ordered[math.ceil(len(samples)*0.95)-1],max_batch_ms=max(samples),
                    mean_ms_per_update=statistics.mean(samples)/args.updates,checksum=expected)
        result['cases'].append(case)
        print(f"{case['kind']:10} bots={case['bots']:3} {case['variant']:10} median batch={case['median_batch_ms']:.3f}ms",flush=True)

    started=time.perf_counter()
    for bots in (30,60,100):
        for count in (8,32,128,256):
            expected=args.updates*(bots*(count-1)*0.5+bots*(bots-1)*0.5)
            for variant in ('handwritten','generated'):
                measure({'kind':'candidate','bots':bots,'candidates':count,'variant':variant,
                         'candidate_evaluations_per_batch':args.updates*bots*count,
                         'returned_rows_per_batch':args.updates*bots*min(count,128)},
                        f'Bench.SetupCandidates({bots},{count});',
                        f'return Bench.Candidates({args.updates},{str(variant=="generated").lower()});',expected)
        for loops in (1,4):
            for variant in ('retained','fresh'):
                setup=f'Bench.SetupActions({bots},{loops},{str(variant=="retained").lower()});'
                vm.execute(setup)
                pairs=vm.execute('return Bench.CountActionPairs();')  # instrumentation never timed
                measure({'kind':'actions','bots':bots,'loops':loops,'variant':variant,
                         'callbacks_per_batch':args.updates*bots,'desired_pair_comparisons_per_update':pairs},
                        setup,f'return Bench.Actions({args.updates},{str(variant=="fresh").lower()});',args.updates*bots)
    result['elapsed_seconds']=time.perf_counter()-started
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print('JSON:',args.output.resolve())
    print(f"Completed {len(result['cases'])} cases in {result['elapsed_seconds']:.2f}s")
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,help='optional JSON result destination')
    parser.add_argument('--repetitions',type=positive,default=21)
    parser.add_argument('--warmups',type=positive,default=3)
    parser.add_argument('--updates',type=positive,default=2)
    run(parser.parse_args())
