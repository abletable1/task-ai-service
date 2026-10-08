from config.runtime import prepare_runtime

# Run before imports that may initialize TLS contexts.
_runtime_adjustments = prepare_runtime()

import os
import sys
from urllib.parse import urlsplit
from time import perf_counter
from uuid import uuid4

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

from api.docs import register_docs
from api.routes import routes
from config.settings import Settings
from services.chat import ChatService
from services.observability import configure_logging, event, request_id


def create_app(settings=None, service=None):
    settings = settings or Settings.from_env()
    adjustments = _runtime_adjustments + prepare_runtime()
    configure_logging()
    for adjustment in adjustments:
        event("runtime_environment_adjusted", **adjustment)
    event("service_configured", python_executable=sys.executable, model=settings.model,
          scope=settings.scope, oauth_host=urlsplit(settings.auth_url).hostname,
          oauth_port=urlsplit(settings.auth_url).port or 443,
          api_host=urlsplit(settings.base_url).hostname,
          custom_ca_bundle=bool(settings.ca_bundle_file),
          proxy_env_present=[name for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
                             if os.getenv(name) or os.getenv(name.lower())])
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    app.extensions["chat_service"] = service or ChatService(settings)
    app.register_blueprint(routes)
    register_docs(app)

    @app.before_request
    def begin_request():
        g.request_id = uuid4().hex
        g.context_token = request_id.set(g.request_id)
        g.started = perf_counter()
        event("request_received", method=request.method, path=request.path)

    @app.after_request
    def finish_request(response):
        response.headers["X-Request-ID"] = g.request_id
        event("request_finished", status=response.status_code,
              duration_ms=round((perf_counter() - g.started) * 1000, 3))
        return response

    @app.teardown_request
    def reset_context(_error):
        token = g.pop("context_token", None)
        if token is not None:
            request_id.reset(token)

    @app.errorhandler(HTTPException)
    def http_error(exc):
        message = ("Размер тела запроса превышает 16 КБ." if exc.code == 413
                   else exc.description)
        event("http_error", status=exc.code)
        return jsonify(error={"code": "http_error", "message": message},
                       request_id=g.request_id), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        event("unexpected_error", error_type=type(exc).__name__)
        return jsonify(error={"code": "internal_error", "message": "Внутренняя ошибка сервиса."},
                       request_id=g.request_id), 500

    return app


if __name__ == "__main__":
    configuration = Settings.from_env()
    create_app(configuration).run(host=configuration.host, port=configuration.port, debug=False)
