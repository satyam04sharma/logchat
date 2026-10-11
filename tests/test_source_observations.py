"""Pure deterministic controls; no provider, intake, or historical state calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from logchat.rag.source_observations import (
    select_observations, validate_observations, render_observations, observation_embedding,
)


def fixture(messages, *, statuses=None, durations=None):
    first = datetime(2026, 10, 10, tzinfo=timezone.utc)
    records = [{"ref": f"e{i}", "timestamp": (first + timedelta(seconds=i * 100)).isoformat(),
                "duration_ms": durations[i] if durations is not None else None,
                "request_status": statuses[i] if statuses is not None else None}
               for i in range(len(messages))]
    views = {row["ref"]: text for row, text in zip(records, messages)}
    return records, views, {ref: [text] for ref, text in views.items()}


def select(data, phrases, fields=None, **kwargs):
    return select_observations({"source_phrases": phrases, "important_fields": fields or []},
                               *data, ["f0", "f1"], output_budget=kwargs.get("budget", 8000))[0]


def phrase(ref, text):
    return {"record_ref": ref, "text": text}


@pytest.mark.parametrize("a,b", [(11, 24), (24, 11)])
def test_exact_mixed_occurrences_and_once_per_record(a, b):
    data = fixture(["workerEnv.init() workerEnv.init() completed extra"] * a
                   + ["mod_jk child init completed extra"] * b)
    observations = select(data, [phrase("e0", "workerEnv.init()"), phrase(f"e{a}", "mod_jk child init")])
    assert [item["exact_metrics"]["event_count"] for item in observations] == [a, b]
    assert observations[0]["event_refs"] == [f"e{i}" for i in range(a)]
    assert observations[1]["event_refs"] == [f"e{i}" for i in range(a, a + b)]
    rendered = render_observations(observations, data[0], [])
    assert f'{a} retained events contain source phrase "workerEnv.init()"' in rendered
    assert f'{b} retained events contain source phrase "mod_jk child init"' in rendered


def test_incident_and_directory_measurements_bind_to_members():
    data = fixture(["incident request timed out extra"] + ["directory index served extra"] * 32,
                   statuses=[504] + [200] * 32, durations=[5000] + [None] * 32)
    observations = select(data, [phrase("e0", "request timed out"), phrase("e1", "directory index")])
    assert observations[0]["exact_metrics"] == dict(event_count=1, duration_count=1,
        duration_sum_ms=5000.0, duration_min_ms=5000.0, duration_max_ms=5000.0, status_counts={"504": 1})
    assert observations[1]["exact_metrics"] == dict(event_count=32, duration_count=0,
        duration_sum_ms=0.0, duration_min_ms=None, duration_max_ms=None, status_counts={"200": 32})


def test_overlap_numbers_and_source_cause_are_only_quoted():
    data = fixture(["extra auth 35 thirty-two status504 caused recovery extra",
                    "extra auth 35 thirty-two status504 caused recovery extra"])
    observations = select(data, [phrase("e0", "auth 35 thirty-two status504"),
                                 phrase("e1", "status504 caused recovery")])
    assert [item["event_refs"] for item in observations] == [["e0", "e1"], ["e0", "e1"]]
    rendered = render_observations(observations, data[0], [])
    assert '2 retained events contain source phrase "auth 35 thirty-two status504"' in rendered
    assert '"status504 caused recovery" (source quote).' in rendered
    assert "Memberships may overlap; do not add counts." in rendered


def test_separated_observations_span_work_unknowns_and_typed_fields():
    data = fixture(["auth operation extra", "other extra", "auth operation extra"],
                   durations=[5, 999, 7], statuses=[200, 500, None])
    observations = select(data, [phrase("e0", "auth operation")])
    assert observations[0]["exact_metrics"]["duration_sum_ms"] == 12
    assert observations[0]["observed_window"]["span_ms"] == 200000
    fields = [{"key": "/custom/alias", "value": [False, 7.0, None, {"a": "007"}], "event_refs": ["e0"]}]
    rendered = render_observations(observations, data[0], fields)
    embedding = observation_embedding(observations, data[0], fields)
    assert embedding['important_fields'] == [{'key': '/custom/alias', 'value': [False, 7.0, None, {'a': '007'}]}]
    assert "missing measurements are unknown" in embedding['measurement_notice']
    assert "not elapsed time" in embedding['measurement_notice']
    assert embedding['source_observations'][0]['exact_metrics'] == observations[0]['exact_metrics']
    assert embedding['source_observations'][0]['observed_window'] == observations[0]['observed_window']
    assert "consecutive" not in rendered


@pytest.mark.parametrize("phrases", [
    [phrase("e99", "auth")], [phrase("e0", "Auth")], [phrase("e0", "absent")],
    [phrase("e0", "auth"), phrase("e1", "auth")], [phrase("e0", " ")],
    [phrase("e0", 3)], [phrase(0, "auth")], [phrase("e0", "x" * 121)],
    [{"record_ref": "e0", "text": "auth", "count": 17}],
    [phrase("e0", "auth")] * 5, "auth", None,
])
def test_invalid_phrases_reject(phrases):
    with pytest.raises(ValueError):
        select(fixture(["auth operation extra", "auth operation extra"]), phrases)


@pytest.mark.parametrize("change", [
    {"summary": "17 events"}, {"uncertainties": ["thirty-two failures"]},
    {"source_phrases": []}, {"important_fields": ["f0", "f0"]},
    {"important_fields": ["f9"]}, {"important_fields": [False]},
    {"important_fields": "f0"},
])
def test_full_response_schema_and_duplicate_fields_reject(change):
    value = {"source_phrases": [phrase("e0", "auth")], "important_fields": []}
    if change == {"source_phrases": []}:
        del value["important_fields"]
    else:
        value.update(change)
    with pytest.raises(ValueError):
        select_observations(value, *fixture(["auth operation extra"]), ["f0"], output_budget=8000)


def test_output_overflow_and_source_scope_reject():
    data = fixture(["auth operation extra"])
    with pytest.raises(ValueError):
        select(data, [phrase("e0", "auth")], budget=10)
    with pytest.raises(ValueError, match="scope"):
        select((data[0], {"e1": "auth"}, data[2]), [phrase("e0", "auth")])


@pytest.mark.parametrize("phrases", [
    [phrase("e0", "auth operation")],
    [phrase("e0", "auth "), phrase("e0", "operation")],
    [phrase("e0", "auth oper"), phrase("e0", "operation")],
])
def test_whole_producer_body_and_union_reconstruction_reject(phrases):
    data = fixture(["auth operation extra canonical fields"])
    data[2]["e0"].append("auth operation")
    with pytest.raises(ValueError, match="body"):
        select(data, phrases)


def test_nonwitness_body_privacy_and_repeated_body_coverage():
    data = fixture(["auth operation extra", "auth operation"])
    with pytest.raises(ValueError, match="body"):
        select(data, [phrase("e0", "auth operation")])
    data = fixture(["prefix abcabc suffix"])
    data[2]["e0"].append("abcabc")
    with pytest.raises(ValueError, match="reconstruction"):
        select(data, [phrase("e0", "abc")])


@pytest.mark.parametrize("separator", [" ", "\t", "\n", " \t\n ", ",", " / ", "—", "…", "()"])
@pytest.mark.parametrize("body_surface", ["current_view", "canonical", "producer", "projection"])
def test_cross_record_separator_reconstruction_rejects_every_body_surface(separator, body_surface):
    body = f"auth{separator}operation"
    data = fixture(["auth extra", "operation extra", body if body_surface == "current_view" else "other extra"])
    if body_surface != "current_view":
        data[2]["e2"].append(body)
    with pytest.raises(ValueError, match="reconstruction"):
        select(data, [phrase("e0", "auth"), phrase("e1", "operation")])


@pytest.mark.parametrize("body,texts", [
    ("auth auth, operation operation", ["auth", "operation"]),
    ("abcde, abcde", ["abc", "cde"]),
])
def test_cross_record_repeated_and_overlapping_separator_reconstruction_rejects(body, texts):
    data = fixture([f"{text} extra" for text in texts] + [body])
    with pytest.raises(ValueError, match="reconstruction"):
        select(data, [phrase(f"e{i}", text) for i, text in enumerate(texts)])


@pytest.mark.parametrize("omitted", [" omitted ", "7", "+", "=", "\u0301"])
def test_content_bearing_gap_permits_fragments_without_normalizing_membership(omitted):
    data = fixture(["auth extra", "operation extra", f"auth{omitted}operation"])
    observations = select(data, [phrase("e0", "auth"), phrase("e1", "operation")])
    assert [item["text"] for item in observations] == ["auth", "operation"]
    assert [item["event_refs"] for item in observations] == [["e0", "e2"], ["e1", "e2"]]


def test_privacy_separator_rule_does_not_normalize_exact_occurrences():
    data = fixture(["auth operation extra", "auth\toperation extra", "auth—operation extra"])
    observations = select(data, [phrase("e0", "auth operation")])
    assert observations[0]["text"] == "auth operation"
    assert observations[0]["event_refs"] == ["e0"]


def test_separator_only_body_without_matching_phrases_does_not_reject_selection():
    data = fixture(["auth extra", " , \t "])
    assert select(data, [phrase("e0", "auth")])[0]["event_refs"] == ["e0"]
    assert select(data, []) == []


@pytest.mark.parametrize("mutation", ["count", "status", "duration", "span", "membership", "duplicate", "witness", "extra"])
def test_durable_fact_tampering_reject(mutation):
    data = fixture(["auth operation extra", "auth operation extra"], statuses=[200, 504], durations=[5, 7])
    observations = deepcopy(select(data, [phrase("e0", "auth")]))
    item = observations[0]
    if mutation == "count":
        item["exact_metrics"]["event_count"] = 17
    elif mutation == "status":
        item["exact_metrics"]["status_counts"] = {"200": 2}
    elif mutation == "duration":
        item["exact_metrics"]["duration_sum_ms"] = 200000
    elif mutation == "span":
        item["observed_window"]["span_ms"] = 12
    elif mutation == "membership":
        item["event_refs"] = ["e0"]
    elif mutation == "duplicate":
        item["event_refs"] = ["e0", "e0", "e1"]
    elif mutation == "witness":
        item["record_ref"] = "e99"
    else:
        item["cause"] = "inferred"
    with pytest.raises(ValueError):
        validate_observations(observations, data[0])


def test_source_discard_and_empty_selection_are_honest():
    data = fixture(["auth operation extra"])
    observations = select(data, [phrase("e0", "auth")])
    assert "extra" not in str(observations)
    data[1].clear()
    data[2].clear()
    validate_observations(observations, data[0])
    rendered = render_observations([], data[0], [])
    assert "before source disposal" in rendered
    assert "source-derived operation counts are unavailable" in rendered
    assert "omitted source details cannot be recovered" in rendered


@pytest.mark.parametrize('unit', ['x', 'λ', '😀'])
def test_maximal_phrases_records_and_fields_fit_escaped_summary(unit):
    import json

    texts = [str(i) + unit * 119 for i in range(4)]
    data = fixture(['prefix ' + ' '.join(texts) + ' discarded'] * 500)
    observations = select(data, [phrase('e0', text) for text in texts])
    fields = [{'key': f'field{i}', 'value': 'v' * 1900, 'event_refs': ['e0']}
              for i in range(64)]
    summary = render_observations(observations, data[0], fields)
    assert len(json.dumps(summary, ensure_ascii=False, separators=(',', ':'))) <= 1600
    assert render_observations(observations, data[0], []) == summary
    assert all(item['event_refs'] == [f'e{i}' for i in range(500)] for item in observations)
    transport = observation_embedding(observations, data[0], fields)
    assert transport['important_fields'] == [{'key': item['key'], 'value': item['value']} for item in fields]
    assert transport['whole_section_facts']['exact_metrics']['event_count'] == 500
    for original, embedded in zip(observations, transport['source_observations']):
        assert embedded == {key: original[key] for key in ('text', 'exact_metrics', 'observed_window')}


@pytest.mark.parametrize('unit', ['"', '\\', '\n', '\t', '\x01'])
def test_escape_overflow_fails_without_truncation(unit):
    import json

    texts = [str(i) + unit * 119 for i in range(4)]
    data = fixture(['prefix ' + ' '.join(texts) + ' discarded'])
    observations = select(data, [phrase('e0', text) for text in texts])
    before = deepcopy(observations)
    # Each phrase obeys the Unicode cap but its JSON quote needs escaping again
    # in the summary envelope. The full maximal selection must fail closed.
    assert all(len(text) == 120 for text in texts)
    for render in (render_observations, observation_embedding):
        with pytest.raises(ValueError, match='source_observation_summary_budget_exceeded'):
            render(observations, data[0], [])
    assert observations == before
    single = render_observations(observations[:1], data[0], [])
    assert len(json.dumps(single, ensure_ascii=False)) <= 1600
    assert json.dumps(texts[0], ensure_ascii=False) in single


def test_embedding_preserves_distinct_subset_facts_and_out_of_order_window():
    data = fixture(['incident 504 timed out extra'] + ['directory index served extra'] * 32,
                   statuses=[504] + [200] * 32, durations=[5000] + [None] * 32)
    data[0][0]['timestamp'], data[0][-1]['timestamp'] = data[0][-1]['timestamp'], data[0][0]['timestamp']
    observations = select(data, [phrase('e0', 'incident 504'), phrase('e1', 'directory index')])
    transport = observation_embedding(observations, data[0], [])
    incident, directory = transport['source_observations']
    assert incident['exact_metrics']['event_count'] == 1
    assert incident['exact_metrics']['status_counts'] == {'504': 1}
    assert incident['exact_metrics']['duration_sum_ms'] == 5000
    assert incident['observed_window']['span_ms'] == 0
    assert directory['exact_metrics']['event_count'] == 32
    assert directory['exact_metrics']['status_counts'] == {'200': 32}
    assert directory['exact_metrics']['duration_min_ms'] is None
    assert directory['observed_window']['span_ms'] == 3100000
    assert transport['whole_section_facts']['observed_window']['span_ms'] == 3200000
    assert transport['whole_section_facts']['exact_metrics']['duration_sum_ms'] == 5000
    assert 'event_refs' not in str(transport) and 'record_ref' not in str(transport)
    empty = observation_embedding([], data[0], [])
    assert empty['source_observations'] == []
    assert empty['whole_section_facts'] == transport['whole_section_facts']
    assert 'source-derived operation counts are unavailable' in empty['summary']
