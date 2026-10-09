"""Project onboarding and questions for the local logchat stack."""
import json
import os
import subprocess
import tomllib
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import typer
from rich import print
from rich.table import Table

from cli.secrets import write_credential,read_credential,delete_credential

app=typer.Typer(help='Connect a personal project and ask how it is doing.',no_args_is_help=True)
env_app=typer.Typer(help='Manage dev, prod, and other environments.')
models_app=typer.Typer(help='Prepare local models.')
mcp_app=typer.Typer(help='Connect coding agents through local MCP.')
app.add_typer(env_app,name='env');app.add_typer(models_app,name='models')
app.add_typer(mcp_app,name='mcp')
from logchat.local.cli import app as local_app, settings_app
app.add_typer(local_app,name='local')
app.add_typer(settings_app,name='settings')
from logchat.local.installer import install_command
app.command('install')(install_command)


def stack_dir():
    """Installation root; deliberately independent of the selected project cwd."""
    from cli.runtime import runtime_dir
    return runtime_dir()

def _stack_port(name,default):
    value=None
    try:
        # Inspect only the requested assignment. Other credential values are never
        # parsed, retained, returned, or printed by the CLI.
        with (stack_dir()/'.logchat/.secrets').open() as settings:
            for line in settings:
                if line.startswith(name+'='):
                    value=line.split('=',1)[1].strip()
    except OSError:
        return default
    if value is None:return default
    if len(value)>=2 and value[0]==value[-1] and value[0] in {'"',"'"}:value=value[1:-1]
    if not value.isascii() or not value.isdecimal():raise typer.BadParameter(name+' must be a port from 1 to 65535.')
    port=int(value)
    if not 1<=port<=65535:raise typer.BadParameter(name+' must be a port from 1 to 65535.')
    return port

def _local_url(value):
    try:
        parsed=urlparse(value);port=parsed.port
    except (TypeError,ValueError):
        raise typer.BadParameter('Use a valid local API address and port.') from None
    if (parsed.scheme not in {'http','https'} or parsed.hostname not in {'localhost','127.0.0.1','::1'}
            or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in {'','/'}):
        raise typer.BadParameter('Use a local API address.')
    port=port or (443 if parsed.scheme=='https' else 80)
    if not 1<=port<=65535:raise typer.BadParameter('Use a valid local API address and port.')
    return value.rstrip('/')

def base_url():
    value=os.getenv('LOGCHAT_API_URL')
    if value is not None:return _local_url(value)
    return 'http://127.0.0.1:'+str(_stack_port('API_PORT',8080))

def _docker_environment():
    """Pin a validated local daemon without changing the user's Docker context."""
    context=os.getenv('DOCKER_CONTEXT')
    endpoint=os.getenv('DOCKER_HOST') if not context else None
    if not endpoint:
        args=['docker','context','inspect']+([context] if context else [])+['--format','{{.Endpoints.docker.Host}}']
        try:
            result=subprocess.run(args,capture_output=True,text=True,timeout=10)
        except (FileNotFoundError,subprocess.TimeoutExpired):
            raise typer.BadParameter('A local Docker installation is required.') from None
        if result.returncode:raise typer.BadParameter('Select a local Docker context before starting logchat.')
        endpoint=result.stdout.strip()
    parsed=urlparse(endpoint)
    if parsed.scheme!='unix' or parsed.netloc or not Path(parsed.path).is_absolute():
        raise typer.BadParameter('Remote Docker endpoints are unsupported. Select a local Unix-socket Docker context.')
    env=dict(os.environ);env.pop('DOCKER_CONTEXT',None);env['DOCKER_HOST']=endpoint
    return env

def config_path(): return Path.cwd()/'.logchat/config.toml'

def config():
    try:return tomllib.loads(config_path().read_text())
    except (OSError,ValueError):raise typer.BadParameter('Run logchat init in this project first.') from None

def save_config(value):
    path=config_path();path.parent.mkdir(exist_ok=True)
    path.write_text('\n'.join(f'{key} = {json.dumps(val)}' for key,val in value.items())+'\n')
    ignore=Path.cwd()/'.gitignore'
    old=ignore.read_text() if ignore.exists() else ''
    if '.logchat/session' not in old:ignore.write_text(old+'\n.logchat/session*\n.logchat/.secrets*\n')

