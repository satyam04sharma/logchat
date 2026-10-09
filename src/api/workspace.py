"""Owner-scoped investigations, safe summary search and conversational retrieval."""
import json
import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from psycopg.types.json import Jsonb

from api.routes import Input, Question
from api.security import database, owner, project_access
from pipeline.models import ModelUnavailable
from pipeline.redaction import redact_text, sanitize
from pipeline.retrieval import QueryError, answer_question

router = APIRouter()
CONVERSATION_FIELDS = 'id,title,created_at,updated_at'
MESSAGE_FIELDS = 'id,role,content,result,created_at'

@router.get('/guide')
def knowledge_guide():
    from logchat.mcp import GUIDE
    return {'guide': GUIDE}

@router.get('/guide/skill')
def knowledge_skill():
    from fastapi.responses import PlainTextResponse
    from logchat.mcp import GUIDE
    return PlainTextResponse(GUIDE,headers={'Content-Disposition':'attachment; filename="SKILL.md"'})

@router.post('/projects/{project_id}/agent-config')
def agent_config(project_id: UUID, request: Request, owner_id: str = Depends(owner)):
    """Bind the existing read-only stdio server using Docker, without host Python."""
    from cli.secrets import write_credential,delete_credential,secret_dir
    installation=os.getenv('LOGCHAT_HOST_ROOT')
    if not installation:
        raise HTTPException(409,'Start the stack with ./start.sh to generate Docker agent configuration.')
    authorization=request.headers.get('authorization','')
    token=authorization[7:] if authorization.lower().startswith('bearer ') else request.cookies.get('logchat_session')
    with database(owner_id) as conn:
        project=project_access(conn,project_id)
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 190058))', (str(project_id),))
        directory=secret_dir()/'agents'/str(project_id)
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        directory.chmod(0o700)
        state=directory/'binding.json'
        previous=json.loads(state.read_text()).get('session_ref') if state.exists() else None
        reference=write_credential(json.dumps({'access_token':token}))
        values={'name':project['name'],'project_id':str(project_id),'owner_id':owner_id,'environment':'dev','session_ref':reference}
        config=directory/'.logchat'
        config.mkdir(exist_ok=True,mode=0o700)
        try:
            for path,content in ((config/'config.toml','\n'.join(f'{key} = {json.dumps(value,ensure_ascii=False)}' for key,value in values.items())+'\n'),(state,json.dumps({'session_ref':reference}))):
                temporary=path.with_name(path.name+'.'+str(uuid4())+'.tmp')
                fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                try:
                    with os.fdopen(fd,'w') as out:
                        out.write(content)
                    os.replace(temporary,path)
                finally:
                    temporary.unlink(missing_ok=True)
        except Exception:
            # Do not remove the newly referenced credential after a partial atomic replacement.
            raise HTTPException(503,'Could not save the agent binding. Generate it again.') from None
        if previous:
            delete_credential(previous)
    args=['compose','--project-directory',installation,'--env-file',str(Path(installation)/'.logchat/.secrets'),'exec','-T','-e','LOGCHAT_API_URL=http://127.0.0.1:8080','api','logchat','mcp','serve','--project',str(directory)]
    return {'mcpServers':{'logchat':{'command':'docker','args':args}},'notice':'This Docker stdio binding uses your local session. If it expires, sign in again and regenerate the configuration. Tools are read-only.'}

class Investigation(Input):
    title: str = Field(default='New investigation', min_length=1, max_length=200)

class Turn(Question):
    request_id: UUID = Field(default_factory=uuid4)

def conversation_access(conn, project_id, conversation_id):
    project_access(conn, project_id)
    row = conn.execute(f'SELECT {CONVERSATION_FIELDS} FROM conversations WHERE id=%s AND project_id=%s', (conversation_id, project_id)).fetchone()
    if not row:
        raise HTTPException(404, 'Investigation not found.')
    return row

@router.get('/projects/{project_id}/conversations')
def conversations(project_id: UUID, owner_id: str = Depends(owner)):
    with database(owner_id) as conn:
        project_access(conn, project_id)
        return conn.execute(f'SELECT {CONVERSATION_FIELDS} FROM conversations WHERE project_id=%s ORDER BY updated_at DESC LIMIT 100', (project_id,)).fetchall()

