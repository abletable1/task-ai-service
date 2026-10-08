import io
import json
import unittest
import time
from dataclasses import replace
from unittest.mock import Mock, patch

import requests

from cache.ttl import TTLCache, make_key
from config.settings import Settings
from llm.client import GigaChatClient
from llm.prompts import build_prompt
from main import create_app
from services.chat import ChatService
from services.observability import JsonFormatter, logger


def response_data(text="  Краткое   резюме.\n"):
    return {"choices": [{"finish_reason": "stop",
                         "message": {"role": "assistant", "content": text}}]}


def http_response(status=200, data=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(response_data() if data is None else data).encode()
    response._content_consumed = True
    response.encoding = "utf-8"
    return response


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(auth_key="test-secret", retry_base_seconds=0.01)
        self.post = Mock(return_value=http_response())
        self.sleep = Mock()
        self.provider = GigaChatClient(self.settings, post=self.post, sleep=self.sleep)
        self.provider._token = "test-token"
        self.provider._expires_at = time.time() + 1800
        self.service = ChatService(self.settings, client=self.provider)
        self.app = create_app(self.settings, service=self.service)
        self.client = self.app.test_client()

    def send(self, message="Текст для суммаризации"):
        return self.client.post("/chat", json={"message": message})

    def test_success_and_cache(self):
        first = self.send()
        second = self.send()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["answer"], "Краткое резюме.")
        self.assertFalse(first.json["cached"])
        self.assertTrue(second.json["cached"])
        self.assertNotEqual(first.json["request_id"], second.json["request_id"])
        self.assertEqual(first.headers["X-Request-ID"], first.json["request_id"])
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.kwargs["timeout"], 15)
        payload = self.post.call_args.kwargs["json"]
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertEqual(payload["model"], self.settings.model)

    def test_invalid_inputs_do_not_call_provider(self):
        for data in ({}, {"message": ""}, {"message": "  "}, {"message": 5},
                     {"message": None}, {"message": "я" * 1001}, [], None):
            with self.subTest(data=data):
                response = self.client.post("/chat", data=json.dumps(data),
                                            content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["error"]["code"], "validation_error")
        self.post.assert_not_called()

    def test_message_boundary(self):
        self.assertEqual(self.send("я" * 1000).status_code, 200)

    def test_malformed_json_and_content_type(self):
        for body, content_type in (("{", "application/json"), ("text", "text/plain")):
            response = self.client.post("/chat", data=body, content_type=content_type)
            self.assertEqual(response.status_code, 400)
        self.post.assert_not_called()

    def test_large_body(self):
        response = self.client.post("/chat", data="x" * 17000, content_type="application/json")
        self.assertEqual(response.status_code, 413)
        self.post.assert_not_called()

    def test_network_failures_retry_and_no_fallback_cache(self):
        for exception in (requests.Timeout, requests.ConnectionError):
            with self.subTest(exception=exception):
                self.post.reset_mock()
                self.sleep.reset_mock()
                self.post.side_effect = exception("simulated")
                response = self.send()
                self.assertEqual(response.status_code, 503)
                self.assertIn("временно недоступен", response.json["answer"])
                self.assertEqual(self.post.call_count, 3)
                self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.01, 0.02])
        self.post.side_effect = None
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(self.post.call_count, 4)

    def test_temporary_http_failures_retry(self):
        for status in (408, 429, 500, 502, 503):
            with self.subTest(status=status):
                self.post.reset_mock()
                self.post.side_effect = [http_response(status), http_response(status), http_response()]
                response = self.send(str(status))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.post.call_count, 3)

    def test_http_retry_exhaustion(self):
        self.post.return_value = http_response(503)
        self.assertEqual(self.send().status_code, 503)
        self.assertEqual(self.post.call_count, 3)

    def test_permanent_http_errors_not_retried(self):
        for status in (400, 403, 404):
            with self.subTest(status=status):
                self.post.reset_mock()
                self.post.return_value = http_response(status)
                self.assertEqual(self.send().status_code, 503)
                self.post.assert_called_once()

    def test_non_retryable_request_exception(self):
        self.post.side_effect = requests.exceptions.InvalidURL("bad URL")
        self.assertEqual(self.send().status_code, 503)
        self.post.assert_called_once()

    def test_missing_key_returns_fallback(self):
        settings = replace(self.settings, auth_key="")
        client = create_app(settings).test_client()
        self.assertEqual(client.post("/chat", json={"message": "текст"}).status_code, 503)

    def test_invalid_provider_json_not_cached(self):
        response = http_response()
        response._content = b"not json"
        self.post.return_value = response
        self.assertEqual(self.send().status_code, 502)
        self.assertEqual(self.send().status_code, 502)
        self.assertEqual(self.post.call_count, 2)
        self.sleep.assert_not_called()

    def test_invalid_response_structure(self):
        examples = [[], {}, {"choices": []}, {"choices": [None]},
                    response_data("   "), response_data(42),
                    {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "partial"}}]},
                    {"choices": [{"finish_reason": "blacklist", "message": {"role": "assistant", "content": "refused"}}]},
                    {"choices": [{"finish_reason": "stop", "message": {"role": "user", "content": "wrong role"}}]}]
        for data in examples:
            with self.subTest(data=data):
                self.post.return_value = http_response(data=data)
                self.assertEqual(self.send().status_code, 502)

    def test_json_logs_stage_durations_and_no_key(self):
        import logging
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        try:
            first = self.send()
            self.send()
        finally:
            logger.removeHandler(handler)
        records = [json.loads(line) for line in stream.getvalue().splitlines()]
        events = {r["event"] for r in records}
        self.assertTrue({"request_received", "prompt_built", "llm_response", "cache_miss",
                         "cache_hit", "request_finished"}.issubset(events))
        durations = [r for r in records if r["event"] == "stage_finished"]
        self.assertTrue(all(r["duration_ms"] >= 0 for r in durations))
        self.assertTrue(any(r["stage"] == "validation" for r in durations))
        self.assertIn(first.json["request_id"], {r["request_id"] for r in records})
        self.assertNotIn("test-secret", stream.getvalue())


