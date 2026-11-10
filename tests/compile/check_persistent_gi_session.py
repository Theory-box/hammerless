"""Exercise a bounded live GI session on a controlled single-light BSP fixture.

One owned hidden process, explicit request deadlines, independent normal-bake
references, and no Blender/game or installed-addon changes.
"""
from pathlib import Path
import argparse
import json
import queue
import subprocess
import sys
import threading
import time

from check_persistent_gi import command, digest, edited_light_fixture
from inspect_gi_replay import inspect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'blender'))
from run_isolated import OwnedJob


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('exe', 'reference-exe', 'bsp', 'game', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=90)
    parser.add_argument('--factor', type=float, choices=(0., .5), default=.5)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('Timeout must be positive')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = args.bsp.resolve()
    original = source.read_bytes()
    source_hash = digest(source)
    normal, edited, retained = [out / name for name in ('normal.bsp', 'edited.bsp', 'retained.bsp')]
    normal.write_bytes(original)
    edited.write_bytes(edited_light_fixture(original, args.factor))
    retained.write_bytes(original)
    options = ['-gpu', '-noextra', '-bounce', '0', '-gi', '12', '-girays', '256',
               '-threads', '1', '-game', str(args.game.resolve())]
    for path in (normal, edited):
        command([args.reference_exe.resolve(), *options, path], path.with_suffix('.log'), args.timeout)
    results = {'scope': 'One live retained fixed-geometry session, original/edited/original intensity',
               'edit_factor': args.factor,
               'exe_sha256': digest(args.exe), 'requests': [], 'ok': False}
    process = None
    job = OwnedJob()
    reader = None
    messages = queue.Queue()
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            [str(args.exe.resolve()), *options, '-giserver', '5', str(out / 'live'), str(retained)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        job.assign(process)
        results['pid'] = process.pid

        def read_output():
            try:
                with (out / 'session.log').open('w') as log:
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        messages.put(line.strip())
            finally:
                messages.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()

        def expect(marker):
            deadline = time.monotonic() + args.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(marker)
                try:
                    line = messages.get(timeout=remaining)
                except queue.Empty as error:
                    raise TimeoutError(marker) from error
                if line is None:
                    raise RuntimeError(f'Session ended before {marker}; see session.log')
                if line == marker or line.startswith(marker + ' '):
                    return line

        expect('HLGI_READY')
        results['ready_seconds'] = time.perf_counter() - started
        comparison = out / 'comparison'
        comparison.mkdir()
        for index, (request, reference) in enumerate(
                [('SOLVE', normal), (f'SCALE {args.factor:g}', edited), ('SOLVE', edited), ('SCALE 1', normal)]):
            tick = time.perf_counter()
            process.stdin.write(request + '\n')
            process.stdin.flush()
            marker = expect('HLGI_RESULT')
            elapsed = time.perf_counter() - tick
            if int(marker.split()[1]) != index:
                raise RuntimeError(f'Unexpected response: {marker}')
            checked = inspect(reference, out / f'live.{index}.bin', comparison, limit=3)
            if checked['metrics']['channels'] <= 0 or checked['metrics']['max_abs_linear'] != 0:
                raise RuntimeError(f'Request {request} differs from fresh reference')
            results['requests'].append({'request': request, 'roundtrip_seconds': elapsed,
                                        'response': marker, 'metrics': checked['metrics']})
        process.stdin.write('QUIT\n')
        process.stdin.flush()
        marker = expect('HLGI_DONE')
        if int(marker.split()[1]) != 4:
            raise RuntimeError(f'Unexpected completion: {marker}')
        if process.wait(timeout=10) != 0:
            raise RuntimeError('Worker failed on shutdown')
        results['ok'] = True
    finally:
        job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            results['exited'] = process.poll() is not None
        if reader is not None:
            reader.join(timeout=5)
        results['source_unchanged'] = digest(source) == source_hash
        results['diagnostic_input_unchanged'] = digest(retained) == source_hash
        if not results['source_unchanged'] or not results['diagnostic_input_unchanged']:
            results['ok'] = False
        (out / 'session.json').write_text(json.dumps(results, indent=2) + '\n')
    if not results['ok']:
        raise RuntimeError('Session validation failed')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
