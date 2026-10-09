"""No-Docker sidecar CLI, explicit app attachment, and project-bound agent setup."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse
import subprocess
import sys
import threading
import signal
from dataclasses import asdict
import time
import tomllib
from uuid import uuid4

import httpx
import typer

from cli.secrets import write_credential,read_credential,delete_credential,secret_dir
from logchat.client import _local_api_url
from logchat.local.client import binding,LocalLogchatClient,LocalEmitter
from logchat.local.lifecycle import DEFAULT_PORT,control_token,local_url,managed_process,start,state_directory,stop

app=typer.Typer(help='Minimal local Logchat: SQLite, one loopback port, no Docker.',no_args_is_help=True)

WRAPPED_DRAIN_SECONDS = 310


def fail(error):
    typer.echo(str(error),err=True)
    raise typer.Exit(1) from None


def directory_for(project=None,explicit=None):
    value=explicit
    if not value and project and (Path(project)/'.logchat/local.toml').is_file():
        value=binding(project).get('local_state_dir')
    return state_directory(value)


def api(directory,method,path,body=None,*,timeout=30):
    try:
        with httpx.Client(base_url=local_url(directory),headers={'Authorization':'Bearer '+control_token(directory)},trust_env=False,timeout=timeout) as client:
            response=client.request(method,path,json=body)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        if path.startswith('/settings/'):
            try: detail=error.response.json().get('detail')
            except ValueError: detail=None
            if isinstance(detail,str) and len(detail)<=1000:
                raise RuntimeError(detail) from None
        raise RuntimeError('The local request failed. Check logchat local status and its source configuration.') from None
    except httpx.HTTPError:
        raise RuntimeError('The local request failed. Check logchat local status and its source configuration.') from None


def save_binding(directory,config):
    folder=directory/'.logchat';folder.mkdir(exist_ok=True,mode=0o700)
    config_path=folder/'local.toml'
    temporary=folder/('local.'+str(uuid4())+'.tmp')
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,'w') as out:
            out.write('\n'.join(f'{key} = {json.dumps(value,ensure_ascii=False)}' for key,value in config.items())+'\n')
        os.replace(temporary,config_path)
    finally:temporary.unlink(missing_ok=True)
    # Node SDK gets non-secret endpoint and a protected credential reference, never a token.
    integration={key:config[key] for key in ('api_url','project_id','source_id')}
    integration['credential_file']=str(secret_dir()/(config['source_ref']+'.json'))
    descriptor=folder/'local.json'
    fd=os.open(descriptor,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as out:json.dump(integration,out)
    ignore=directory/'.gitignore'
    old=ignore.read_text() if ignore.exists() else ''
    entries=['.logchat/local.toml','.logchat/local.json','.logchat/local.*.tmp']
    missing=[entry for entry in entries if entry not in old.splitlines()]
    if missing:ignore.write_text(old+('' if not old or old.endswith('\n') else '\n')+'\n'.join(missing)+'\n')


def attach_project(project,port=None,name=None,environment='dev',state_dir=None,kind='process'):
    project=Path(project).expanduser().resolve()
    if not project.is_dir():raise RuntimeError('Select an existing project directory.')
    if port is not None and not 1<=port<=65535:raise RuntimeError('Application port must be between 1 and 65535.')
    directory=directory_for(project,state_dir)
    process,_=managed_process(directory)
    if not process:
        prior=binding(project) if (project/'.logchat/local.toml').is_file() else None
        prior_port=urlparse(_local_api_url(prior['api_url'])).port if prior and Path(prior.get('local_state_dir','')).resolve()==directory else DEFAULT_PORT
        start(port=prior_port or DEFAULT_PORT,state_dir=directory)
    from logchat.local.lifecycle import locked
    with locked(directory):
        previous=binding(project) if (project/'.logchat/local.toml').is_file() else None
        if port is None and previous: port=previous.get('app_port') or None
        if previous and previous['api_url']==local_url(directory) and previous.get('environment')==environment and previous.get('app_port',0)==(port or 0) and previous.get('kind')==kind:
            # Verify protected references still exist before reusing the binding.
            try:
                read_credential(previous['source_ref'],purpose='local_ingest',project_id=previous['project_id'],source_id=previous['source_id'],endpoint=previous['api_url'])
                read_credential(previous['session_ref'],purpose='local_control',project_id=previous['project_id'],endpoint=previous['api_url'])
                api(directory,'GET','/projects/'+previous['project_id']+'/status')
                return previous
            except (RuntimeError,KeyError):
                pass  # Rebuild stale or pre-scoping native bindings below.
        projects=api(directory,'GET','/projects')
        item=next((item for item in projects if item.get('path')==str(project)),None)
        if not item:item=api(directory,'POST','/projects',{'name':name or project.name,'path':str(project)})
        environments=api(directory,'GET','/projects/'+item['id']+'/environments')
        env=next((env for env in environments if env['name']==environment),None)
        if not env:env=api(directory,'POST','/projects/'+item['id']+'/environments',{'name':environment})
        source=api(directory,'POST','/projects/'+item['id']+'/sources',{'name':f'{name or project.name} / {environment} / {kind} / {port or 0} / {str(uuid4())[:8]}', 'environment':environment,'port':port,'kind':kind})
        if not source.get('token'):raise RuntimeError('The new source did not return its protected ingestion credential.')
        session_ref=write_credential(json.dumps({'access_token':control_token(directory)}),purpose='local_control',project_id=item['id'],endpoint=local_url(directory))
        source_ref=write_credential(source['token'],purpose='local_ingest',project_id=item['id'],source_id=source['id'],endpoint=local_url(directory))
        config={'name':item['name'],'project_id':item['id'],'environment':environment,'environment_id':env['id'],'api_url':local_url(directory),'session_ref':session_ref,
                'source_id':source['id'],'source_ref':source_ref,'local_state_dir':str(directory),'app_port':port or 0,'kind':kind}
        try:save_binding(project,config)
        except Exception:
            delete_credential(session_ref);delete_credential(source_ref);raise
        if previous:
            delete_credential(previous.get('session_ref'));delete_credential(previous.get('source_ref'))
        return config


@app.command('start')
def start_command(port:int=DEFAULT_PORT,state_dir:Path|None=None,foreground:bool=False):
    """Start one background localhost service. No app restart or model download."""
    try:
        directory=state_directory(state_dir)
        if foreground:
            from logchat.local.lifecycle import ensure_fresh_rag
            ensure_fresh_rag(directory)
            import uvicorn
            from logchat.local.app import create_app
            uvicorn.run(create_app(directory,port),host='127.0.0.1',port=port,access_log=False,log_level='critical')
        else:
            url=start(port,directory)
            capture_label='automatic accessible host capture' if os.getenv('LOGCHAT_CAPTURE_HOST','1')!='0' else 'host capture disabled'
            memory_label=('semantic RAG; configured model required' if (directory/'rag.json').exists() else
                'temporary capture; awaiting model configuration' if (directory/'capture.json').exists() else 'legacy lexical memory; semantic RAG not configured')
            typer.echo('Logchat local is running at '+url+' (SQLite; '+capture_label+'; '+memory_label+').')
            typer.echo('Timeline is ready without project setup. To add application output: logchat local attach --project PATH --port APP_PORT')
    except (RuntimeError,OSError) as error:fail(error)


@app.command('configure-rag')
def configure_rag(state_dir:Path|None=None,base_url:str='http://127.0.0.1:11434',
                  embedding_model: str = typer.Option(...), dimensions: int = typer.Option(..., min=1, max=4096), chat_model: str = typer.Option(...),
                  content_policy:str='local_model_compact'):
    """Enable the shared semantic core after verifying an installed embedding model."""
    import asyncio
    from logchat.local.rag_runtime import configure
    try:
        directory=state_directory(state_dir)
        process,_=managed_process(directory)
        if process:
            raise RuntimeError('Stop this instance before changing its semantic index configuration.')
        value=asyncio.run(configure(directory,base_url=base_url,model=embedding_model,dimensions=dimensions,
            chat_model=chat_model,content_policy=content_policy))
        typer.echo(json.dumps({'state':'configured','embedding':value['embedding'],
                              'content_policy':value['content_policy'],'generation_model':value['generation_model'],
                              'next':'Start this state directory; every attached source uses the shared semantic core.'},indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@app.command('retry')
def retry_semantic(project:Path=typer.Option(Path('.'),exists=True,file_okay=False)):
    """Retry bounded failed semantic jobs after repairing the configured model."""
    try:
        config=binding(project)
        result=api(Path(config['local_state_dir']),'POST','/projects/'+config['project_id']+'/retry')
        typer.echo(json.dumps(result,indent=2))
    except (RuntimeError,OSError,KeyError) as error:fail(error)


@app.command('serve')
def serve_command(port:int=DEFAULT_PORT,state_dir:Path|None=None):
    """Foreground mode for a supervisor or diagnostics; access logs stay disabled."""
    start_command(port,state_dir,True)


@app.command('stop')
def stop_command(state_dir:Path|None=None):
    """Stop only the sidecar process owned by this state directory; retain memory."""
    try:
        if (state_directory(state_dir)/'railway-manager.json').exists():stop_collection(state_dir)
        typer.echo('Logchat stopped.' if stop(state_dir) else 'Logchat is already stopped.')
    except (RuntimeError,OSError) as error:fail(error)


@app.command('status')
def status_command(state_dir:Path|None=None,project:Path|None=None):
    try:
        directory=directory_for(project,state_dir)
        url=local_url(directory)
        projects=api(directory,'GET','/projects')
        result={'status':'running','mode':'local','url':url,'projects':len(projects),'provider_capture':api(directory,'GET','/providers')}
        if project is not None:
            config=binding(project)
            result['project']=api(directory,'GET','/projects/'+config['project_id']+'/status')
        report=directory/'railway-status.json'
        if report.exists():result['railway_collection']=json.loads(report.read_text())
        typer.echo(json.dumps(result,indent=2))
    except (RuntimeError,OSError) as error:fail(error)


@app.command('collect-railway')
def collect_railway(project:Path=typer.Option(...,exists=True,file_okay=False),
                    service:list[str]=typer.Option(...), environment:str='prod',
                    command_prefix:str='["railway"]', since:str|None=None,
                    state_dir:Path|None=None, background:bool=False):
    """Collect configured Railway deployment logs without restarting the remote app.

    command-prefix is a JSON argv array, e.g. ["dhunctl","prod","railway"].
    Never put credentials in the prefix; use the CLI's protected login/profile.
    The default performs one pass; --background keeps collecting every five minutes.
    """
    from datetime import datetime,timedelta,timezone
    from logchat.local.lifecycle import write_private,locked
    from logchat.local.collection import run
    try:
        directory=state_directory(state_dir)
        local_url(directory)  # Require a running, explicitly selected instance.
        prefix=json.loads(command_prefix)
        if not isinstance(prefix,list) or not prefix or not all(isinstance(v,str) and v for v in prefix):
            raise ValueError('Use a JSON argv array for the configured CLI.')
        if any('token' in v.lower() or 'password' in v.lower() or 'secret' in v.lower() for v in prefix):
            raise ValueError('Use CLI credentials, not credentials in command arguments.')
        begin=datetime.fromisoformat(since.replace('Z','+00:00')) if since else datetime.now(timezone.utc)-timedelta(hours=1)
        if begin.tzinfo is None or begin>=datetime.now(timezone.utc):
            raise ValueError('Use a past timestamp with a timezone for --since.')
        config_path=directory/'railway-collection.json'
        with locked(directory):
            config=json.loads(config_path.read_text()) if config_path.exists() else {'sources':[], 'since':begin.astimezone(timezone.utc).isoformat(), 'interval':300}
            projects=api(directory,'GET','/projects')
            item=next((v for v in projects if v.get('path')==str(project.resolve())),None)
            if not item:item=api(directory,'POST','/projects',{'name':project.name,'path':str(project.resolve())})
            if config.get('project_id') and config['project_id']!=item['id']:
                raise ValueError('This collector belongs to another project; select a separate state directory.')
            config['project_id']=item['id']
            envs=api(directory,'GET','/projects/'+item['id']+'/environments')
            if not any(v['name']==environment for v in envs):
                api(directory,'POST','/projects/'+item['id']+'/environments',{'name':environment})
            for name in service:
                identity={'service':name,'environment':environment,'command_prefix':prefix}
                if any(all(v.get(k)==val for k,val in identity.items()) for v in config['sources']):continue
                value=api(directory,'POST','/projects/'+item['id']+'/sources',{'name':f'Railway / {environment} / {name}', 'kind':'push','environment':environment})
                # The collector is a local OS-user process; no ingestion credential is needed or saved.
                config['sources'].append({**identity,'id':value['id']})
            write_private(config_path,config)
        if background:
            import psutil
            metadata_path=directory/'railway-manager.json'
            try:
                old=json.loads(metadata_path.read_text());process=psutil.Process(old['pid'])
                if abs(process.create_time()-old['create_time'])<.01 and 'logchat.local.collection' in process.cmdline() and str(directory) in process.cmdline():
                    typer.echo('Railway collector is already running; configured sources updated.');return
            except (OSError,ValueError,KeyError,psutil.Error):pass
            child=subprocess.Popen([sys.executable,'-m','logchat.local.collection','--state-dir',str(directory)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,cwd=Path(__file__).resolve().parents[2])
            write_private(metadata_path,{'pid':child.pid,'create_time':psutil.Process(child.pid).create_time()})
            typer.echo('Railway collector started. Check logchat local status --state-dir '+str(directory))
        else:
            typer.echo(json.dumps(run(directory,once=True),indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@app.command('stop-collection')
def stop_collection(state_dir:Path|None=None):
    """Stop this instance's owned Railway collector; preserve summaries and cursors."""
    import psutil
    directory=state_directory(state_dir);path=directory/'railway-manager.json'
    try:
        metadata=json.loads(path.read_text());process=psutil.Process(metadata['pid'])
        if abs(process.create_time()-metadata['create_time'])<.01 and 'logchat.local.collection' in process.cmdline() and str(directory) in process.cmdline():
            process.terminate()
            typer.echo('Railway collector stopped; memory retained.')
        else:typer.echo('No owned Railway collector is running.')
    except (OSError,ValueError,KeyError,psutil.Error):typer.echo('Railway collector is already stopped.')
    path.unlink(missing_ok=True)