class CacheTests(unittest.TestCase):
    def test_expiration_and_capacity(self):
        now = [0]
        cache = TTLCache(ttl=600, max_entries=2, clock=lambda: now[0])
        cache.set("one", "answer")
        now[0] = 599
        self.assertEqual(cache.get("one"), "answer")
        now[0] = 600
        self.assertIsNone(cache.get("one"))
        cache.set("a", "a")
        cache.set("b", "b")
        cache.set("c", "c")
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get("c"), "c")

    def test_key_includes_all_generation_parameters(self):
        settings = Settings()
        payload = build_prompt("hello", settings)
        key = make_key(payload, settings.base_url)
        for field, value in (("messages", [{"role": "user", "content": "other"}]),
                             ("model", "other"), ("temperature", 1), ("max_tokens", 500)):
            with self.subTest(field=field):
                self.assertNotEqual(key, make_key({**payload, field: value}, settings.base_url))
        self.assertNotEqual(key, make_key(payload, "http://localhost"))
        system_changed = {**payload, "messages": [
            {"role": "system", "content": "other instructions"}, payload["messages"][1]]}
        self.assertNotEqual(key, make_key(system_changed, settings.base_url))
        self.assertNotEqual(make_key(payload, settings.base_url, "GIGACHAT_API_PERS"),
                            make_key(payload, settings.base_url, "GIGACHAT_API_CORP"))
        self.assertEqual(key, make_key(dict(reversed(list(payload.items()))), settings.base_url))



