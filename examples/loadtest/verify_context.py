"""Supplement a running owned rehearsal with one cross-service context check."""
import argparse,json,pathlib,sqlite3,time,sys
import httpx

def main():
 p=argparse.ArgumentParser();p.add_argument('--run-dir',required=True,type=pathlib.Path);a=p.parse_args();root=a.run_dir;bindings=json.loads((root/'bindings-public.json').read_text());state=root/'state';deadline=time.time()+900
 while time.time()<deadline:
  connection=sqlite3.connect(f'file:{state}/rag.db?mode=ro',uri=True)
  payloads=[json.loads(r[0]) for r in connection.execute('SELECT payload FROM rag_chunks')];connection.close()
  if {'web','worker','auth'} <= {p['service'] for p in payloads}:break
  time.sleep(2)
 else:raise RuntimeError('three_sources_not_indexed')
 stamps=[json.loads(c['compact_evidence'])['records'][0]['timestamp'] for c in payloads]
 from datetime import datetime,timedelta
 body=dict(question='What context exists for INC_CORRELATION_73 across web, worker, and auth services?',environment_ids=[bindings['environment_id']],timezone='UTC',start=(min(datetime.fromisoformat(s) for s in stamps)-timedelta(seconds=2)).isoformat(),end=(max(datetime.fromisoformat(s) for s in stamps)+timedelta(seconds=2)).isoformat())
 started=time.monotonic()
 with httpx.Client(base_url='http://127.0.0.1:18960',trust_env=False,timeout=240,headers={'Authorization':'Bearer '+(state/'control-token').read_text().strip()}) as client:
  r=client.post('/projects/'+bindings['project_id']+'/ask',json=body)
 answer=r.json();evidence=answer.get('evidence',[]);selected=sorted({e['service'] for e in evidence});retained_incident_services=sorted({e['service'] for e in evidence if 'INC_CORRELATION_73' in json.dumps(e)})
 (root/'query-cross-service.json').write_text(json.dumps(answer,indent=2));result=dict(status=r.status_code,seconds=round(time.monotonic()-started,3),selected_services=selected,retained_incident_services=retained_incident_services,all_three_services_returned=set(retained_incident_services)=={'web','worker','auth'},source_scopes_valid=all(e['source_id'] in {s['source_id'] for s in bindings['sources']} and e['project_id']==bindings['project_id'] and e['environment_id']==bindings['environment_id'] for e in evidence),cited_ids_valid=set(answer.get('cited_evidence_ids',[]))=={e['id'] for e in evidence},evidence_count=len(evidence))
 (root/'cross-service-check.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));return 0 if result['all_three_services_returned'] and result['source_scopes_valid'] and result['cited_ids_valid'] else 2
if __name__=='__main__':sys.exit(main())
