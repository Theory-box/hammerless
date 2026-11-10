"""Run isolated persistent-GI diagnostic and a normal bake on disposable copies.

No Blender/game launch. The output directory must not already exist, preventing
stale artifacts from being mistaken for current results. This checks retained
GI state, not light-edit performance or viewport responsiveness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def edited_light_fixture(data, factor):
    """Brightness-only edit for the controlled one-light fixture, not a BSP editor."""
    from inspect_gi_replay import override_lump, _lumps
    entities = _lumps(data)(0).rstrip(b'\0').decode('ascii')
    candidates = []
    for block in re.finditer(r'\{[^{}]*\}', entities):
        keys = dict(re.findall(r'"([^"\\]*)"\s*"([^"\\]*)"', block.group()))
        if keys.get('classname') in ('light', 'light_spot'):
            candidates.append((block, keys))
    if len(candidates) != 1:
        raise ValueError('Relight fixture must contain exactly one point or spot light')
    block, keys = candidates[0]
    if '_lightHDR' in keys or keys.get('style', '0') != '0':
        raise ValueError('Relight fixture requires default style and no HDR intensity override')
    values = [float(value) for value in keys['_light'].split()]
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        raise ValueError('Relight fixture needs a finite four-component _light')
    values[3] *= factor
    value = ' '.join(format(component, '.9g') for component in values)
    changed, count = re.subn(r'("_light"\s*")[^"]*(")',
                            lambda match: match[1] + value + match[2], block.group())
    if count != 1:
        raise ValueError('Ambiguous fixture _light key')
    entities = entities[:block.start()] + changed + entities[block.end():]
    return override_lump(data, 0, entities.encode('ascii') + b'\0')


def command(args, log, timeout):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'blender'))
    from run_isolated import OwnedJob
    started = time.perf_counter()
    job = OwnedJob()
    try:
        with log.open('wb') as stream:
            with subprocess.Popen(
                    [str(a) for a in args], stdout=stream, stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as completed:
                try:
                    job.assign(completed)
                    completed.wait(timeout=timeout)
                except BaseException:
                    job.close()
                    if completed.poll() is None:
                        completed.kill()
                    completed.wait(timeout=10)
                    raise
    finally:
        job.close()
    text = log.read_text(errors='replace')
    if completed.returncode:
        raise RuntimeError(f'Compiler failed ({completed.returncode}); see {log}')
    if 'Lighting on the GPU:' not in text:
        raise RuntimeError(f'GPU execution not confirmed; see {log}')
    return {'wall_seconds': time.perf_counter() - started, 'log': str(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--reference-exe', type=Path,
                        help='Optional unchanged compiler for the normal-bake reference')
    parser.add_argument('--bsp', type=Path, required=True)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rays', type=int, default=64)
    parser.add_argument('--passes', type=int, default=3)
    parser.add_argument('--repeats', type=int, default=3, choices=range(2, 9))
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--modeldir', type=Path)
    parser.add_argument('--static-prop-polys', action='store_true')
    parser.add_argument('--threads', type=int, default=1, choices=range(1, 17))
    parser.add_argument('--relight-factor', type=float,
                        help='Controlled fixture: change its single point/spot brightness after replay 0')
    args = parser.parse_args()
    if not args.exe.is_file() or not args.bsp.is_file() or not args.game.is_dir():
        parser.error('Executable, source BSP, and game directory must exist')
    if args.reference_exe and not args.reference_exe.is_file():
        parser.error('Reference executable must exist')
    if min(args.rays, args.passes, args.timeout) < 1:
        parser.error('Rays, passes, and timeout must be positive')
    if args.relight_factor is not None and (not math.isfinite(args.relight_factor) or not 0 < args.relight_factor <= 16):
        parser.error('Relight factor must be finite and in (0, 16]')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = args.bsp.resolve()
    original_hash = digest(source)
    reference, retained = out / 'reference.bsp', out / 'retained.bsp'
    shutil.copy2(source, reference)
    shutil.copy2(source, retained)
    common = [args.exe.resolve(), '-gpu', '-noextra', '-bounce', '0',
              '-gi', args.passes, '-girays', args.rays, '-threads', args.threads,
              '-timing', '-game', args.game.resolve()]
    if args.modeldir:
        common += ['-modeldir', args.modeldir.resolve()]
    if args.static_prop_polys:
        common += ['-StaticPropPolys']
    result = {'source': str(source), 'source_sha256': original_hash,
              'exe_sha256': digest(args.exe), 'rays': args.rays,
              'reference_exe_sha256': digest(args.reference_exe or args.exe),
              'passes': args.passes, 'repeats': args.repeats,
              'scope': ('Persistent GI with one real direct-light intensity update.' if args.relight_factor is not None
                        else 'Persistent GI replay; direct lighting calculated once per process.')}
    try:
        baseline = [args.reference_exe.resolve() if args.reference_exe else common[0]] + common[1:]
        result['normal_bake'] = command(baseline + [reference], out / 'normal.log', args.timeout)
        edited = None
        options = []
        if args.relight_factor is not None:
            edited = out / 'edited-reference.bsp'
            edited.write_bytes(edited_light_fixture(source.read_bytes(), args.relight_factor))
            result['edited_normal_bake'] = command(baseline + [edited], out / 'edited-normal.log', args.timeout)
            result['relight_factor'] = args.relight_factor
            options = ['-girelight', args.relight_factor]
        result['persistent_gi'] = command(
            common + ['-girepeat', args.repeats, out / 'replay'] + options + [retained],
            out / 'persistent.log', args.timeout)
        events = [json.loads(line) for line in (out / 'replay.jsonl').read_text().splitlines()]
        replays = [event for event in events if event.get('kind') == 'replay']
        if [event.get('repeat') for event in replays] != list(range(args.repeats)):
            raise RuntimeError('Missing or unexpected GI replay records')
        if not all(event.get('passes', 0) > 0 for event in replays):
            raise RuntimeError('GI replay did not execute any passes')
        if edited is None and not all(event.get('equal_first') is True for event in replays):
            raise RuntimeError('GI replays did not produce identical completed solves')
        if edited is not None and args.relight_factor != 1:
            if digest(out / 'replay.0.bin') == digest(out / 'replay.1.bin'):
                raise RuntimeError('Light edit did not change the resulting lightmaps')
        from inspect_gi_replay import inspect
        comparison = out / 'comparison'
        comparison.mkdir()
        result['comparisons'] = []
        for i in range(args.repeats):
            checked = inspect(edited if edited and i > 0 else reference,
                              out / f'replay.{i}.bin', comparison, limit=3)
            stats = checked['metrics']
            result['comparisons'].append(stats)
            if stats['channels'] <= 0 or stats['max_abs_linear'] != 0:
                raise RuntimeError(f'Replay {i} differs from normal-bake reference; see comparison artifacts')
        result['validated'] = True
    finally:
        result['source_unchanged'] = digest(source) == original_hash
        result['diagnostic_input_unchanged'] = digest(retained) == original_hash
        if not result['source_unchanged'] or not result['diagnostic_input_unchanged']:
            result['validated'] = False
        (out / 'run.json').write_text(json.dumps(result, indent=2) + '\n')
    if not result['source_unchanged'] or not result['diagnostic_input_unchanged']:
        raise RuntimeError('Input BSP changed unexpectedly')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