@app.command('discover')
def discover_command(json_output:bool=typer.Option(False,'--json')):
    """List visible user-owned listeners without reading argv or probing apps."""
    from logchat.local.discovery import discover
    result=discover()
    if json_output:typer.echo(json.dumps(result,indent=2));return
    for project in result['projects']:
        typer.echo(f"Port {project['port']} · {project['process']} · {project['path'] or 'Directory unavailable'}")
    typer.echo(result['notice'])


@app.command('attach')
def attach_command(project:Path=typer.Option(Path('.'),exists=True,file_okay=False),port:int|None=None,name:str|None=None,environment:str='dev',state_dir:Path|None=None):
    """Bind a project/port and generate protected native agent configuration."""
    try:
        config=attach_project(project,port,name,environment,state_dir)
        typer.echo('Project attached to '+config['api_url']+'. The app stays on its own port.')
        typer.echo('A port identifies the app; it does not expose logs. Capture the next dev run with:')
        typer.echo('logchat local run --project '+str(project.resolve())+' -- npm run dev')
        typer.echo('Agent configuration: logchat local integration --project '+str(project.resolve()))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@app.command('connect',context_settings={'allow_extra_args':True,'ignore_unknown_options':True})
def connect_command(ctx:typer.Context,
                    project:Path=typer.Option(Path('.'),exists=True,file_okay=False),
                    app_port:int|None=typer.Option(None,'--port','--app-port',min=1,max=65535),
                    logchat_port:int=typer.Option(DEFAULT_PORT,min=1,max=65535),
                    log_file:Path|None=None,from_start:bool=False,
                    environment:str='dev',state_dir:Path|None=None):
    """Connect local output: a background file watcher, or a wrapped app command."""
    command=list(ctx.args)
    if command and command[0]=='--':command=command[1:]
    if bool(log_file)==bool(command):
        raise typer.BadParameter('Choose --log-file PATH or an app command after --. A port alone does not expose logs.')
    if app_port is not None and app_port==logchat_port:
        raise typer.BadParameter('The application and Logchat must use different ports.')
    if from_start and log_file is None:
        raise typer.BadParameter('--from-start applies only to a log file.')
    try:
        directory=directory_for(project,state_dir)
        if log_file is not None:
            from .files import FileCapture
            # Validate before starting a service or changing a project binding.
            path=log_file.expanduser().absolute()
            with FileCapture.open_file(path):pass
        process,metadata=managed_process(directory)
        if process and metadata['port']!=logchat_port:
            raise RuntimeError('This Logchat instance uses a different port; supply its --logchat-port or a separate --state-dir.')
        start(logchat_port,directory)
        if not (directory/'rag.json').exists() and not (directory/'capture.json').exists():
            raise RuntimeError('This state uses legacy memory. Configure its semantic core with logchat local configure-rag before connecting logs.')
        if log_file is None:
            run_command(ctx,project,app_port,environment,directory)
            return
        config=attach_project(project,app_port,None,environment,directory,kind='push')
        result=api(directory,'POST',f"/projects/{config['project_id']}/sources/{config['source_id']}/file",
                   {'path':str(path),'from_start':from_start})
        typer.echo('Local log capture connected to '+config['api_url']+'. The application keeps running independently.')
        typer.echo('Capture continues in the Logchat service and resumes when it restarts. Pending originals become searchable only after the configured model summarizes and embeds them.')
        typer.echo('Existing lines are '+('included.' if from_start else 'skipped on the first connection.'))
        typer.echo('Check capture: logchat local status --project '+str(project.resolve()))
    except typer.Exit:
        raise  # Preserve the wrapped application's exit status, including success.
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@app.command('disconnect')
def disconnect_command(project:Path=typer.Option(Path('.'),exists=True,file_okay=False)):
    """Stop the bound file capture without stopping the app or deleting memories."""
    try:
        config=binding(project);directory=directory_for(project)
        adapter='provider' if config.get('kind') in {'docker','railway','vercel','cli'} else 'file'
        api(directory,'DELETE',f"/projects/{config['project_id']}/sources/{config['source_id']}/{adapter}")
        typer.echo('Capture stopped. Application and saved memory are retained.')
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@app.command('emit')
def emit_command(message:str,project:Path=typer.Option(Path('.'),exists=True,file_okay=False),service:str='app',level:str='info',duration_ms:float|None=None):
    """Explicitly deliver one structured event from an already-running project."""
    try:
        with LocalEmitter(project) as emitter:emitter.emit({'message':message,'service':service,'level':level,'duration_ms':duration_ms})
        typer.echo('Event accepted for summarized memory.')
    except Exception:fail(RuntimeError('Could not deliver this event; check the native binding and sidecar.'))


