"""Exercise the real HTTP pipeline against a local, explicitly fake LLM provider."""
import html
import json
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import requests
from werkzeug.serving import make_server

from config.settings import ROOT, Settings
from main import create_app


class DemoProvider(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):
        type(self).calls += 1
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/oauth":
            body = json.dumps({"access_token": "local-demo-token", "expires_at": (time.time() + 1800) * 1000}).encode()
        elif json.loads(raw)["messages"][-1]["content"] == "DEMO: invalid JSON":
            body = b"invalid json"
        else:
            body = json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "Команда создала сервис суммаризации и проверила его работу."}}]},
                ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    provider = ThreadingHTTPServer(("127.0.0.1", 0), DemoProvider)
    settings = Settings(auth_key="local-demo-key", model="local-demo-model",
                        base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                        auth_url=f"http://127.0.0.1:{provider.server_port}/oauth",
                        timeout_seconds=0.3, retry_base_seconds=0.05)
    application = make_server("127.0.0.1", 0, create_app(settings), threaded=True)
    for server in (provider, application):
        Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{application.server_port}/chat"
    results = []

    def exercise(name, body, expected_status, cached=None):
        response = requests.post(url, json=body, timeout=5)
        result = response.json()
        assert response.status_code == expected_status, (name, result)
        if cached is not None:
            assert result["cached"] == cached, (name, result)
        results.append({"case": name, "request": body, "status": response.status_code,
                        "response": result, "result": "PASS"})

    try:
        message = {"message": "Команда создала сервис суммаризации. Затем проверила его работу."}
        exercise("Успешный запрос (локальная модель-заглушка)", message, 200, False)
        exercise("Повторный запрос: кеш", message, 200, True)
        assert DemoProvider.calls == 2  # One OAuth exchange and one generation.
        exercise("Отсутствует message", {}, 400)
        exercise("Текст длиннее 1000 символов", {"message": "я" * 1001}, 400)
        exercise("Некорректный JSON провайдера", {"message": "DEMO: invalid JSON"}, 502)
        provider.shutdown()
        provider.server_close()
        exercise("Сбой сети: порт провайдера закрыт", {"message": "Новый запрос после сбоя"}, 503, False)
        exercise("Закешированный ответ доступен при сбое", message, 200, True)
    finally:
        application.shutdown()
        application.server_close()
        provider.server_close()
    destination = ROOT / "artifacts"
    destination.mkdir(exist_ok=True)
    transcript = {"generated_at": datetime.now(timezone.utc).isoformat(),
                  "provider": "Local fake HTTP server; no GigaChat calls", "cases": results}
    (destination / "demo.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    cards = []
    for result in results:
        display = dict(result)
        if len(display["request"].get("message", "")) > 200:
            display["request"] = {"message": "я × 1001 (сокращено для отображения)"}
        cards.append("<article><h2>" + html.escape(result["case"]) + "</h2><pre>" +
                     html.escape(json.dumps(display, ensure_ascii=False, indent=2)) + "</pre></article>")
    page = ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Демонстрация сервиса</title>'
            '<style>body{font:16px system-ui;background:#f1f5f9;color:#172033;max-width:1000px;'
            'margin:40px auto;padding:0 20px}article{background:white;padding:20px;margin:20px 0;'
            'border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}h2{font-size:20px}</style>'
            '<h1>LLM-сервис: результаты HTTP-проверок</h1><p>Провайдер — локальная заглушка. '
            'Реальные запросы GigaChat здесь не выполнялись.</p>' + "".join(cards) + '</html>')
    (destination / "demo.html").write_text(page, encoding="utf-8")
    print(f"PASS: {len(results)} HTTP cases. Artifacts: artifacts/demo.json, artifacts/demo.html")


if __name__ == "__main__":
    main()
