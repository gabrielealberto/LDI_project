"""Flask application factory for the LDI web interface."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from flask import Flask, jsonify, render_template

from .api.routes_config import bp as config_bp
from .api.routes_results import bp as results_bp
from .api.routes_run import bp as run_bp
from .api.routes_stress import bp as stress_bp
from .repository import DEFAULT_STORAGE_DIR, RunRepository
from .service import RunService


def create_app(config: dict | None = None) -> Flask:
    root = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
    )
    app.config.update(
        WEBAPP_STORAGE_DIR=str(DEFAULT_STORAGE_DIR),
        MAX_CONTENT_LENGTH=1_000_000,
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)

    repository = RunRepository(Path(app.config["WEBAPP_STORAGE_DIR"]))
    operation_lock = RLock()
    app.extensions["run_repository"] = repository
    app.extensions["operation_lock"] = operation_lock
    app.extensions["run_service"] = RunService(repository, operation_lock)
    for blueprint in (config_bp, run_bp, results_bp, stress_bp):
        app.register_blueprint(blueprint)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