@app.command('integration')
def integration_command(project:Path=typer.Option(Path('.'),exists=True,file_okay=False),instructions:bool=False):
    """Print a keyless-to-config MCP binding; optional project logging instructions."""
    try:
        config=binding(project)
        # Pin the selected distribution, independent of the requesting app's cwd.
        bootstrap=f'import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r}); from cli.native import app; app()'
        value={'mcpServers':{'logchat':{'command':sys.executable,'args':['-I','-c',bootstrap,'local','mcp','--project',str(project.resolve())]}}}
        if os.getenv('LOGCHAT_SECRETS_DIR'):value['mcpServers']['logchat']['env']={'LOGCHAT_SECRETS_DIR':str(secret_dir())}
        typer.echo(json.dumps(value,indent=2))
        if instructions:
            sdk=Path(__file__).resolve().parents[1]/'resources/integrations/node.mjs'
            typer.echo('\nFor an existing server, use the Python LocalEmitter or Node SDK: '+str(sdk))
            typer.echo("import { createLogchat } from "+json.dumps(str(sdk))+";\nconst logs = createLogchat({ project: process.cwd() });\nawait logs.emit({ service: 'web', level: 'error', message: 'checkout timed out', duration_ms: 850 });")
            typer.echo('MCP uses the protected project session automatically. Read logchat://guide first and get_status to check the active semantic index, model stages and coverage gaps.')
    except (RuntimeError,OSError) as error:fail(error)


