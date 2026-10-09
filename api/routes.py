import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ConfigDict
from psycopg.types.json import Jsonb

from api.security import owner,database,project_access
from cli.secrets import read_credential
from connectors.registry import make_connector
from pipeline.models import LocalModels, ModelUnavailable
from pipeline.redaction import redact_text

router=APIRouter()

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')

class Session(Input):
    email:str=Field(min_length=3,max_length=254)
    password:str=Field(min_length=12,max_length=72)
    create:bool=False

@router.post('/session')
async def session(body:Session,response:Response):
    url=os.environ['AUTH_URL']+('/signup' if body.create else '/token?grant_type=password')
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=20) as client:
            upstream=await client.post(url,json={'email':body.email,'password':body.password})
            upstream.raise_for_status(); data=upstream.json(); token=data['access_token']
    except Exception: raise HTTPException(401,'Could not sign in. Check your local account details.') from None
    response.set_cookie('logchat_session',token,httponly=True,samesite='strict',max_age=3600)
    return {'access_token':token,'owner_id':data['user']['id'],'expires_in':data.get('expires_in',3600)}

@router.delete('/session')
def logout(response:Response):
    response.delete_cookie('logchat_session'); return {'status':'signed_out'}

class Project(Input):
    name:str=Field(min_length=1,max_length=200)

@router.post('/projects',status_code=201)
def create_project(body:Project,owner_id:str=Depends(owner)):
    with database(owner_id) as conn:
        row=conn.execute('INSERT INTO projects(owner_id,name) VALUES (%s,%s) RETURNING id,name',(owner_id,redact_text(body.name))).fetchone()
        row['environments']=conn.execute('SELECT id,name FROM environments WHERE project_id=%s ORDER BY name',(row['id'],)).fetchall()
        return row

@router.get('/projects')
def projects(owner_id:str=Depends(owner)):
    with database(owner_id) as conn: return conn.execute('SELECT id,name FROM projects ORDER BY created_at').fetchall()

@router.get('/projects/{project_id}/environments')
def environments(project_id:UUID,owner_id:str=Depends(owner)):
    with database(owner_id) as conn:
        project_access(conn,project_id)
        return conn.execute('SELECT id,name FROM environments WHERE project_id=%s ORDER BY name',(project_id,)).fetchall()

@router.post('/projects/{project_id}/environments',status_code=201)
def add_environment(project_id:UUID,body:Project,owner_id:str=Depends(owner)):
    if not __import__('re').fullmatch(r'[a-z][a-z0-9_-]{0,39}',body.name): raise HTTPException(422,'Use a short environment name such as dev or prod.')
    with database(owner_id) as conn:
        project_access(conn,project_id)
        return conn.execute('INSERT INTO environments(owner_id,project_id,name) VALUES (%s,%s,%s) ON CONFLICT(project_id,name) DO UPDATE SET name=EXCLUDED.name RETURNING id,name',(owner_id,project_id,body.name)).fetchone()

class Source(Input):
    id:UUID=Field(default_factory=uuid4)
    environment_id:UUID
    connector:str
    source_project_id:str=Field(min_length=1,max_length=200)
    retention_seconds:int=Field(gt=0,le=31536000)
    connector_config:dict=Field(default_factory=dict)
    credential_ref:str|None=None

@router.post('/projects/{project_id}/sources',status_code=201)
async def connect_source(project_id:UUID,body:Source,owner_id:str=Depends(owner)):
    schemas={'docker':({'container'},{'container','service'}), 'sentry':({'organization','project'},{'organization','project','service','provider_environment'})}
    if body.connector not in schemas: raise HTTPException(422,'This connector is not available yet. Docker and Sentry are supported.')
    required,allowed=schemas[body.connector]
    if not required<=body.connector_config.keys() or set(body.connector_config)-allowed or any(not isinstance(v,str) or len(v)>200 for v in body.connector_config.values()):
        raise HTTPException(422,'Invalid connector configuration; credentials belong in the local secret store.')
    if any(redact_text(v)!=v for v in body.connector_config.values()): raise HTTPException(422,'Do not put secrets or personal data in connector configuration.')
    with database(owner_id) as conn:
        project_access(conn,project_id)
        if not conn.execute('SELECT id FROM environments WHERE id=%s AND project_id=%s',(body.environment_id,project_id)).fetchone(): raise HTTPException(404,'Environment not found.')
    try:
        credential=read_credential(body.credential_ref,owner_id=owner_id,project_id=str(project_id),environment_id=str(body.environment_id),source_id=str(body.id)) if body.credential_ref else None
        connector=make_connector(body.connector,body.connector_config,credential)
        now=datetime.now(timezone.utc)
        await connector.probe(now,now)
    except Exception: raise HTTPException(422,'Connector unavailable. Check its configuration, credentials, or the Docker profile.') from None
    with database(owner_id) as conn:
        return conn.execute('INSERT INTO sources(id,owner_id,project_id,environment_id,connector,source_project_id,retention_seconds,connector_config,credential_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,environment_id,connector,source_project_id,retention_seconds',
            (body.id,owner_id,project_id,body.environment_id,body.connector,redact_text(body.source_project_id),body.retention_seconds,Jsonb(body.connector_config),body.credential_ref)).fetchone()

@router.get('/projects/{project_id}/status')
async def status(project_id:UUID,owner_id:str=Depends(owner)):
    with database(owner_id) as conn:
        project=project_access(conn,project_id)
        sources=conn.execute('SELECT s.id,s.connector,s.source_project_id,s.environment_id,e.name AS environment,s.enabled,st.cursor_ts,st.next_check_at,st.interval_seconds FROM sources s JOIN environments e ON e.id=s.environment_id LEFT JOIN pipeline_source_state st ON st.source_id=s.id WHERE s.project_id=%s ORDER BY e.name,s.connector',(project_id,)).fetchall()
        jobs=conn.execute('SELECT id,source_id,status,attempts,error_summary,window_start,window_end FROM pipeline_jobs WHERE project_id=%s ORDER BY created_at DESC LIMIT 20',(project_id,)).fetchall()
        chunks=conn.execute('SELECT count(*) AS count FROM summary_chunks WHERE project_id=%s',(project_id,)).fetchone()['count']
    return {'project':{'id':project['id'],'name':project['name']},'sources':sources,'recent_jobs':jobs,'chunks':chunks,'models':await LocalModels().installed()}

@router.post('/projects/{project_id}/retry')
def retry_processing(project_id:UUID,owner_id:str=Depends(owner)):
    with database(owner_id) as conn:
        project_access(conn,project_id)
        count=conn.execute('SELECT public.retry_failed_jobs(%s) AS count',(project_id,)).fetchone()['count']
        return {'retried_jobs':count}

class Question(Input):
    question:str=Field(min_length=1,max_length=2000)
    environment_ids:list[UUID]=Field(min_length=1,max_length=4)
    timezone:str='America/New_York'
    start:datetime|None=None
    end:datetime|None=None
    compare_start:datetime|None=None
    compare_end:datetime|None=None
    service:str|None=Field(default=None,max_length=200)

@router.post('/projects/{project_id}/ask')
async def ask(project_id:UUID,body:Question,owner_id:str=Depends(owner)):
    from pipeline.retrieval import answer_question,QueryError
    try: return await answer_question(owner_id,str(project_id),body)
    except ModelUnavailable as error: raise HTTPException(503,str(error)) from None
    except QueryError as error: raise HTTPException(422,str(error)) from None
