"""Source selection after the shared model profile, reused by installer and CLI."""
from __future__ import annotations
import json
from pathlib import Path
import shlex
import typer
from uuid import uuid4

KINDS = ('later', 'file', 'command', 'docker', 'railway', 'vercel', 'cli')


def load_source_config(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 32768:
        raise ValueError('Source configuration must be a small regular JSON file.')
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or any(k in value for k in ('token', 'password', 'api_key', 'secret')):
        raise ValueError('Source config contains invalid fields. Use --prompt-token or an environment variable for credentials.')
    return value


def connect_provider_source(kind: str, project: Path, directory: Path, port: int,
                            config: dict, *, token: str | None = None, new_source: bool = False) -> dict:
    from .cli import api, attach_project, save_binding, write_credential, delete_credential
    from .client import binding
    from .lifecycle import start
    if kind not in ('docker', 'railway', 'vercel', 'cli'):
        raise ValueError('Choose docker, railway, vercel, or cli.')
    if not (directory/'rag.json').exists() and not (directory/'capture.json').exists():
        raise ValueError('Run logchat install for this state directory before connecting a provider.')
    config = dict(config)
    config.setdefault('cwd', str(project.resolve()))
    start(port, directory)
    previous = binding(project) if (project/'.logchat/local.toml').exists() else None
    attached = attach_project(project, None, None, 'dev', directory, kind=kind)
    if new_source and previous and previous.get('source_id') == attached['source_id']:
        source = api(directory, 'POST', f"/projects/{attached['project_id']}/sources",
                     {'name':f'{kind} source / {str(uuid4())[:8]}', 'environment':attached['environment'], 'kind':kind})
        ref = write_credential(source['token'],purpose='local_ingest',project_id=attached['project_id'],
                               source_id=source['id'],endpoint=attached['api_url'])
        attached = {**attached,'source_id':source['id'],'source_ref':ref}
        try: save_binding(project,attached)
        except Exception:
            delete_credential(ref)
            raise
        delete_credential(previous.get('source_ref'))
    payload = {'kind': kind, 'config': config}
    if token is not None: payload['token'] = token
    return api(directory,'POST',f"/projects/{attached['project_id']}/sources/{attached['source_id']}/provider",payload)


def onboard_source(directory: Path, port: int, *, source: str | None = None,
                   project: Path | None = None, config_path: Path | None = None,
                   interactive: bool = True) -> dict | None:
    if source is None:
        if not interactive: return None
        typer.echo('Choose a log source: file, command, docker, railway, vercel, cli, or later.')
        source = typer.prompt('Log source', default='later').strip().lower()
    if source not in KINDS: raise ValueError('Unknown log source. Choose '+', '.join(KINDS)+'.')
    if source == 'later': return None
    project = (project or Path(typer.prompt('Local project directory', default=str(Path.cwd())) if interactive else '.')).expanduser().resolve()
    if not project.is_dir(): raise ValueError('Select an existing local project directory.')
    config = load_source_config(config_path) if config_path else {}
    if source in ('file', 'command'):
        from .cli import api, attach_project
        from .files import FileCapture
        if source == 'command':
            typer.echo('Launch your app through Logchat to capture stdout and stderr:')
            typer.echo(f'logchat local connect --project {shlex.quote(str(project))} --state-dir {shlex.quote(str(directory))} --logchat-port {port} -- YOUR_APP_COMMAND')
            return {'status':'command_instructions','source':'command'}
        value = config.get('path')
        if not value and interactive: value = typer.prompt('Log file path')
        if not value: raise ValueError('A file source needs a config file with path, or an interactive path selection.')
        path = Path(value).expanduser().absolute()
        with FileCapture.open_file(path): pass
        attached = attach_project(project,None,None,'dev',directory,kind='push')
        from_start = config.get('from_start', False)
        if type(from_start) is not bool: raise ValueError('from_start must be true or false.')
        result = api(directory,'POST',f"/projects/{attached['project_id']}/sources/{attached['source_id']}/file",{'path':str(path),'from_start':from_start})
    else:
        if not config and not interactive: raise ValueError('Provider selection requires --source-config PATH with the adapter fields.')
        prompts = {'docker':[('container','Docker container name or ID')],
                   'railway':[('provider_project','Railway project ID'),('provider_environment','Railway environment'),('service','Railway service')],
                   'vercel':[('provider_project','Vercel project ID'),('provider_environment','Vercel environment (production or preview)')],
                   'cli':[('argv','Log CLI command as a JSON array, including {since} and {until}')]}[source]
        if interactive:
            for key,label in prompts:
                if key not in config:
                    value = typer.prompt(label)
                    config[key] = json.loads(value) if key == 'argv' else value
        token = None
        if interactive and source in ('railway','vercel') and not config.get('token_env') and not config.get('token_ref'):
            typer.echo('Use an existing CLI login, or enter a provider token stored privately on this machine.')
            if typer.confirm('Store a token for this source?',default=False): token = typer.prompt('Provider token', hide_input=True)
        result = connect_provider_source(source,project,directory,port,config,token=token)
    typer.echo(f'{source.capitalize()} source configured. Check logchat local status --project {shlex.quote(str(project))}.')
    typer.echo('Connection configuration is not proof of captured or searchable history; inspect the source status and RAG processing state.')
    return result