def request(method,path,body=None,authenticated=True):
    headers={}
    if authenticated:
        cfg=config()
        session=json.loads(read_credential(cfg['session_ref']))
        headers['Authorization']='Bearer '+session['access_token']
    try:
        response=httpx.request(method,base_url()+path,trust_env=False,json=body,headers=headers,timeout=240 if path.endswith('/ask') else 30)
        if response.is_error:
            detail=response.json().get('detail','Request failed.')
            raise typer.BadParameter(str(detail))
        return response.json()
    except httpx.HTTPError:raise typer.BadParameter('Local stack unavailable. Start it from the logchat installation folder.') from None

def project_path(suffix=''):return '/projects/'+config()['project_id']+suffix

def environment_id(name=None):
    selected=name or config().get('environment','dev')
    rows=request('GET',project_path('/environments'))
    for row in rows:
        if row['name']==selected:return row['id']
    raise typer.BadParameter('Environment not found. Use logchat env add '+selected)

@app.command()
def init(name:str=typer.Option(None),email:str=typer.Option(None),existing_account:bool=False):
    """Initialize this project with a local account and dev environment."""
    if config_path().exists():raise typer.BadParameter('This project is already initialized.')
    email=email or typer.prompt('Local account email')
    password=typer.prompt('Local account password (12+ characters)',hide_input=True,confirmation_prompt=not existing_account)
    session=request('POST','/session',{'email':email,'password':password,'create':not existing_account},authenticated=False)
    ref=write_credential(json.dumps({'access_token':session['access_token'],'email':email}))
    save_config({'name':name or Path.cwd().name,'project_id':str(uuid4()),'environment':'dev','session_ref':ref,'owner_id':session['owner_id']})
    try:
        project=request('POST','/projects',{'name':name or Path.cwd().name})
        cfg=config();cfg['project_id']=project['id'];save_config(cfg)
    except Exception:
        config_path().unlink(missing_ok=True);delete_credential(ref);raise
    print('[green]Project connected to local logchat.[/green] Your first environment is dev.')
    print('Next: logchat connect docker --container YOUR_CONTAINER --retention-hours 24')

@app.command()
def login(email:str=typer.Option(None)):
    """Renew this project's local sign-in."""
    cfg=config();old=json.loads(read_credential(cfg['session_ref']))
    email=email or old['email'];password=typer.prompt('Local account password',hide_input=True)
    session=request('POST','/session',{'email':email,'password':password,'create':False},authenticated=False)
    if session['owner_id']!=cfg['owner_id']:raise typer.BadParameter('Sign in with the owner of this project.')
    ref=write_credential(json.dumps({'access_token':session['access_token'],'email':email}))
    delete_credential(cfg['session_ref']);cfg['session_ref']=ref;save_config(cfg);print('Signed in locally.')

@env_app.command('add')
def env_add(name:str):
    row=request('POST',project_path('/environments'),{'name':name});print('Added environment '+row['name'])

@env_app.command('use')
def env_use(name:str):
    environment_id(name);cfg=config();cfg['environment']=name;save_config(cfg);print('Selected '+name)

@env_app.command('list')
def env_list():
    for row in request('GET',project_path('/environments')):print(row['name'])

