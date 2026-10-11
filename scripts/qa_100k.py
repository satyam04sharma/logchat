"""Real native-model HTTP load rehearsal. Raw dataset content and tokens are never evidence."""
import argparse, asyncio, csv, hashlib, io, json, os, re, sqlite3, subprocess, sys, time, uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import httpx
LOGHUB_REVISION='dd61d0952749ee7963bde24220d1be5ede023033'
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

def save(path,value):
    tmp=path.with_suffix('.tmp'); tmp.write_text(json.dumps(value,indent=2)); tmp.replace(path)

def datasets(root):
    provenance=[]; result={}
    names=['Apache','Linux','OpenSSH']
    with httpx.Client(trust_env=False,timeout=60,follow_redirects=True) as client:
        revision=LOGHUB_REVISION
        for name in names:
            values={}
            for suffix in ['log_structured.csv','log_templates.csv']:
                url=f'https://raw.githubusercontent.com/logpai/loghub/{revision}/{name}/{name}_2k.{suffix}'
                response=client.get(url); response.raise_for_status(); rows=list(csv.DictReader(io.StringIO(response.text)))
                provenance.append(dict(dataset=name,url=url,sha256=hashlib.sha256(response.content).hexdigest(),bytes=len(response.content),rows=len(rows),revision=revision,raw_content_persisted=False))
                values[suffix]=rows
            # Replication is dataset-informed safe operational classes, not a claim of 100k source-unique records.
            frequencies=Counter(row['EventId'] for row in values['log_structured.csv'])
            provenance[-1]['sample_pattern_frequencies']=dict(frequencies)
            from pipeline.redaction import redact_text
            derived=[]
            for row in values['log_templates.csv']:
                text=redact_text(row['EventTemplate'])
                text=re.sub(r'<\*>','synthetic-value',text)
                text=re.sub(r'(?<![A-Za-z0-9])(?:/[^\s:]+)+','/synthetic/resource',text)
                text=re.sub(r'(?i)(for (?:invalid user )?)(?!synthetic-value)([A-Za-z][A-Za-z0-9_-]*)',r'\1synthetic-user',text)
                derived.append(dict(event_id=row['EventId'],message=text,weight=frequencies.get(row['EventId'],0)))
            result[name]=derived
            provenance[-1]['sanitized_replication_templates']=derived
    save(root/'dataset-provenance.json',dict(source='Loghub',source_unique_sample_rows=6000,generated_event_target=100000,replication='Frequency-weighted sanitized EventTemplate replication with synthetic placeholder values; original messages never copied',files=provenance))
    return result

def counts(state):
    connection=sqlite3.connect(f'file:{state}/rag.db?mode=ro',uri=True)
    chunks=[json.loads(r[0]) for r in connection.execute('SELECT payload FROM rag_chunks')]
    jobs=dict(connection.execute('SELECT state,COUNT(*) FROM rag_schedule_jobs GROUP BY state'))
    import sqlite_vec
    connection.enable_load_extension(True); sqlite_vec.load(connection); connection.enable_load_extension(False)
    vectors=connection.execute('SELECT COUNT(*) FROM rag_vectors').fetchone()[0]
    events=sum(c['metrics']['event_count'] for c in chunks)
    keys=connection.execute('SELECT COUNT(*) FROM rag_schedule_event_keys').fetchone()[0]
    digests=connection.execute('SELECT COUNT(*) FROM rag_schedule_event_digests').fetchone()[0]
    connection.close()
    return dict(indexed_events=events,chunks=len(chunks),vectors=vectors,event_keys=keys,event_digests=digests,jobs=jobs,versions=dict(Counter(c['compression_version'] for c in chunks)),models=dict(Counter(c['summary_model'] for c in chunks)))

