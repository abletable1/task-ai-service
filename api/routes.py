from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.exceptions import BadRequest

from llm.client import InvalidLLMResponse, LLMUnavailable
from services.observability import event, stage

routes = Blueprint("api", __name__)
FALLBACK = "Сервис временно недоступен, попробуйте позже."


@routes.get("/health")
def health():
    return jsonify(status="ok")


@routes.post("/chat")
def chat():
    try:
        with stage("validation"):
            if not request.is_json:
                raise ValueError("Используйте Content-Type: application/json.")
            try:
                data = request.get_json()
            except BadRequest:
                raise ValueError("Тело запроса должно содержать валидный JSON.") from None
            if not isinstance(data, dict):
                raise ValueError("Тело запроса должно быть JSON-объектом.")
            message = data.get("message")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("Поле message обязательно и должно быть непустой строкой.")
            if len(message) > 1000:
                raise ValueError("Поле message должно содержать не более 1000 символов.")
            event("input_validated", message=message)
        result = current_app.extensions["chat_service"].summarize(message)
        with stage("response_builder"):
            return jsonify(**result, request_id=g.request_id), 200
    except ValueError as exc:
        event("validation_error", message=str(exc))
        return jsonify(error={"code": "validation_error", "message": str(exc)},
                       request_id=g.request_id), 400
    except LLMUnavailable:
        event("fallback", reason="llm_unavailable")
        return jsonify(answer=FALLBACK, cached=False,
                       error={"code": "llm_unavailable", "message": FALLBACK},
                       request_id=g.request_id), 503
    except InvalidLLMResponse:
        message = "Модель вернула некорректный ответ. Попробуйте позже."
        event("fallback", reason="invalid_llm_response")
        return jsonify(answer=message, cached=False,
                       error={"code": "invalid_llm_response", "message": message},
                       request_id=g.request_id), 502
