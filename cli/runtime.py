"""Find and materialize the installed headless Docker runtime."""
import os
import shutil
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path


def runtime_dir():
    if os.getenv('LOGCHAT_STACK_DIR'):
        return Path(os.environ['LOGCHAT_STACK_DIR']).expanduser().resolve()
    source = Path(__file__).resolve().parents[1]
    if (source/'compose.yaml').exists():
        return source
    # Installation state is stable across releases, just like the Docker volumes.
    return Path.home()/'.local/share/logchat/runtime'


def prepare_runtime():
    root = runtime_dir()
    if os.getenv('LOGCHAT_STACK_DIR'):
        if not (root/'compose.yaml').exists():
            raise RuntimeError('LOGCHAT_STACK_DIR must contain the logchat Docker stack.')
        return root
    if (Path(__file__).resolve().parents[1]/'compose.yaml').exists():
        return root
    try: release = version('logchat')
    except PackageNotFoundError: release = '0.1.0'
    marker = root/'.runtime-version'
    if not marker.is_file() or marker.read_text()!=release:
        import logchat
        bundled = Path(logchat.__file__).parent/'_stack'
        if not (bundled/'compose.yaml').exists():
            raise RuntimeError('The historical Docker runtime is not bundled in the native release. Use logchat install, or set LOGCHAT_STACK_DIR to an existing historical source checkout.')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copytree(bundled, root, dirs_exist_ok=True)
        marker.write_text(release)
    return root