def reconcile(state, configs):
    """Verify source metrics and exact IDs independently of query/model selection."""
    from logchat.rag.builder import event_key
    from logchat.rag.contracts import SourceIdentity
    from logchat.rag.scheduler import _identity_key
    db=sqlite3.connect(f'file:{state}/rag.db?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    results=[]
    for config in configs:
        rows=db.execute('SELECT payload FROM rag_chunks WHERE source_id=?',(config['source_id'],)).fetchall()
        values=[json.loads(row['payload']) for row in rows]
        duration=sum(chunk['metrics']['duration_sum_ms'] for chunk in values)
        statuses=Counter()
        for chunk in values:statuses.update(chunk['metrics']['status_counts'])
        size=config['count']; incidents={i for i in [0,10000,20000,30000] if i<size}
        expected_duration=sum(5000 if i in incidents else 10+i%90 for i in range(size))
        expected_statuses={'200':size-len(incidents),'504':len(incidents)}
        expected_ids={f"{config['run_id']}-{config['app']}-{i:06d}" for i in range(size)}
        metadata=[]
        for chunk in values:
            evidence=json.loads(chunk['compact_evidence'])
            metadata.extend(evidence['records'])
        observed_ids={record['event_id'] for record in metadata}
        identity=SourceIdentity('local-os-user',config['project_id'],config['environment_id'],config['source_id'])
        keys={event_key(identity,identifier) for identifier in expected_ids}
        observed_keys={row[0] for row in db.execute('SELECT event_key FROM rag_schedule_event_keys WHERE scope=?',(_identity_key(identity),))}
        scope_valid=all(chunk['identity']==dict(owner_id=identity.owner_id,project_id=identity.project_id,environment_id=identity.environment_id,source_id=identity.source_id) for chunk in values)
        result=dict(app=config['app'],expected_events=size,indexed_events=sum(c['metrics']['event_count'] for c in values),metadata_records=len(metadata),unique_metadata_ids=len(observed_ids),missing_ids=len(expected_ids-observed_ids),unexpected_ids=len(observed_ids-expected_ids),scope_valid=scope_valid,event_keys_match=bool(keys) and keys==observed_keys,duration_sum_ms=duration,expected_duration_sum_ms=expected_duration,status_counts=dict(statuses),expected_status_counts=expected_statuses)
        result['passed']=result['indexed_events']==size and len(metadata)==size and observed_ids==expected_ids and result['event_keys_match'] and scope_valid and duration==expected_duration and dict(statuses)==expected_statuses
        results.append(result)
    db.close()
    return dict(passed=all(result['passed'] for result in results),sources=results)

def context_quality(state, configs, label, answer, service=None):
    """Score seeded incident support against durable IDs, not model confidence."""
    evidence=answer.get('evidence',[])
    if label in {'negative','empty environment','empty control project'}:
        return dict(passed=not evidence,false_positive_memories=len(evidence),expected_empty=True)
    expected_ids=set()
    for config in configs:
        if service and config['app']!=service:continue
        expected_ids.update(f"{config['run_id']}-{config['app']}-{index:06d}" for index in [0,10000,20000,30000] if index<config['count'])
    with sqlite3.connect(f'file:{state}/rag.db?mode=ro',uri=True) as db:
        chunks=[json.loads(row[0]) for row in db.execute('SELECT payload FROM rag_chunks')]
    relevant={chunk['chunk_id'] for chunk in chunks if any(record['event_id'] in expected_ids for record in json.loads(chunk['compact_evidence'])['records'])}
    selected=[item['chunk_id'] for item in evidence]
    hits=[identifier in relevant for identifier in selected]
    precision=sum(hits)/len(hits) if hits else 0.0
    recall=len(set(selected)&relevant)/len(relevant) if relevant else 0.0
    expected_services={service} if service else {config['app'] for config in configs}
    observed_services={item.get('service') for item in evidence}
    source_coverage=len(expected_services&observed_services)/len(expected_services)
    return dict(passed=bool(evidence) and precision==1 and source_coverage==1,
        selected_memory_precision=precision,selected_memory_recall=recall,
        reciprocal_rank=next((1/(index+1) for index,hit in enumerate(hits) if hit),0.0),
        relevant_memories=len(relevant),selected_memories=len(selected),source_coverage=source_coverage,
        interpretation='Seeded incident support only; bounded selection recall is reported, not a global RAGAS or entailment score')

def main():
    p=argparse.ArgumentParser(); p.add_argument('--run-dir',required=True,type=Path); p.add_argument('--profile',required=True,type=Path); p.add_argument('--count',type=int,default=100000); p.add_argument('--minutes',type=float,default=90); p.add_argument('--smoke',type=int,default=48); p.add_argument('--batch',type=int,default=48); p.add_argument('--capture-burst',action='store_true'); p.add_argument('--drain-seconds',type=int,default=180); p.add_argument('--port',type=int,default=18960); a=p.parse_args()
    if not 3 <= a.count <= 100000 or not 1 <= a.batch <= 1000 or not 1 <= a.smoke <= a.count//3:
        raise ValueError('choose_bounded_count_batch_and_smoke')
    root=a.run_dir.resolve(); root.mkdir(parents=True,exist_ok=True,mode=0o700); state=root/'state'; state.mkdir(mode=0o700)
    if (state/'local.db').exists():raise RuntimeError('fresh_state_required')
    import psutil
    save(root/'host-resource-snapshot.json',dict(cpu_percent=psutil.cpu_percent(interval=1),memory_available_bytes=psutil.virtual_memory().available,memory_total_bytes=psutil.virtual_memory().total,load_average=os.getloadavg(),existing_native_service_count=sum('logchat.local.server' in ' '.join(proc.info.get('cmdline') or []) for proc in psutil.process_iter(['cmdline']))))
    templates=datasets(root); profile=json.loads(a.profile.read_text())
    from logchat.local.rag_runtime import configure
    asyncio.run(configure(state,base_url=profile['base_url'],model=profile['embedding']['model'],dimensions=profile['embedding']['dimensions'],chat_model=profile['generation_model'],content_policy=profile['content_policy']))
    env=dict(os.environ,PYTHONPATH=str(ROOT/'src'),LOGCHAT_CAPTURE_HOST='0',LOGCHAT_LOCAL_DIR=str(state),LOGCHAT_SECRETS_DIR=str(root/'credentials'))
    server=subprocess.Popen([sys.executable,'-m','logchat.local.server','--state-dir',str(state),'--port',str(a.port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    import logchat, pipeline.models
    save(root/'runtime-provenance.json',dict(harness_root=str(ROOT),harness_cwd=os.getcwd(),logchat_module=logchat.__file__,pipeline_models_module=pipeline.models.__file__,server_cwd=str(ROOT),explicit_pythonpath=str(ROOT/'src')))
    save(root/'processes.json',dict(server_pid=server.pid,producer_pids=[],port=a.port,loopback=True,host_capture=False))
    deadline=time.time()+a.minutes*60; run_id=uuid.uuid4().hex[:8]; timestamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat(); configs=[]; producers=[]
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{a.port}',trust_env=False,timeout=330) as client:
            for _ in range(120):
                try:
                    if client.get('/health/ready').status_code==200:break
                except httpx.HTTPError:pass
                if server.poll() is not None:raise RuntimeError('server_exited')
                time.sleep(.5)
            client.headers['Authorization']='Bearer '+(state/'control-token').read_text().strip()
            def request(method,path,body=None):
                r=client.request(method,path,json=body); r.raise_for_status(); return r.json()
            project=request('POST','/projects',dict(name='100K multi application rehearsal',path=str(root/'applications'))); pid=project['id']
            request('POST',f'/projects/{pid}/environments',dict(name='prod'))
            request('POST',f'/projects/{pid}/environments',dict(name='preview'))
            if a.capture_burst:
                request('PUT','/settings/capture',dict(mode='retain_until_summarized',max_bytes=512*1024*1024,retention_seconds=86400))
            other=request('POST','/projects',dict(name='Isolation control project',path=str(root/'control')))
            envs=request('GET',f'/projects/{pid}/environments'); prod=next(e['id'] for e in envs if e['name']=='prod'); preview=next(e['id'] for e in envs if e['name']=='preview')
            for number,(app,dataset) in enumerate([('web','Apache'),('worker','Linux'),('auth','OpenSSH')]):
                source=request('POST',f'/projects/{pid}/sources',dict(name=app+' simulator',environment='prod',kind='push'))
                configs.append(dict(app=app,dataset=dataset,templates=templates[dataset],project_id=pid,environment_id=prod,source_id=source['id'],token=source['token'],url=str(client.base_url).rstrip('/'),evidence=str(root),run_id=run_id,timestamp=timestamp,deadline=deadline,batch=a.batch,count=a.count//3+(number<a.count%3)))
            wrong_token=client.post(f'/projects/{pid}/events',json=dict(source_id=configs[1]['source_id'],events=[dict(message='Synthetic wrong-source scope check',event_id='isolation-probe')]),headers={'Authorization':'Bearer '+configs[0]['token']})
            save(root/'source-token-isolation.json',dict(status=wrong_token.status_code,passed=wrong_token.status_code==401))
            save(root/'bindings-public.json',dict(run_id=run_id,timestamp=timestamp,project_id=pid,other_project_id=other['id'],environment_id=prod,preview_environment_id=preview,sources=[{k:c[k] for k in ['app','source_id','dataset','count']} for c in configs]))
            from importlib.util import spec_from_file_location,module_from_spec
            spec=spec_from_file_location('producer',ROOT/'examples/loadtest/producer.py'); module=module_from_spec(spec); spec.loader.exec_module(module)
            # Smoke uses the same exact workload events; producer replay will deduplicate these.
            start=time.monotonic(); smoke_events=[module.event(configs[0],i) for i in range(a.smoke)]
            r=client.post(f'/projects/{pid}/events',json=dict(source_id=configs[0]['source_id'],events=smoke_events),headers={'Authorization':'Bearer '+configs[0]['token']})
            smoke=dict(status=r.status_code,elapsed_seconds=round(time.monotonic()-start,3),category=r.headers.get('X-Logchat-Processing-Category'),phase=r.headers.get('X-Logchat-Processing-Phase'),events=a.smoke,mode='temporary_original_admission' if a.capture_burst else 'native_compact_semantic')
            if r.status_code==200:smoke['receipt']=r.json()
            smoke['projected_100k_hours']=None if a.capture_burst else round(smoke['elapsed_seconds']/a.smoke*100000/3600,2)
            save(root/'smoke.json',smoke); print(json.dumps(dict(smoke=smoke)),flush=True)
            if r.status_code!=200:raise RuntimeError('real_model_smoke_failed')
            producers=[]
            for config in configs:
                child=subprocess.Popen([sys.executable,str(ROOT/'examples/loadtest/producer.py')],cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,text=True)
                child.stdin.write(json.dumps(config)+'\n'); child.stdin.close(); producers.append(child)
            save(root/'processes.json',dict(server_pid=server.pid,producer_pids=[c.pid for c in producers],port=a.port,loopback=True,host_capture=False))
            while any(c.poll() is None for c in producers) and time.time()<deadline:
                snapshot=counts(state); save(root/'index-progress.json',snapshot); print(json.dumps(snapshot),flush=True); time.sleep(30)
            for c in producers:
                if c.poll() is None:c.terminate()
            for c in producers:
                try:c.wait(timeout=10)
                except subprocess.TimeoutExpired:c.kill(); c.wait()
            drain=time.time()+a.drain_seconds
            while time.time()<drain:
                snapshot=counts(state)
                if a.capture_burst:
                    capture_status=request('GET',f'/projects/{pid}/status')['rag']['capture']
                    save(root/'capture-drain-progress.json',dict(counts=snapshot,capture=capture_status,observed_unix=time.time(),seconds_since_first_admission=round(time.time()-capture_status['oldest_captured_at'],3) if capture_status['oldest_captured_at'] else None))
                    if snapshot['indexed_events']==a.count and not capture_status['pending_events'] and not any(k!='completed' for k in snapshot['jobs']):break
                    if capture_status['pending_failed_events']:break
                elif not any(k!='completed' for k in snapshot['jobs']):break
                time.sleep(2)
            results=[]
            queries=[('cross service incident','What context exists for INC_CORRELATION_73 across web, worker, and auth services?',prod,None),('exact incident','INC_CORRELATION_73',prod,None),('semantic paraphrase','Which services saw payment requests fail because a gateway did not respond?',prod,'web'),('worker scope','INC_CORRELATION_73',prod,'worker'),('auth scope','INC_CORRELATION_73',prod,'auth'),('empty environment','INC_CORRELATION_73',preview,None),('negative','QUANTUM_NONEXISTENT_92731',prod,None)]
            other_env=request('GET',f"/projects/{other['id']}/environments")[0]['id']
            queries.append(('empty control project','INC_CORRELATION_73',other_env,None))
            if a.capture_burst and counts(state)['indexed_events']!=a.count:queries=[]
            for label,question,eid,service in queries:
                start=time.monotonic(); body=dict(question=question,environment_ids=[eid],timezone='UTC',start=(datetime.fromisoformat(timestamp)-timedelta(seconds=2)).isoformat(),end=(datetime.fromisoformat(timestamp)+timedelta(seconds=3)).isoformat())
                if service:body['service']=service
                try:
                    query_pid=other['id'] if label=='empty control project' else pid
                    answer=request('POST',f'/projects/{query_pid}/ask',body); evidence=answer.get('evidence',[])
                    db=sqlite3.connect(f'file:{state}/rag.db?mode=ro',uri=True); existing_ids={row[0] for row in db.execute('SELECT chunk_id FROM rag_chunks')}; db.close()
                    citations_valid=all(c['id'] in existing_ids and c['id'] in {e['id'] for e in evidence} for c in answer.get('citations',[])) and set(answer.get('cited_evidence_ids',[]))=={e['id'] for e in evidence} and (not evidence or bool(answer.get('citations'))) 
                    valid_scope=all(e['project_id']==query_pid and e['environment_id']==eid and (not service or e.get('service')==service) for e in evidence)
                    save(root/('query-'+label.replace(' ','-')+'.json'),answer)
                    results.append(dict(query=label,seconds=round(time.monotonic()-start,3),evidence_count=len(evidence),scope_valid=valid_scope,citations_valid=citations_valid,incident_found='INC_CORRELATION_73' in json.dumps(evidence),empty_expected=label in {'empty environment','negative','empty control project'},status='passed' if valid_scope and citations_valid and (label not in {'exact incident','worker scope','auth scope'} or 'INC_CORRELATION_73' in json.dumps(evidence)) and (label!='cross service incident' or {e.get('service') for e in evidence}=={'web','worker','auth'}) and (bool(evidence) if label not in {'empty environment','negative','empty control project'} else not evidence) else 'failed'))
                    quality=context_quality(state,configs,label,answer,service)
                    results[-1]['context_quality']=quality
                    if not quality['passed']:results[-1]['status']='failed'
                except Exception as error:results.append(dict(query=label,status='failed',error_category=type(error).__name__))
            duplicate=client.post(f'/projects/{pid}/events',json=dict(source_id=configs[0]['source_id'],events=smoke_events),headers={'Authorization':'Bearer '+configs[0]['token']}); replay=dict(status=duplicate.status_code,receipt=duplicate.json() if duplicate.status_code==200 else None)
            if a.capture_burst:
                from logchat.local.raw_capture import CaptureSpool
                before_restart=CaptureSpool(state).status(pid)
                server.terminate(); server.wait(timeout=20)
                stopped_counts=CaptureSpool(state).status(pid)
                server=subprocess.Popen([sys.executable,'-m','logchat.local.server','--state-dir',str(state),'--port',str(a.port)],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                for attempt in range(120):
                    try:
                        if client.get('/health/ready').status_code==200:break
                    except httpx.HTTPError:pass
                    time.sleep(.5)
                after_restart=request('GET',f'/projects/{pid}/status')['rag']['capture']
                replay_response=client.post(f'/projects/{pid}/events',json=dict(source_id=configs[0]['source_id'],events=smoke_events),headers={'Authorization':'Bearer '+configs[0]['token']})
                after_replay=request('GET',f'/projects/{pid}/status')['rag']['capture']
                save(root/'restart-durability.json',dict(before=before_restart,stopped=stopped_counts,after=after_restart,after_replay=after_replay,replay_status=replay_response.status_code,replay_receipt=replay_response.json() if replay_response.status_code==200 else None,accepted_stable=all(x['accepted_events']==a.count for x in [before_restart,stopped_counts,after_restart,after_replay]),accounting_valid=after_replay['pending_events']+after_replay['completed_events']+after_replay['expired_events']==a.count,no_drops=after_replay['dropped_events']==0))
                save(root/'processes.json',dict(server_pid=server.pid,producer_pids=[c.pid for c in producers],port=a.port,loopback=True,host_capture=False,restart_verified=True))
            progress=[json.loads((root/(c['app']+'-progress.json')).read_text()) for c in configs]
            snapshot=counts(state); final=dict(target=a.count,counts=snapshot,accepted=sum(p['accepted'] for p in progress),deduplicated=sum(p['deduplicated'] for p in progress),generated_unique_events=sum(p['generated'] for p in progress),acknowledged_new_producer_events=sum(p['accepted']-p['deduplicated'] for p in progress),smoke_acknowledged_events=a.smoke,complete=snapshot['indexed_events']==a.count,smoke=smoke,queries=results,duplicate_replay=replay,producer_results=progress,test_mode='temporary_original_admission_burst' if a.capture_burst else 'native_compact_semantic',runtime_status=request('GET',f'/projects/{pid}/status'),limitations=['Frequency-weighted sanitized Loghub EventTemplate replication; 6000 original samples inspected, not 100000 source-unique logs','Single machine loopback deployment','No remote or production deployment','Query evidence is selected subset; exact corpus totals independently read from durable native database'])
            capture=final['runtime_status']['rag']['capture']
            final['admission_complete']=capture['accepted_events']==a.count if a.capture_burst else final['accepted']==a.count
            final['rag_complete']=final['complete']
            final['generation_complete']=final['generated_unique_events']==a.count
            final['retrieval_evaluation_complete']=bool(results) and all(q['status']=='passed' for q in results)
            final['exact_corpus_accounting']=reconcile(state,configs)
            final['restart_verified']=not a.capture_burst or all(json.loads((root/'restart-durability.json').read_text())[key] for key in ['accepted_stable','accounting_valid','no_drops'])
            final['source_auth_isolation']=wrong_token.status_code==401
            final['replay_verified']=replay['status']==200 and replay['receipt']['deduplicated']==a.smoke
            final['no_pending_or_failed_work']=not capture['pending_events'] and not capture['pending_failed_events'] and not any(k!='completed' for k in snapshot['jobs'])
            final['acceptance_passed']=all(final[key] for key in ['admission_complete','rag_complete','generation_complete','retrieval_evaluation_complete','restart_verified','source_auth_isolation','replay_verified','no_pending_or_failed_work']) and final['exact_corpus_accounting']['passed']
            save(root/'report.json',final); print(json.dumps(dict(complete=final['complete'],counts=snapshot)),flush=True)
            return 0 if final['acceptance_passed'] else 2
    except Exception as error:
        save(root/'failure.json',dict(error_category=type(error).__name__,counts=counts(state) if (state/'rag.db').exists() else None)); raise
    finally:
        import signal
        for child in producers:
            if child.poll() is None:
                child.send_signal(signal.SIGCONT); child.terminate()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill(); child.wait()
        server.terminate()
        try:server.wait(timeout=20)
        except subprocess.TimeoutExpired:server.kill();server.wait()
        save(root/'cleanup.json',dict(owned_server_stopped=server.poll() is not None,server_exit_code=server.returncode,owned_producers_stopped=all(c.poll() is not None for c in producers),producer_exit_codes=[c.returncode for c in producers]))
if __name__=='__main__':sys.exit(main())