class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(auth_key="oauth-secret", retry_base_seconds=0.01)
        self.post = Mock()
        self.sleep = Mock()
        self.now = [1_800_000_000.0]
        self.client = GigaChatClient(self.settings, post=self.post, sleep=self.sleep,
                                     clock=lambda: self.now[0])
        self.payload = build_prompt("Текст", self.settings)

    def auth_response(self, token="token-secret", milliseconds=True):
        expiry = self.now[0] + 1800
        return http_response(data={"access_token": token,
                                   "expires_at": expiry * 1000 if milliseconds else expiry})

    def test_exchange_headers_form_token_reuse_and_no_secret_logs(self):
        import logging
        from uuid import UUID
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        self.post.side_effect = [self.auth_response(), http_response(), http_response()]
        try:
            self.client.generate(self.payload)
            self.client.generate({**self.payload, "messages": [{"role": "user", "content": "new"}]})
        finally:
            logger.removeHandler(handler)
        self.assertEqual(self.post.call_count, 3)
        auth, first, second = self.post.call_args_list
        self.assertEqual(auth.args[0], self.settings.auth_url)
        self.assertEqual(auth.kwargs["headers"]["Authorization"], "Basic oauth-secret")
        self.assertEqual(UUID(auth.kwargs["headers"]["RqUID"]).version, 4)
        self.assertEqual(auth.kwargs["data"], {"scope": "GIGACHAT_API_PERS"})
        self.assertEqual(first.kwargs["headers"]["Authorization"], "Bearer token-secret")
        self.assertTrue(first.args[0].endswith('/chat/completions'))
        for call in (auth, first, second):
            self.assertEqual(call.kwargs["timeout"], 15)
            self.assertIs(call.kwargs["verify"], True)
        self.assertNotIn("oauth-secret", stream.getvalue())
        self.assertNotIn("token-secret", stream.getvalue())

    def test_expiry_seconds_and_refresh_before_expiration(self):
        self.post.side_effect = [self.auth_response(milliseconds=False), http_response()]
        self.client.generate(self.payload)
        self.now[0] += 1771
        self.post.side_effect = [self.auth_response(token="new-token"), http_response()]
        self.client.generate(self.payload)
        self.assertEqual(self.post.call_count, 4)
        self.assertEqual(self.post.call_args.kwargs["headers"]["Authorization"], "Bearer new-token")

    def test_generation_401_refreshes_once_within_attempt_limit(self):
        self.client._token = "old-token"
        self.client._expires_at = self.now[0] + 1800
        self.post.side_effect = [http_response(401), self.auth_response(), http_response()]
        self.client.generate(self.payload)
        self.assertEqual(self.post.call_count, 3)
        self.sleep.assert_not_called()
        self.post.reset_mock()
        self.post.side_effect = [http_response(401), self.auth_response(), http_response(401)]
        from llm.client import LLMUnavailable
        with self.assertRaises(LLMUnavailable):
            self.client.generate(self.payload)
        self.assertEqual(self.post.call_count, 3)

    def test_oauth_temporary_errors_retry(self):
        self.post.side_effect = [requests.Timeout(), http_response(503), self.auth_response(), http_response()]
        self.client.generate(self.payload)
        self.assertEqual(self.post.call_count, 4)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list], [0.01, 0.02])

    def test_oauth_rejection_has_no_retries(self):
        from llm.client import LLMUnavailable
        for status in (400, 401, 403):
            with self.subTest(status=status):
                self.post.reset_mock()
                self.post.return_value = http_response(status)
                with self.assertRaises(LLMUnavailable):
                    self.client.generate(self.payload)
                self.post.assert_called_once()

    def test_invalid_tokens_and_expiry_rejected_without_logging_body(self):
        from llm.client import InvalidLLMResponse
        cases = [{}, [], {"access_token": "", "expires_at": self.now[0] + 1800},
                 {"access_token": "token-secret", "expires_at": None},
                 {"access_token": "token-secret", "expires_at": "NaN"},
                 {"access_token": "token-secret", "expires_at": self.now[0] - 1}]
        for data in cases:
            with self.subTest(data=data):
                self.post.reset_mock()
                self.post.return_value = http_response(data=data)
                with self.assertRaises(InvalidLLMResponse):
                    self.client.generate(self.payload)
                self.post.assert_called_once()

    def test_tls_error_not_retried(self):
        from llm.client import LLMUnavailable
        self.post.side_effect = requests.exceptions.SSLError("certificate validation failed")
        with self.assertRaises(LLMUnavailable):
            self.client.generate(self.payload)
        self.post.assert_called_once()
        self.sleep.assert_not_called()

    def test_env_config_uses_gigachat_fields(self):
        import os
        env = {"GIGACHAT_AUTH_KEY": "new-secret", "GIGACHAT_MODEL": "GigaChat-2-Pro",
               "GIGACHAT_SCOPE": "GIGACHAT_API_CORP"}
        with patch("config.settings.load_dotenv"), patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.auth_key, "new-secret")
        self.assertEqual(settings.model, "GigaChat-2-Pro")
        self.assertEqual(settings.scope, "GIGACHAT_API_CORP")

    def test_network_diagnostic_chain_does_not_contain_secrets(self):
        from llm.client import network_causes
        exception = requests.ConnectionError("oauth-secret", ConnectionResetError(10054, "token-secret"))
        causes = network_causes(exception)
        self.assertIn({"type": "ConnectionResetError", "errno": 10054}, causes)
        encoded = json.dumps(causes)
        self.assertNotIn("oauth-secret", encoded)
        self.assertNotIn("token-secret", encoded)

    def test_invalid_scope_and_ca_file(self):
        with self.assertRaises(ValueError):
            replace(self.settings, scope="invalid")
        with self.assertRaises(ValueError):
            replace(self.settings, ca_bundle_file="missing-certificate.pem")


if __name__ == "__main__":
    unittest.main()