@app.command('mcp')
def mcp_command(project:Path=typer.Option(...,exists=True,file_okay=False,resolve_path=True)):
    """Serve read-only native project evidence via MCP stdio."""
    from logchat.mcp import create_server
    try:create_server(project,client_factory=LocalLogchatClient).run(transport='stdio')
    except Exception:
        typer.echo('Native project MCP is unavailable. Check logchat local attach and start.',err=True)
        raise typer.Exit(1) from None


@app.command('run',context_settings={'allow_extra_args':True,'ignore_unknown_options':True})
def run_command(ctx:typer.Context,project:Path=typer.Option(Path('.'),exists=True,file_okay=False),port:int|None=None,environment:str='dev',state_dir:Path|None=None):
    """Run a dev command while capturing stdout/stderr into redacted summaries."""
    command=list(ctx.args)
    if command and command[0]=='--':command=command[1:]
    if not command:raise typer.BadParameter('Supply a development command after --, for example -- npm run dev.')
    try:config=attach_project(project,port,None,environment,state_dir)
    except (RuntimeError,OSError,ValueError) as error:fail(error)
    rag_config=Path(config['local_state_dir'])/'rag.json'
    preserve_capture=rag_config.exists() and json.loads(rag_config.read_text()).get('content_policy') in {'local_model_preserved','local_model_compact'}
    from .raw_capture import load_capture_policy
    preserve_capture = preserve_capture or load_capture_policy(Path(config['local_state_dir']))['mode'] == 'retain_until_summarized'
    environment_vars=dict(os.environ)
    environment_vars.update({'LOGCHAT_INGEST_URL':config['api_url']+'/projects/'+config['project_id']+'/events','LOGCHAT_SOURCE_ID':config['source_id']})
    # The optional child SDK reads a protected project credential file; no token on argv/stdout.
    try:
        child=subprocess.Popen(command,cwd=project.resolve(),env=environment_vars,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,**({'start_new_session':True} if os.name!='nt' else {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}))
    except OSError:fail(RuntimeError('Could not start the development command in this project. Check its executable and working directory.'))
    import queue
    pending=queue.Queue(maxsize=2000)
    finished=threading.Event();dropped=[0];drain_deadline=[None]
    def capture(pipe,output,level):
        buffer=b'';overflow=False
        while True:
            data=pipe.read(4096)
            if not data:break
            output.buffer.write(data);output.buffer.flush()
            for part in data.splitlines(keepends=True):
                if overflow:
                    if part.endswith(b'\n'):overflow=False
                    continue
                buffer+=part
                if len(buffer)>12000:
                    buffer=b'';overflow=not part.endswith(b'\n');dropped[0]+=1;continue
                if part.endswith(b'\n'):
                    text=buffer.decode('utf-8',errors='replace').rstrip('\r\n');buffer=b''
                    if text.strip():
                        from connectors.local import parse_stdout_line
                        try:
                            value=asdict(parse_stdout_line(text,preserve_fields=preserve_capture))
                            value['timestamp']=value.pop('ts').isoformat();value.pop('source',None)
                            if level=='error' and value['level']=='info':
                                try:explicit=json.loads(text).get('level')
                                except (ValueError,AttributeError):explicit=None
                                if explicit is None:value['level']='error'
                        except (ValueError,OverflowError,TypeError):
                            dropped[0]+=1;continue
                        try:pending.put_nowait(value)
                        except queue.Full:dropped[0]+=1
        if buffer:
            try:pending.put_nowait({'message':buffer.decode('utf-8',errors='replace'),'level':level})
            except queue.Full:dropped[0]+=1
    def deliver():
        try:
            with LocalEmitter(project) as emitter:
                while not finished.is_set() or not pending.empty():
                    batch=[]
                    try:batch.append(pending.get(timeout=.2))
                    except queue.Empty:continue
                    while len(batch)<100:
                        try:batch.append(pending.get_nowait())
                        except queue.Empty:break
                    # Split batches by encoded bytes as well as event count.
                    groups=[];group=[];size=100
                    for event in batch:
                        event_size=len(json.dumps(event,ensure_ascii=False).encode('utf-8'))+2
                        if group and size+event_size>230*1024:
                            groups.append(group);group=[];size=100
                        group.append(event);size+=event_size
                    if group:groups.append(group)
                    for index, group in enumerate(groups):
                        if drain_deadline[0] is not None and time.monotonic() >= drain_deadline[0]:
                            dropped[0]+=sum(len(value) for value in groups[index:])+pending.qsize()
                            return
                        try:
                            emitter.emit_batch(group,retry_busy=True,drain_deadline=lambda: drain_deadline[0])
                        except Exception as error:
                            # Never render request bodies, credentials or server payloads.
                            category = 'transport_timeout' if isinstance(error, httpx.TimeoutException) else 'delivery_unavailable'
                            if isinstance(error, httpx.HTTPStatusError):
                                from .raw_capture import SAFE_PROCESSING_FAILURES
                                reported = error.response.headers.get('X-Logchat-Processing-Category')
                                if reported in SAFE_PROCESSING_FAILURES:category = reported
                            typer.echo('Logchat capture: '+category,err=True)
                            dropped[0]+=len(group)
        except Exception:
            dropped[0]+=pending.qsize()
    readers=[threading.Thread(target=capture,args=(child.stdout,sys.stdout,'info'),daemon=True),threading.Thread(target=capture,args=(child.stderr,sys.stderr,'error'),daemon=True)]
    sender=threading.Thread(target=deliver,name='logchat-wrapped-sender',daemon=True)
    for thread in readers+[sender]:thread.start()
    previous_term=signal.getsignal(signal.SIGTERM)
    def terminate_requested(signum,frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate_requested)
    try:code=child.wait()
    except KeyboardInterrupt:
        os.killpg(child.pid,signal.SIGTERM) if os.name!='nt' else child.terminate()
        try:code=child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL) if os.name!='nt' else child.kill()
            code=child.wait()
    finally:
        signal.signal(signal.SIGTERM,previous_term)
        for thread in readers:thread.join(timeout=3)
        # One shutdown budget for the entire queue; cancellation finishes inside
        # the sender before its clients close and before this command returns.
        drain_deadline[0]=time.monotonic()+WRAPPED_DRAIN_SECONDS
        finished.set();sender.join()
        child.stdout.close();child.stderr.close()
    if dropped[0]:typer.echo(f'Logchat: {dropped[0]} log events were not retained (overflow or sidecar unavailable). Coverage is incomplete.',err=True)
    raise typer.Exit(code if code>=0 else 128-code)


