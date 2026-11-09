"""Own and reap one test process; Windows job closes descendants on runner exit."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time


class OwnedJob:
    def __init__(self):
        self.handle=None
        if os.name!='nt':return
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),
                      ('flags',wintypes.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),
                      ('active',wintypes.DWORD),('affinity',ctypes.c_size_t),
                      ('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(n,ctypes.c_ulonglong) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),
                      ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];self.api.CreateJobObjectW.restype=wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        self.handle=self.api.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=Extended();limits.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
            error=ctypes.WinError(ctypes.get_last_error());self.close();raise error

    def assign(self,process):
        if self.handle and not self.api.AssignProcessToJobObject(self.handle,int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle);self.handle=None


def run_process(command,cwd,log,timeout):
    if timeout<=0:raise ValueError('Timeout must be positive')
    job=OwnedJob();process=None;started=time.monotonic();timed_out=False
    try:
        startup=None
        if os.name=='nt':
            startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        with open(log,'wb') as stream:
            process=subprocess.Popen(command,cwd=cwd,stdout=stream,stderr=subprocess.STDOUT,
                startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            job.assign(process)
            try:process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:timed_out=True
    finally:
        # Handle/Job ownership, never a process-name search or blanket kill.
        job.close()
        if process is not None:
            if process.poll() is None:process.kill()
            process.wait(timeout=10)
    return {'pid':process.pid,'returncode':process.returncode,'timed_out':timed_out,
            'exited':process.poll() is not None,'elapsed_seconds':time.monotonic()-started}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--blender',required=True)
    parser.add_argument('--script',required=True,type=Path)
    parser.add_argument('--timeout',type=float,default=120)
    parser.add_argument('--log',required=True,type=Path)
    parser.add_argument('args',nargs=argparse.REMAINDER)
    args=parser.parse_args();root=Path(__file__).resolve().parents[2]
    script=args.script.resolve();relative=script.relative_to(root/'tests')
    if relative.parts[0] not in ('blender','compile') or script.suffix!='.py':
        raise ValueError('Expected a Blender test script under tests/blender or tests/compile')
    args.log.parent.mkdir(parents=True,exist_ok=True)
    # OS releases this lock even if the runner crashes. No second test launch.
    with open(root/'tests/blender/.runner.lock','a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result=run_process([args.blender,'--factory-startup','--python-exit-code','1','--python',str(script),*args.args],root,args.log,args.timeout)
        args.log.with_suffix('.process.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result),flush=True)
        if result['timed_out'] or result['returncode']!=0:raise SystemExit(1)

if __name__=='__main__':main()