@router.post('/projects/{project_id}/conversations', status_code=201)
def create_conversation(project_id: UUID, body: Investigation, owner_id: str = Depends(owner)):
    with database(owner_id) as conn:
        project_access(conn, project_id)
        return conn.execute(f'INSERT INTO conversations(owner_id,project_id,title) VALUES(%s,%s,%s) RETURNING {CONVERSATION_FIELDS}', (owner_id,project_id,redact_text(body.title))).fetchone()

@router.get('/projects/{project_id}/conversations/{conversation_id}')
def conversation(project_id: UUID, conversation_id: UUID, owner_id: str = Depends(owner)):
    with database(owner_id) as conn:
        row = conversation_access(conn, project_id, conversation_id)
        messages = conn.execute(f'SELECT {MESSAGE_FIELDS} FROM conversation_messages WHERE conversation_id=%s ORDER BY created_at,id LIMIT 500', (conversation_id,)).fetchall()
        return {'conversation': row, 'messages': messages}

def turn_scope(body, previous_result, previous_request=None):
    """A follow-up keeps the last window unless it asks for a new one explicitly."""
    if not previous_result:
        return body
    cells = previous_result.get('plan', {}).get('cells', [])
    if previous_request and sorted(str(e) for e in body.environment_ids)==sorted(previous_request.get('environment_ids',[])):
        previous_ids=list(dict.fromkeys(cell['id'].split(':')[0] for cell in cells if cell.get('id')))
        if previous_ids:
            body=body.model_copy(update={'environment_ids':[UUID(e) for e in previous_ids]})
    if body.start is not None:
        return body
    if re.search(r'\btoday\b|\byesterday\b|\bthis week\b|\blast (?:week|month)\b|\b(?:last|past|this) hour\b|\b\d+\s*(?:hours?|days?)\b', body.question, re.I):
        return body
    windows = {cell.get('window', {}).get('label'): cell.get('window') for cell in cells}
    current = windows.get('current')
    if not current:
        return body
    from datetime import datetime
    def timestamp(value):
        return datetime.fromisoformat(value) if isinstance(value, str) else value
    updates = {'start': timestamp(current['start']), 'end': timestamp(current['end'])}
    if body.compare_start is None and 'previous' in windows:
        updates.update(compare_start=timestamp(windows['previous']['start']), compare_end=timestamp(windows['previous']['end']))
    return body.model_copy(update=updates)

