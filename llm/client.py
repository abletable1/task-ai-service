import math
import time
from threading import Lock
from uuid import uuid4
from urllib.parse import urlsplit

import requests

from services.observability import event, stage


class LLMUnavailable(Exception):
    pass


class InvalidLLMResponse(Exception):
    pass



def network_causes(exc):
    """Describe the exception chain without logging messages, URLs or secrets."""
    pending, seen, causes = [exc], set(), []
    while pending and len(causes) < 10:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        cause = {"type": type(item).__name__}
        for name in ("errno", "winerror"):
            value = getattr(item, name, None)
            if isinstance(value, int):
                cause[name] = value
        causes.append(cause)
        for value in (item.__cause__, item.__context__, getattr(item, "reason", None)):
            if isinstance(value, BaseException):
                pending.append(value)
        pending.extend(value for value in item.args if isinstance(value, BaseException))
    return causes


class GigaChatClient:
    def __init__(self, settings, post=None, sleep=time.sleep, clock=time.time):
        self.settings = settings
        self.post = post or requests.post
        self.sleep = sleep
        self.clock = clock
        self._token = None
        self._expires_at = 0
        self._token_lock = Lock()

    def _invalidate_token(self, authorization):
        with self._token_lock:
            if authorization == "Bearer " + (self._token or ""):
                self._token = None
                self._expires_at = 0

    def _request_json(self, operation, url, headers_factory, refresh_token=False, **body):
        refreshed = False
        for attempt in range(1, self.settings.max_attempts + 1):
            renew = False
            with stage(operation + "_attempt"):
                headers = headers_factory()
                event(operation + "_attempt", attempt=attempt)
                try:
                    response = self.post(url, headers=headers, timeout=self.settings.timeout_seconds,
                                         verify=self.settings.tls_verify, **body)
                except requests.exceptions.SSLError as exc:
                    event("llm_tls_error", operation=operation, error_type=type(exc).__name__,
                          hint="Проверьте GIGACHAT_CA_BUNDLE_FILE и сертификаты Минцифры")
                    raise LLMUnavailable() from exc
                except (requests.Timeout, requests.ConnectionError) as exc:
                    event("llm_network_error", operation=operation, attempt=attempt,
                          error_type=type(exc).__name__, endpoint_host=urlsplit(url).hostname,
                          causes=network_causes(exc))
                except requests.RequestException as exc:
                    event("llm_request_error", operation=operation, error_type=type(exc).__name__)
                    raise LLMUnavailable() from exc
                else:
                    with response:
                        status = response.status_code
                        if 200 <= status <= 299:
                            try:
                                data = response.json()
                            except ValueError as exc:
                                event("llm_invalid_json", operation=operation)
                                raise InvalidLLMResponse() from exc
                            # OAuth bodies contain credentials: never log them.
                            if operation == "llm":
                                event("llm_response", response=data)
                            return data
                        event("llm_http_error", operation=operation, attempt=attempt, status=status)
                        if status == 401 and refresh_token and not refreshed and attempt < self.settings.max_attempts:
                            self._invalidate_token(headers["Authorization"])
                            refreshed = True
                            renew = True
                            event("oauth_token_invalidated")
                        elif status not in (408, 429) and not 500 <= status <= 599:
                            raise LLMUnavailable()
            if attempt < self.settings.max_attempts:
                if renew:
                    continue
                delay = self.settings.retry_base_seconds * (2 ** (attempt - 1))
                event("llm_retry", operation=operation, attempt=attempt, delay_seconds=delay)
                with stage("retry_wait"):
                    self.sleep(delay)
        raise LLMUnavailable()

    def _get_access_token(self):
        with self._token_lock:
            if self._token and self.clock() < self._expires_at - 30:
                event("oauth_token_hit")
                return self._token
            with stage("oauth_token"):
                data = self._request_json(
                    "oauth", self.settings.auth_url,
                    lambda: {"Authorization": "Basic " + self.settings.auth_key,
                             "RqUID": str(uuid4()), "Accept": "application/json",
                             "Content-Type": "application/x-www-form-urlencoded"},
                    data={"scope": self.settings.scope},
                )
                if not isinstance(data, dict):
                    raise InvalidLLMResponse()
                token, expires = data.get("access_token"), data.get("expires_at")
                if not isinstance(token, str) or not token.strip() or isinstance(expires, bool):
                    raise InvalidLLMResponse()
                try:
                    expires = float(expires)
                except (TypeError, ValueError) as exc:
                    raise InvalidLLMResponse() from exc
                # GigaChat integrations can return epoch in seconds or milliseconds.
                if expires > 100_000_000_000:
                    expires /= 1000
                if not math.isfinite(expires) or expires <= self.clock() + 30:
                    raise InvalidLLMResponse()
                self._token, self._expires_at = token, expires
                event("oauth_token_received", expires_at=expires)
                return token

    def generate(self, payload):
        if not self.settings.auth_key:
            event("llm_configuration_error", reason="missing_auth_key")
            raise LLMUnavailable()
        return self._request_json(
            "llm", self.settings.base_url.rstrip("/") + "/chat/completions",
            lambda: {"Authorization": "Bearer " + self._get_access_token(),
                     "Accept": "application/json", "Content-Type": "application/json"},
            refresh_token=True, json=payload,
        )
