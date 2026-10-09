from datetime import datetime, timezone
from uuid import uuid4

from api.workspace import Turn, turn_scope

def test_followup_inherits_previous_comparison_windows_without_changing_environment():
    environment = uuid4()
    body = Turn(question='What evidence supports that?',environment_ids=[environment],timezone='UTC')
    previous = {'plan':{'cells':[
        {'window':{'label':'current','start':'2026-09-24T00:00:00+00:00','end':'2026-10-01T00:00:00+00:00'}},
        {'window':{'label':'previous','start':'2026-08-24T00:00:00+00:00','end':'2026-08-31T00:00:00+00:00'}}]}}
    scoped=turn_scope(body,previous)
    assert scoped.start==datetime(2026,9,24,tzinfo=timezone.utc)
    assert scoped.compare_start==datetime(2026,8,24,tzinfo=timezone.utc)
    assert scoped.environment_ids==[environment]
    assert body.start is None

def test_explicit_new_time_request_does_not_reuse_previous_window():
    body=Turn(question='What changed today?',environment_ids=[uuid4()])
    previous={'plan':{'cells':[{'window':{'label':'current','start':'2026-08-24T00:00:00Z','end':'2026-08-31T00:00:00Z'}}]}}
    assert turn_scope(body,previous) is body

def test_named_and_numeric_time_windows_match_the_question():
    from pipeline.retrieval import resolve_windows
    now=datetime(2026,10,1,12,tzinfo=timezone.utc)
    for question,hours in [('What changed over 48 hours?',48),('What changed in the last 3 days?',72),('What happened yesterday?',24)]:
        body=Turn(question=question,environment_ids=[uuid4()],timezone='UTC')
        windows,_=resolve_windows(body,now)
        assert (windows[0]['end']-windows[0]['start']).total_seconds()==hours*3600
    yesterday,_=resolve_windows(Turn(question='What happened yesterday?',environment_ids=[uuid4()],timezone='UTC'),now)
    assert yesterday[0]['end']==datetime(2026,10,1,tzinfo=timezone.utc)

def test_followup_keeps_resolved_environment_but_respects_explicit_picker_change():
    dev,prod=uuid4(),uuid4()
    previous={'plan':{'cells':[{'id':str(dev)+':current','window':{'label':'current','start':'2026-09-24T00:00:00Z','end':'2026-10-01T00:00:00Z'}}]}}
    request={'environment_ids':[str(dev),str(prod)]}
    same=Turn(question='What supports that?',environment_ids=[dev,prod])
    assert turn_scope(same,previous,request).environment_ids==[dev]
    changed=Turn(question='What supports that?',environment_ids=[prod])
    assert turn_scope(changed,previous,request).environment_ids==[prod]

def test_followup_recognizes_all_resolver_relative_windows():
    previous={'plan':{'cells':[{'window':{'label':'current','start':'2026-08-24T00:00:00Z','end':'2026-08-31T00:00:00Z'}}]}}
    for phrase in ('past hour','this hour','48hours'):
        body=Turn(question='What changed in the '+phrase+'?',environment_ids=[uuid4()])
        assert turn_scope(body,previous) is body
