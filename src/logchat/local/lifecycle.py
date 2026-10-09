"""Start/stop only the local sidecar managed by this installation."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import psutil

DEFAULT_PORT=8765


def state_directory(value=None):
    directory=Path(value or os.getenv('LOGCHAT_LOCAL_DIR') or Path.home()/'.local/share/logchat/local').expanduser().resolve()
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    directory.chmod(0o700)
    return directory


def write_private(path, value):
    temporary=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    fd=os.open(temporary,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'w') as output:
            json.dump(value,output)
        temporary.chmod(0o600)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(directory):
    with (directory/'manager.lock').open('a+b') as handle:
        os.chmod(handle.name,0o600)
        if os.name=='nt':
            import msvcrt
            handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
            msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX)
        try: yield
        finally:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(handle,fcntl.LOCK_UN)


def managed_process(directory):
    try:
        metadata=json.loads((directory/'manager.json').read_text())
        process=psutil.Process(metadata['pid'])
        args=process.cmdline()
        if abs(process.create_time()-metadata['create_time'])>0.01 or 'logchat.local.server' not in args or str(directory) not in args:
            return None,None
        return process,metadata
    except (OSError,ValueError,KeyError,psutil.Error): return None,None


def local_url(directory):
    process,metadata=managed_process(directory)
    if not process: raise RuntimeError('The local sidecar is stopped. Run logchat start.')
    return 'http://127.0.0.1:'+str(metadata['port'])


def control_token(directory):
    path=directory/'control-token'
    if path.is_symlink() or path.stat().st_mode&0o077:
        raise RuntimeError('Local credential permissions are invalid.')
    token=path.read_text().strip()
    if not token: raise RuntimeError('Local credential is unavailable.')
    return token


def ensure_fresh_rag(directory):
    """Every supported fresh native entrypoint uses the semantic core."""
    from .raw_capture import load_capture_policy
    if load_capture_policy(directory)["mode"] == "retain_until_summarized":
        return
    if not (directory/'rag.json').exists() and not (directory/'local.db').exists():
        raise RuntimeError('Choose your installed models with logchat install before starting a new instance. No model is selected or downloaded automatically.')


def start(port=DEFAULT_PORT, state_dir=None):
    if not 1<=port<=65535: raise RuntimeError('Select a port between 1 and 65535.')
    directory=state_directory(state_dir)
    with locked(directory):
        existing,metadata=managed_process(directory)
        if existing:
            if metadata['port']!=port: raise RuntimeError(f"Logchat is already running on port {metadata['port']}. Stop it before changing the port.")
            return 'http://127.0.0.1:'+str(port)
        url='http://127.0.0.1:'+str(port)
        # Never reuse a different application's listener based solely on its health response.
        import socket
        with socket.socket() as probe:
            if os.name!='nt':probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try: probe.bind(('127.0.0.1',port))
            except OSError: raise RuntimeError(f'Port {port} is occupied. Choose --port with another port.') from None
        ensure_fresh_rag(directory)
        environment=dict(os.environ)
        environment['LOGCHAT_LOCAL_DIR']=str(directory)
        environment['LOGCHAT_LOCAL_PORT']=str(port)
        command=[sys.executable,'-m','logchat.local.server','--state-dir',str(directory),'--port',str(port)]
        kwargs={'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.DETACHED_PROCESS}
        # Resolve the daemon from the same distribution as this CLI. An app's
        # working directory may contain an older checkout or a package named
        # logchat, which must not silently replace the installed runtime.
        child=subprocess.Popen(command,cwd=Path(__file__).resolve().parents[2],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=environment,**kwargs)
        try:
            creation=psutil.Process(child.pid).create_time()
            # Cold package/extension imports can exceed ten seconds on a busy
            # development machine. Bound readiness by wall time, not poll count.
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                if child.poll() is not None: raise RuntimeError('Local startup failed. Run logchat local serve to see a safe startup error.')
                try:
                    token=control_token(directory)
                    with httpx.Client(trust_env=False,timeout=1) as client:
                        health=client.get(url+'/health/ready');projects=client.get(url+'/projects',headers={'Authorization':'Bearer '+token})
                    if health.status_code==200 and health.json().get('mode')=='local' and projects.status_code==200:
                        write_private(directory/'manager.json',{'pid':child.pid,'create_time':creation,'port':port})
                        return url
                except (OSError,RuntimeError,httpx.HTTPError,ValueError): pass
                time.sleep(.1)
            raise RuntimeError('Local startup did not become ready. Run logchat local serve for diagnostics.')
        except Exception:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
            raise


def stop(state_dir=None):
    directory=state_directory(state_dir)
    with locked(directory):
        process,_=managed_process(directory)
        if not process:
            (directory/'manager.json').unlink(missing_ok=True)
            return False
        process.terminate()
        try:process.wait(timeout=8)
        except psutil.TimeoutExpired:
            raise RuntimeError('The owned Logchat process has not stopped; no other process was signaled.') from None
        (directory/'manager.json').unlink(missing_ok=True)
        return True
