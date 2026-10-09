import os
from contextlib import contextmanager
from uuid import UUID

import jwt
import psycopg
from psycopg.rows import dict_row
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer=HTTPBearer(auto_error=False)


def owner(request:Request, credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
    token=credentials.credentials if credentials else request.cookies.get('logchat_session')
    if not token: raise HTTPException(401,'Sign in to your local project first.')
    try:
        claims=jwt.decode(token,os.environ['JWT_SECRET'],algorithms=['HS256'],audience='authenticated',options={'require':['sub','exp','aud']})
        if claims.get('role')!='authenticated': raise ValueError()
        return str(UUID(claims['sub']))
    except Exception: raise HTTPException(401,'Local session expired. Sign in again.') from None


@contextmanager
def database(owner_id):
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as conn:
        conn.execute('SET LOCAL ROLE authenticated')
        conn.execute("SELECT set_config('request.jwt.claims', %s, true)",(__import__('json').dumps({'sub':owner_id,'role':'authenticated'}),))
        yield conn


def project_access(conn, project_id):
    row=conn.execute('SELECT * FROM projects WHERE id=%s',(project_id,)).fetchone()
    if not row: raise HTTPException(404,'Project not found.')
    return row