@app.command()
def connect(connector:str,container:str=typer.Option(None),environment:str=typer.Option(None),retention_hours:float=typer.Option(None),organization:str=typer.Option(None),source_project:str=typer.Option(None),service:str=typer.Option(None),provider_environment:str=typer.Option(None)):
    """Connect a source to the selected environment; secrets stay local."""
    if connector not in {'docker','sentry'}:raise typer.BadParameter('Docker and Sentry are available in this build.')
    if retention_hours is None:retention_hours=typer.prompt('Verified source retention in hours',type=float)
    if retention_hours<=0:raise typer.BadParameter('Retention must be positive.')
    cfg=config();env_id=environment_id(environment);source_id=str(uuid4());credential_ref=None
    if connector=='docker':
        container=container or typer.prompt('Docker container name')
        settings={'container':container};source_project_id=container
    else:
        organization=organization or typer.prompt('Sentry organization slug')
        source_project=source_project or typer.prompt('Sentry project slug')
        credential_ref=write_credential(typer.prompt('Sentry read token',hide_input=True),owner_id=cfg['owner_id'],project_id=cfg['project_id'],environment_id=env_id,source_id=source_id)
        settings={'organization':organization,'project':source_project};source_project_id=organization+'/'+source_project
        if provider_environment:settings['provider_environment']=provider_environment
    if service:settings['service']=service
    try:request('POST',project_path('/sources'),{'id':source_id,'environment_id':env_id,'connector':connector,'source_project_id':source_project_id,'retention_seconds':max(1,int(retention_hours*3600)),'connector_config':settings,'credential_ref':credential_ref})
    except Exception:
        delete_credential(credential_ref);raise
    print('[green]Source connected.[/green] The scheduler will start building history automatically.')

@app.command()
def status():
    """See connected sources, pipeline progress, and local model availability."""
    if not config_path().exists():print(request('GET','/health/ready',authenticated=False));return
    data=request('GET',project_path('/status'))
    print(data['project']['name']+' — '+str(data['chunks'])+' searchable chunks')
    table=Table('Environment','Source','Processed through','Next check')
    for source in data['sources']:table.add_row(source['environment'],source['source_project_id'],str(source['cursor_ts'] or 'Not yet'),str(source['next_check_at']))
    print(table)
    if not all(data['models'][key] for key in ('chat','embedding')):print('Models need preparation: logchat models pull')
    for job in data['recent_jobs'][:5]:print(job['status'],job.get('error_summary') or '')

@app.command()
def ask(question:str,environment:list[str]=typer.Option(None,'--environment','-e'),start:str=typer.Option(None),end:str=typer.Option(None),compare_start:str=typer.Option(None),compare_end:str=typer.Option(None),timezone:str='America/New_York',service:str=typer.Option(None)):
    """Ask about this project, or compare environments and time windows."""
    ids=[environment_id(name) for name in (environment or [config().get('environment','dev')])]
    result=request('POST',project_path('/ask'),{'question':question,'environment_ids':ids,'timezone':timezone,'start':start,'end':end,'compare_start':compare_start,'compare_end':compare_end,'service':service})
    print(result['answer'])
    for assumption in result['plan']['assumptions']:print('Assumption: '+assumption)
    for gap in result['gaps']:print('Coverage: '+gap)
    table=Table('Evidence','Environment','Service','Period','Summary')
    for row in result['evidence']:table.add_row(row['id'],row['environment'],row['service'],row['cell'].split(':')[-1],row['summary'])
    if result['evidence']:print(table)

@app.command()
def retry():
    """Retry failed processing after fixing a connector or preparing the models."""
    data=request('POST',project_path('/retry'))
    print(str(data['retried_jobs'])+' failed jobs queued for another attempt.')

@app.command()
def up(docker:bool=False):
    """Start the headless local stack. No browser or UI is needed."""
    from cli.runtime import prepare_runtime
    docker_environment=_docker_environment()
    try:stack=prepare_runtime()
    except RuntimeError as error:raise typer.BadParameter(str(error)) from None
    prepared=subprocess.run([sys.executable,str(stack/'scripts/setup.py')],cwd=stack)
    if prepared.returncode:raise typer.Exit(prepared.returncode)
    args=['docker','compose','--env-file',str(stack/'.logchat/.secrets')]
    if docker:args+=['--profile','docker']
    try:result=subprocess.run(args+['up','-d','--build','--remove-orphans','--wait','--wait-timeout','300'],cwd=stack,env=docker_environment)
    except FileNotFoundError:raise typer.BadParameter('Install Docker with Compose before starting logchat.') from None
    if result.returncode:raise typer.Exit(result.returncode)
    print('Local pipeline ready. Run logchat doctor, then logchat init in your project.')

