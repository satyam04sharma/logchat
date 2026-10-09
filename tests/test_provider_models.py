import json
import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import httpx

from pipeline.models import LocalModels, ModelUnavailable, OpenAICompatibleModels, validate_endpoint
from pipeline.types import LogEvent


SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}


class ProviderModelTests(unittest.IsolatedAsyncioTestCase):
    def events(self, count, message, **fields):
        return [LogEvent(str(i), datetime(2026, 10, 3, tzinfo=timezone.utc), "fake",
                         fields.get("service", "api"), fields.get("level", "error"),
                         message, "pattern", release=fields.get("release"),
                         duration_ms=i, request_status=500 if i % 2 else 200)
                for i in range(count)]

    def configured_models(self, handler):
        transport = httpx.MockTransport(handler)
        return (LocalModels(transport=transport),
                OpenAICompatibleModels("https://models.example/v1", "chat",
                                       transport=transport, embedding_transport=transport))

    async def test_summary_is_deterministic_with_zero_http_for_both_providers(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(503)
        events = self.events(200, '漢🙂' * 4000, service="服" * 1000,
                             release="版" * 1000, level="警" * 1000)
        outputs = []
        for models in self.configured_models(handler):
            for batch in ([], self.events(1, "🙂" * 50000), events):
                summary = await models.summarize(batch)
                self.assertEqual(summary, await models.summarize(batch))
                data = json.loads(summary)
                self.assertEqual(data["facts"]["event_count"], len(batch))
                self.assertNotIn("model_example_sampling", data)
                self.assertIn("Deterministic full-batch", data["provenance"])
            outputs.append(summary)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(seen, [])
        self.assertEqual(len(events[-1].service), 1000)

    async def test_only_generate_and_embed_make_http_requests(self):
        for remote in (False, True):
            seen = []
            def handler(request):
                seen.append(request)
                if request.url.path == "/api/embed":
                    return httpx.Response(200, json={"embeddings": [[0.0] * 768]})
                if remote:
                    return httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"observed"}'}}]})
                return httpx.Response(200, json={"response": '{"answer":"observed"}', "done_reason": "stop"})
            models = self.configured_models(handler)[int(remote)]
            summary = await models.summarize(self.events(200, "database timeout"))
            self.assertEqual(seen, [])
            self.assertEqual(await models.generate("Answer.", {}, SCHEMA), {"answer": "observed"})
            self.assertEqual(await models.embed(summary), [0.0] * 768)
            self.assertEqual([r.url.path for r in seen],
                             ["/v1/chat/completions" if remote else "/api/generate", "/api/embed"])
            body = json.loads(seen[-1].content)
            self.assertFalse(body["truncate"])
            self.assertEqual(body["input"], summary)
            if not remote:
                self.assertEqual(json.loads(seen[0].content)["options"],
                                 {"temperature": 0, "num_ctx": 8192, "num_predict": 1200})

    async def test_actual_generate_and_embed_failures_remain_strict(self):
        for failure in ("http", "timeout", "invalid", "length"):
            seen = []
            def handler(request):
                seen.append(request)
                if failure == "timeout":
                    raise httpx.ReadTimeout("unavailable", request=request)
                if failure == "http":
                    return httpx.Response(503)
                content = '{"answer":"observed"}' if failure == "length" else '{}'
                return httpx.Response(200, json={
                    "response": content, "done_reason": "length" if failure == "length" else "stop",
                    "choices": [{"finish_reason": "length" if failure == "length" else "stop",
                                 "message": {"content": content}}], "embeddings": [[0.0]],
                })
            for models in self.configured_models(handler):
                with self.subTest(failure=failure, provider=type(models).__name__):
                    before = len(seen)
                    await models.summarize(self.events(1, "database"))
                    self.assertEqual(len(seen), before)
                    with self.assertRaises(ModelUnavailable):
                        await models.generate("Answer.", {}, SCHEMA)
                    with self.assertRaises(ModelUnavailable):
                        await models.embed("observed")
                    self.assertEqual(len(seen), before + 2)

    async def test_generate_hard_budget_still_rejects_before_http(self):
        seen = []
        transport = httpx.MockTransport(lambda request: seen.append(request))
        for models in (LocalModels(transport=transport),
                       OpenAICompatibleModels("https://models.example/v1", "chat", transport=transport)):
            with self.assertRaises(ModelUnavailable):
                await models.generate("Answer.", ["x" * 12000] * 3, SCHEMA)
        self.assertEqual(seen, [])

    async def test_full_batch_facts_and_omitted_rare_terms_are_grounded(self):
        events = self.events(200, "database timeout private-canary-never-store")
        events = [replace(event, ts=event.ts + timedelta(seconds=i)) for i, event in enumerate(events)]
        events[17] = replace(events[17], message="cache unavailable private-canary-never-store")
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(503)
        outputs = []
        for model in self.configured_models(handler):
            summary = await model.summarize(events)
            outputs.append(summary)
            data = json.loads(summary)
            self.assertEqual(data["facts"], {
                "event_count": 200, "observed_start": "2026-10-03T00:00:00+00:00",
                "observed_end": "2026-10-03T00:03:19+00:00", "service_counts": {"api": 200},
                "severity_counts": {"error": 200}, "duration_count": 200, "duration_sum_ms": 19900.0,
                "duration_min_ms": 0.0, "duration_max_ms": 199.0, "status_counts": {"200": 100, "500": 100},
            })
            self.assertEqual(data["operational_terms"], ["timeout", "database", "cache", "unavailable"])
            self.assertNotIn("model_example_sampling", data)
            self.assertNotIn("private-canary", summary)
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(seen, [])

    async def test_service_credentials_are_redacted_in_fact_keys(self):
        secret = "custom-provider-credential-canary"
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            "choices": [{"message": {"content": '{"selected_terms":[]}'}}]}))
        summary = await OpenAICompatibleModels("https://models.example/v1", "chat", secret, transport=transport).summarize(
            self.events(1, "database", service=secret))
        self.assertNotIn(secret, summary)
        self.assertEqual(json.loads(summary)["facts"]["service_counts"], {"[REDACTED_SECRET]": 1})

    async def test_keyless_openai_compatible_structured_output(self):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"answer":"observed"}'}}]})

        models = OpenAICompatibleModels("https://models.example/v1", "example-chat",
                                        transport=httpx.MockTransport(handler))
        result = await models.generate("Answer from evidence.", {"event": "safe"}, SCHEMA)
        self.assertEqual(result, {"answer": "observed"})
        self.assertNotIn("authorization", {key.lower() for key in seen[0].headers})
        payload = json.loads(seen[0].content)
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], SCHEMA)

    async def test_key_never_appears_in_safe_provider_error(self):
        secret = "sk-provider-canary-123456789"

        def handler(request):
            self.assertEqual(request.headers["Authorization"], "Bearer " + secret)
            return httpx.Response(401, text="rejected " + secret)

        models = OpenAICompatibleModels("https://models.example/v1", "example-chat", secret,
                                        transport=httpx.MockTransport(handler))
        with self.assertRaises(ModelUnavailable) as caught:
            await models.generate("Answer.", {}, SCHEMA)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn("rejected", str(caught.exception))

    async def test_arbitrary_configured_key_removed_from_context_and_output(self):
        secret = "custom-provider-credential-canary"
        def handler(request):
            self.assertNotIn(secret, json.loads(request.content)["messages"][1]["content"])
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"answer": secret})}}]})
        models = OpenAICompatibleModels("https://models.example/v1", "chat", secret, transport=httpx.MockTransport(handler))
        result = await models.generate("Answer.", {"nested": [secret]}, SCHEMA)
        self.assertNotIn(secret, result["answer"])

    def test_refuses_credentials_over_plain_http(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleModels("http://localhost:9000/v1", "example-chat", "secret-key")

    async def test_rejects_output_outside_requested_schema(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": '{"answer":"ok","extra":"bad"}'}}]},
        ))
        models = OpenAICompatibleModels("https://models.example/v1", "example-chat", transport=transport)
        with self.assertRaises(ModelUnavailable):
            await models.generate("Answer.", {}, SCHEMA)

    def test_endpoint_policy_allows_keyless_local_and_blocks_metadata(self):
        self.assertEqual(validate_endpoint("http://localhost:9000/v1"), "http://localhost:9000/v1")
        self.assertEqual(validate_endpoint("http://[::1]:9000/v1"), "http://[::1]:9000/v1")
        self.assertEqual(validate_endpoint("http://host.docker.internal:9000/v1"),
                         "http://host.docker.internal:9000/v1")
        self.assertEqual(validate_endpoint("http://demo-mcp:8090/mcp"), "http://demo-mcp:8090/mcp")
        for value in ("http://169.254.169.254/latest", "https://user:pass@example.com/v1",
                      "http://example.com/v1", "https://example.com/v1?key=secret"):
            with self.assertRaises(ValueError):
                validate_endpoint(value)


if __name__ == "__main__":
    unittest.main()
