from datetime import datetime,timedelta,timezone

import pytest

from logchat.local.app import Question
from pipeline.retrieval import QueryError,resolve_windows


@pytest.mark.parametrize("question",["last 30 minutes","last30minutes","last 30-minute logs"])
def test_minutes_resolve_elapsed_time_across_dst(question):
    now=datetime(2026,11,1,6,10,tzinfo=timezone.utc)
    windows,_=resolve_windows(Question(question=question,environment_ids=["00000000-0000-0000-0000-000000000001"]),now=now)
    assert windows[0]["end"]==now
    assert windows[0]["end"]-windows[0]["start"]==timedelta(minutes=30)


def test_native_year_window_preserves_legacy_bound():
    body=Question(question="last365days",environment_ids=["00000000-0000-0000-0000-000000000001"])
    with pytest.raises(QueryError):resolve_windows(body)
    windows,_=resolve_windows(body,max_days=366)
    assert windows[0]["end"]-windows[0]["start"]==timedelta(days=365)
