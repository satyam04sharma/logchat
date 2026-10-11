"""Independent loopback HTTP workload producer; credentials arrive only on stdin."""
import json, os, random, sys, time
from datetime import datetime, timedelta
from pathlib import Path
import httpx

def write(path, value):
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(value,indent=2)); tmp.replace(path)

def event(config, index):
    dataset=config['dataset']; slot=((index//24)*997)%sum(t['weight'] for t in config['templates']); cumulative=0
    for template_index,template in enumerate(config['templates']):
        cumulative+=template['weight']
        if slot<cumulative:break
    message=template['message']
    incident=index in {0,10000,20000,30000}
    if incident:
        message={'web':'Upstream payment gateway connection timed out', 'worker':'Payment reconciliation job exhausted retry budget', 'auth':'Service credential denied after signing key rotation'}[config['app']]
    return dict(event_id=f"{config['run_id']}-{config['app']}-{index:06d}",
        timestamp=(datetime.fromisoformat(config['timestamp'])+timedelta(milliseconds=index%1000)).isoformat(),
        service=config['app'], level='error' if incident or any(term in message.lower() for term in ['error','fail','forbidden','denied','abnormally']) else 'info',
        message=message, fingerprint=f"{config['app']}-incident" if incident else f"{dataset}-{template_index}",
        release='loadtest-v1',duration_ms=5000 if incident else 10+index%90,
        request_status=504 if incident else 200,
        **({'incident_id':'INC_CORRELATION_73','error_code':config['app'].upper()+'_FAIL_73'} if incident else {}))

def main():
    config=json.loads(sys.stdin.readline()); root=Path(config['evidence']); root.mkdir(parents=True,exist_ok=True)
    progress=dict(app=config['app'],pid=os.getpid(),target=config['count'],accepted=0,generated=0,deduplicated=0,retries=0,batches=0,latencies=[],failures={})
    start=time.monotonic(); write(root/(config['app']+'-progress.json'),progress)
    with httpx.Client(base_url=config['url'],trust_env=False,timeout=330,headers={'Authorization':'Bearer '+config['token']}) as client:
        for offset in range(0,config['count'],config['batch']):
            if time.time()>config['deadline'] or (root/'stop-after-current-batch').exists(): break
            events=[event(config,i) for i in range(offset,min(offset+config['batch'],config['count']))]
            progress['generated']+=len(events)
            write(root/(config['app']+'-progress.json'),progress)
            body=json.dumps(dict(source_id=config['source_id'],events=events),separators=(',',':')).encode()
            if len(body)>256*1024: raise RuntimeError('request_bound_exceeded')
            attempts=0; batch_start=time.monotonic()
            while time.time()<config['deadline']:
                attempts+=1
                try:
                    response=client.post('/projects/'+config['project_id']+'/events',content=body,headers={'Content-Type':'application/json'})
                    if response.status_code==200:
                        receipt=response.json(); progress['accepted']+=receipt['accepted']; progress['deduplicated']+=receipt.get('deduplicated',0); progress['batches']+=1
                        progress['latencies'].append(round(time.monotonic()-batch_start,3)); break
                    category=response.headers.get('X-Logchat-Processing-Category','http_'+str(response.status_code))
                except httpx.HTTPError as error: category=type(error).__name__
                progress['retries']+=1; progress['failures'][category]=progress['failures'].get(category,0)+1
                progress['elapsed_seconds']=round(time.monotonic()-start,2); write(root/(config['app']+'-progress.json'),progress)
                if category not in {'intake_busy_retry_required','intake_backpressure_retry_required'} and attempts>=1: break
                time.sleep(random.uniform(1,3) if category=='intake_busy_retry_required' else min(15,attempts))
            progress['elapsed_seconds']=round(time.monotonic()-start,2); write(root/(config['app']+'-progress.json'),progress)
            if progress['accepted']<offset+len(events): break
    progress['complete']=progress['accepted']==config['count']; write(root/(config['app']+'-progress.json'),progress)
    return 0 if progress['complete'] else 2
if __name__=='__main__':sys.exit(main())