settings_app = typer.Typer(help="Inspect and change local settings; all commands use the authenticated service API.", no_args_is_help=True)
app.add_typer(settings_app, name="settings")


@settings_app.command("show")
def settings_show(state_dir: Path | None = typer.Option(None, "--state-dir")):
    """Print shared model, capture retention and environment visibility as JSON."""
    try:
        directory=state_directory(state_dir)
        value={key:api(directory,"GET",path) for key,path in
            (("model","/settings/models"),("capture","/settings/capture"),("ui","/settings/ui"))}
        typer.echo(json.dumps(value,indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@settings_app.command("capture")
def settings_capture(mode: str = typer.Option(...,"--mode"),
                     max_mb: int | None = typer.Option(None,"--max-mb",min=1,max=1024),
                     retention_hours: float | None = typer.Option(None,"--retention-hours",min=1/60,max=720),
                     state_dir: Path | None = typer.Option(None,"--state-dir")):
    """Set summary_only or retain_until_summarized; unspecified limits are preserved."""
    if mode not in {"summary_only","retain_until_summarized"}:
        raise typer.BadParameter("Choose summary_only or retain_until_summarized.")
    try:
        directory=state_directory(state_dir);current=api(directory,"GET","/settings/capture")
        value={"mode":mode,"max_bytes":max_mb*1048576 if max_mb is not None else current["max_bytes"],
            "retention_seconds":round(retention_hours*3600) if retention_hours is not None else current["retention_seconds"]}
        typer.echo(json.dumps(api(directory,"PUT","/settings/capture",value),indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@settings_app.command("ui")
def settings_ui(environments: bool = typer.Option(...,"--environments/--no-environments"),
                state_dir: Path | None = typer.Option(None,"--state-dir")):
    """Show or hide environment controls; this changes visibility, not access."""
    try:typer.echo(json.dumps(api(state_directory(state_dir),"PUT","/settings/ui",{"environments_enabled":environments}),indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@settings_app.command("models")
def settings_models(state_dir: Path | None = typer.Option(None,"--state-dir")):
    """List installed models from the configured local endpoint."""
    try:typer.echo(json.dumps(api(state_directory(state_dir),"GET","/settings/models/available"),indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@settings_app.command("model")
def settings_model(model: str = typer.Option(...,"--model"),
                   embedding_model: str | None = typer.Option(None,"--embedding-model"),
                   dimensions: int | None = typer.Option(None,"--dimensions", min=1, max=4096),
                   endpoint: str | None = typer.Option(None,"--endpoint"),
                   state_dir: Path | None = typer.Option(None,"--state-dir")):
    """Select one shared generation model after internal checks; keep the embedding index."""
    try:
        directory=state_directory(state_dir)
        current=api(directory,"GET","/settings/models")
        body={"model":model,"endpoint":endpoint or current.get("base_url") or "http://127.0.0.1:11434"}
        if embedding_model is not None: body["embedding_model"] = embedding_model
        if dimensions is not None: body["dimensions"] = dimensions
        typer.echo(json.dumps(api(directory,"PUT","/settings/shared-model",body,timeout=180),indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@settings_app.command("check")
def settings_check(state_dir: Path | None = typer.Option(None,"--state-dir")):
    """Check configured model availability without changing selection."""
    try:typer.echo(json.dumps(api(state_directory(state_dir),"POST","/settings/models/check"),indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@app.command('connect-provider')
def connect_provider_command(
    kind: str = typer.Argument(..., help='docker, railway, vercel, or cli'),
    project: Path = typer.Option(Path('.'), exists=True, file_okay=False),
    state_dir: Path | None = None,
    logchat_port: int = typer.Option(DEFAULT_PORT, min=1,max=65535),
    container: str | None = None, provider_project: str | None = None,
    provider_environment: str | None = None, service: str | None = None,
    scope: str | None = None, executable: str | None = None,
    argv: str | None = typer.Option(None, help='JSON argv array for a bounded NDJSON log CLI, with {since} and {until}.'),
    token_env: str | None = typer.Option(None,help='Environment variable NAME containing the provider token; never the token itself.'),
    prompt_token: bool = typer.Option(False,help='Read a provider token with hidden input and store it in a scoped private credential file.'),
    since: str | None = None,
    config_file: Path | None = typer.Option(None,exists=True,dir_okay=False,help='Non-secret adapter JSON.'),
    new_source: bool = typer.Option(False,help='Create a separate source for a different provider identity without replacing history.'),
):
    """Connect provider logs to the same local semantic pipeline; provider CLIs must be installed."""
    from .onboarding import connect_provider_source,load_source_config
    try:
        if kind not in {'docker','railway','vercel','cli'}: raise ValueError('Choose docker, railway, vercel, or cli.')
        if prompt_token and (token_env or kind not in {'railway','vercel'}):
            raise ValueError('--prompt-token supports Railway/Vercel and cannot be combined with --token-env.')
        config=load_source_config(config_file) if config_file else {}
        fields={'container':container,'provider_project':provider_project,'provider_environment':provider_environment,
                'service':service,'scope':scope,'executable':executable,'token_env':token_env,'since':since}
        config.update({k:v for k,v in fields.items() if v is not None})
        if argv is not None:config['argv']=json.loads(argv)
        token=typer.prompt('Provider token',hide_input=True) if prompt_token else None
        result=connect_provider_source(kind,project.resolve(),directory_for(project,state_dir),logchat_port,config,token=token,new_source=new_source)
        typer.echo(json.dumps(result,indent=2))
        typer.echo('Provider configured. Inspect logchat local status; capture and indexing are separate stages.')
    except (RuntimeError,OSError,ValueError):
        fail(RuntimeError('Provider setup failed. Check adapter fields, CLI availability and source identity; use --new-source for a different provider or service. No provider error payload is displayed.'))


@app.command('providers')
def providers_command(state_dir:Path|None=None,project:Path|None=None,poll:bool=False):
    """Inspect safe source status, or request one eligible provider collection pass."""
    try:
        directory=directory_for(project,state_dir)
        if poll:
            if project is None:raise ValueError('--poll requires --project.')
            config=binding(project)
            result=api(directory,'POST',f"/projects/{config['project_id']}/providers/poll",timeout=360)
        elif project is not None:
            config=binding(project)
            result=api(directory,'GET',f"/projects/{config['project_id']}/status")['provider_capture']
        else:result=api(directory,'GET','/providers')
        typer.echo(json.dumps(result,indent=2))
    except (RuntimeError,OSError,ValueError) as error:fail(error)


@app.command('ask')
def native_ask(question:str,project:Path=typer.Option(Path('.'),exists=True,file_okay=False),
               service:str|None=None,start_time:str|None=typer.Option(None,'--start'),
               end_time:str|None=typer.Option(None,'--end'),json_output:bool=typer.Option(False,'--json')):
    """Retrieve scoped context and supporting memories for the bound native project."""
    from logchat.client import LogchatError
    try:
        config=binding(project)
        if (start_time is None) != (end_time is None):raise ValueError('Supply --start and --end together.')
        result=api(directory_for(project),'POST',f"/projects/{config['project_id']}/ask",
                   {'question':question,'environment_ids':[config['environment_id']], 'timezone':'UTC',
                    'service':service,'start':start_time,'end':end_time},timeout=360)
        typer.echo(json.dumps(result,indent=2,ensure_ascii=False) if json_output else result.get('context_text',result.get('answer','')))
    except (RuntimeError,OSError,ValueError,LogchatError) as error:fail(error)