@router.post('/projects/{project_id}/conversations/{conversation_id}/messages')
async def send_message(project_id: UUID, conversation_id: UUID, body: Turn, owner_id: str = Depends(owner)):
    request = sanitize(body.model_dump(mode='json', exclude={'request_id'}))
    token = uuid4()
    question = redact_text(body.question)
    with database(owner_id) as conn:
        row = conversation_access(conn, project_id, conversation_id)
        # Serialize only the claim transaction; never hold a DB lock during inference.
        conn.execute('SELECT id FROM conversations WHERE id=%s FOR UPDATE', (conversation_id,))
        earlier = conn.execute('SELECT role,request,result FROM conversation_messages WHERE conversation_id=%s AND request_id=%s', (conversation_id,body.request_id)).fetchall()
        if earlier:
            user = next((item for item in earlier if item['role']=='user'), None)
            if not user or user['request'] != request:
                raise HTTPException(409, 'This request ID belongs to a different question.')
            assistant = next((item for item in earlier if item['role']=='assistant'), None)
            if assistant:
                messages = conn.execute(f'SELECT {MESSAGE_FIELDS} FROM conversation_messages WHERE conversation_id=%s AND request_id=%s', (conversation_id,body.request_id)).fetchall()
                return {'user_message':next(m for m in messages if m['role']=='user'), 'assistant_message':next(m for m in messages if m['role']=='assistant'), 'result':assistant['result']}
        claimed = conn.execute("UPDATE conversations SET pending_token=%s,pending_until=now()+interval '10 minutes' WHERE id=%s AND (pending_token IS NULL OR pending_until<now()) RETURNING id", (token,conversation_id)).fetchone()
        if not claimed:
            raise HTTPException(409, 'An answer is already being prepared. Wait for it, then retry.')
        previous = conn.execute('SELECT role,content,result,request FROM conversation_messages WHERE conversation_id=%s ORDER BY created_at DESC,id DESC LIMIT 12', (conversation_id,)).fetchall()
        # Remove an abandoned pending turn only when the inference lease has expired.
        conn.execute("DELETE FROM conversation_messages u WHERE conversation_id=%s AND role='user' AND NOT EXISTS (SELECT 1 FROM conversation_messages a WHERE a.conversation_id=u.conversation_id AND a.request_id=u.request_id AND a.role='assistant')", (conversation_id,))
        user_message = conn.execute(f"INSERT INTO conversation_messages(owner_id,project_id,conversation_id,request_id,role,content,request) VALUES(%s,%s,%s,%s,'user',%s,%s) RETURNING {MESSAGE_FIELDS}", (owner_id,project_id,conversation_id,body.request_id,question,Jsonb(request))).fetchone()
    try:
        last_result = next((item['result'] for item in previous if item['role']=='assistant'), None)
        last_request = next((item['request'] for item in previous if item['role']=='user'), None)
        scoped = turn_scope(body, last_result,last_request)
        previous_questions = [item['content'][:600] for item in reversed(previous) if item['role']=='user'][-3:]
        result = sanitize(jsonable_encoder(await answer_question(owner_id,str(project_id),scoped,conversation_context=previous_questions)))
        answer = result.get('answer','')[:12000]
        with database(owner_id) as conn:
            updated = conn.execute("UPDATE conversations SET pending_token=NULL,pending_until=NULL,updated_at=now(),title=CASE WHEN title='New investigation' THEN %s ELSE title END WHERE id=%s AND pending_token=%s RETURNING id", (question[:200],conversation_id,token)).fetchone()
            if not updated:
                raise HTTPException(409, 'The investigation changed while this answer was prepared. Reload and retry.')
            assistant_message = conn.execute(f"INSERT INTO conversation_messages(owner_id,project_id,conversation_id,request_id,role,content,result) VALUES(%s,%s,%s,%s,'assistant',%s,%s) RETURNING {MESSAGE_FIELDS}", (owner_id,project_id,conversation_id,body.request_id,answer,Jsonb(result))).fetchone()
        return {'user_message':user_message,'assistant_message':assistant_message,'result':result}
    except Exception as error:
        with database(owner_id) as conn:
            released = conn.execute('UPDATE conversations SET pending_token=NULL,pending_until=NULL WHERE id=%s AND pending_token=%s RETURNING id', (conversation_id,token)).fetchone()
            if released:
                conn.execute('DELETE FROM conversation_messages WHERE conversation_id=%s AND request_id=%s', (conversation_id,body.request_id))
        if isinstance(error, ModelUnavailable):
            raise HTTPException(503,str(error)) from None
        if isinstance(error, QueryError):
            raise HTTPException(422,str(error)) from None
        raise

@router.get('/projects/{project_id}/search')
def search(project_id: UUID, q: str = Query(default='',max_length=200), environment_id: UUID|None = None, owner_id: str = Depends(owner)):
    with database(owner_id) as conn:
        project_access(conn,project_id)
        if environment_id and not conn.execute('SELECT id FROM environments WHERE id=%s AND project_id=%s',(environment_id,project_id)).fetchone():
            raise HTTPException(404,'Environment not found.')
        clause='c.project_id=%s'
        params=[project_id]
        if environment_id:
            clause+=' AND c.environment_id=%s';params.append(environment_id)
        if q.strip():
            clause+=" AND (to_tsvector('simple',c.summary) @@ plainto_tsquery('simple',%s) OR c.service ILIKE %s OR c.summary ILIKE %s)";params.extend([redact_text(q),'%'+redact_text(q)+'%','%'+redact_text(q)+'%'])
        rows=conn.execute(f'SELECT c.id,e.name AS environment,c.source_id,s.source_project_id,s.connector,c.service,c.level,c.release,c.summary,c.event_count,c.bucket_start,c.bucket_end FROM summary_chunks c JOIN sources s ON s.id=c.source_id JOIN environments e ON e.id=c.environment_id WHERE {clause} ORDER BY c.bucket_start DESC LIMIT 100',params).fetchall()
        return {'evidence':sanitize(jsonable_encoder(rows))}

@router.get('/projects/{project_id}/coverage')
def coverage(project_id: UUID, owner_id: str = Depends(owner)):
    with database(owner_id) as conn:
        project_access(conn,project_id)
        rows=conn.execute('SELECT c.source_id,e.name AS environment,s.source_project_id,c.window_start,c.window_end,c.status,c.gap_reason,c.event_count FROM source_coverage c JOIN sources s ON s.id=c.source_id JOIN environments e ON e.id=c.environment_id WHERE c.project_id=%s ORDER BY c.window_start DESC LIMIT 200',(project_id,)).fetchall()
        return {'coverage':sanitize(jsonable_encoder(rows))}
