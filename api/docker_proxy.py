"""GET-only Docker proxy, enabled explicitly; no host port and no mutation route."""
import re
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

@app.get('/health')
def health(): return {'status':'ok'}

@app.get('/containers/{container}/{action}')
async def docker_get(container:str, action:str, request:Request):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',container) or action not in {'json','logs'}:
        raise HTTPException(404)
    allowed={'stdout','stderr','timestamps','since','until','follow'}
    params=dict(request.query_params)
    if set(params)-allowed or params.get('follow','0')!='0': raise HTTPException(400)
    client=httpx.AsyncClient(trust_env=False, transport=httpx.AsyncHTTPTransport(uds='/var/run/docker.sock'), timeout=30)
    try:
        response=await client.send(client.build_request('GET',f'http://docker/containers/{container}/{action}',params=params),stream=True)
        if response.status_code!=200:
            await response.aclose(); await client.aclose(); raise HTTPException(503,'Docker source unavailable')
        if action=='json':
            # Never forward container environment variables, labels or mounts (may contain secrets).
            data=__import__('json').loads(await response.aread())
            await response.aclose(); await client.aclose()
            return JSONResponse({'Id':data['Id'],'Name':data.get('Name'), 'State':{'Running':data.get('State',{}).get('Running')}})
        async def stream():
            try:
                async for chunk in response.aiter_bytes(): yield chunk
            finally:
                await response.aclose(); await client.aclose()
        return StreamingResponse(stream(),media_type='application/octet-stream')
    except HTTPException: raise
    except Exception:
        await client.aclose(); raise HTTPException(503,'Docker source unavailable') from None
