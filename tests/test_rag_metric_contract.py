import pytest

from logchat.rag.contracts import ExactMetrics


@pytest.mark.parametrize("values", [
    {"event_count":1,"duration_sum_ms":4},
    {"event_count":1,"duration_count":1,"duration_sum_ms":4},
    {"event_count":1,"duration_count":1,"duration_sum_ms":4,"duration_min_ms":float("nan"),"duration_max_ms":4},
    {"event_count":1,"duration_count":1,"duration_sum_ms":4,"duration_min_ms":5,"duration_max_ms":4},
    {"event_count":1,"status_counts":{"not-a-status":1}},
])
def test_corrupt_persisted_metrics_are_rejected(values):
    with pytest.raises(ValueError):
        ExactMetrics(**values)