@app.command()
def doctor():
    """Check local service readiness, project scope, models, and connected sources."""
    healthy=True
    try:
        data=request('GET','/health/ready',authenticated=False)
        print('Local API: '+data['status'])
        for name,state in data.get('checks',{}).items():print(name+': '+state)
        healthy=data.get('status')=='ready'
    except typer.BadParameter:
        print('Local API unavailable. Run logchat up --docker.');healthy=False
    if config_path().exists():
        try:
            data=request('GET',project_path('/status'))
            print('Project: '+data['project']['name'])
            print('Connected sources: '+str(len(data['sources'])))
            for name in ('chat','embedding'):
                ready=bool(data['models'].get(name));print(name+' model: '+('ready' if ready else 'missing'))
                healthy=healthy and ready
            if not data['sources']:print('Next: logchat connect docker --container NAME --retention-hours HOURS')
        except (typer.BadParameter,RuntimeError,KeyError,ValueError):
            print('Project session unavailable. Run logchat login.');healthy=False
    else:print('Project not initialized. Run logchat init here.')
    if not healthy:raise typer.Exit(1)

@app.command()
def setup(docker:bool=False,container:str=typer.Option(None),retention_hours:float=typer.Option(None),name:str=typer.Option(None),email:str=typer.Option(None),existing_account:bool=False):
    """Start services/models, initialize this project, and optionally connect Docker."""
    if container and (not docker or retention_hours is None or retention_hours<=0):
        raise typer.BadParameter('With --container, supply --docker and a positive --retention-hours.')
    up(docker=docker);model_pull()
    if not config_path().exists():init(name=name,email=email,existing_account=existing_account)
    if container:connect('docker',container=container,environment=None,retention_hours=retention_hours,organization=None,source_project=None,service=None,provider_environment=None)
    doctor()
    print('Agent setup: logchat mcp config --project '+str(Path.cwd()))

@mcp_app.command('serve')
def mcp_serve(project:Path=typer.Option(...,exists=True,file_okay=False,resolve_path=True)):
    """Run project-scoped MCP over stdio. Stdout is reserved for the protocol."""
    from logchat.mcp import serve
    from logchat import LogchatError
    try:serve(project)
    except LogchatError as error:
        typer.echo(str(error),err=True)
        raise typer.Exit(1) from None

@mcp_app.command('config')
def mcp_config(project:Path=typer.Option(...,exists=True,file_okay=False,resolve_path=True)):
    """Print generic MCP client configuration; does not edit agent settings."""
    if not (project/'.logchat/config.toml').is_file():raise typer.BadParameter('Run logchat init in the project first.')
    value={'mcpServers':{'logchat':{'command':sys.executable,'args':['-m','cli.main','mcp','serve','--project',str(project)],'env':{'LOGCHAT_STACK_DIR':str(stack_dir())}}}}
    if os.getenv('LOGCHAT_API_URL') is not None:
        value['mcpServers']['logchat']['env']['LOGCHAT_API_URL']=base_url()
    if os.getenv('LOGCHAT_SECRETS_DIR'):
        value['mcpServers']['logchat']['env']['LOGCHAT_SECRETS_DIR']=os.environ['LOGCHAT_SECRETS_DIR']
    typer.echo(json.dumps(value,indent=2))

@models_app.command('pull')
def model_pull():
    """Download embedding and chat models into the local Docker volume."""
    stack=stack_dir()
    docker_environment=_docker_environment()
    chat=os.getenv('LOGCHAT_CHAT_MODEL')
    if not chat:
        try:
            with (stack/'.logchat/.secrets').open() as settings:
                chat=next((line.split('=',1)[1].strip() for line in settings if line.startswith('LOGCHAT_CHAT_MODEL=')),None)
        except OSError:pass
    for model in ('nomic-embed-text',chat or 'qwen2.5:1.5b'):
        result=subprocess.run(['docker','compose','--env-file',str(stack/'.logchat/.secrets'),'exec','ollama','ollama','pull',model],cwd=stack,env=docker_environment)
        if result.returncode:raise typer.Exit(result.returncode)
    print('Local models ready.')

@app.command('start')
def native_start(port:int=8765,state_dir:Path|None=None):
    """Start minimal background Logchat on one port, without Docker."""
    from logchat.local.cli import start_command
    start_command(port,state_dir,False)

if __name__=='__main__':app()
