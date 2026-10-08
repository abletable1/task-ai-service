import json

from flask import Blueprint, jsonify
from flask_swagger_ui import get_swaggerui_blueprint

from config.settings import ROOT

routes = Blueprint("documentation", __name__)


@routes.get("/openapi.json")
def openapi_spec():
    specification = json.loads((ROOT / "config/openapi.json").read_text(encoding="utf-8"))
    return jsonify(specification)


def register_docs(app):
    app.register_blueprint(routes)
    app.register_blueprint(get_swaggerui_blueprint(
        "/docs", "/openapi.json",
        config={"app_name": "LLM Service API", "deepLinking": True,
                "displayRequestDuration": True, "validatorUrl": None,
                "supportedSubmitMethods": ["get", "post"]},
    ))
