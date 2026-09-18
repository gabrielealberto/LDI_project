"""Durable run-management endpoints."""

from flask import Blueprint, current_app, jsonify, request, send_file

from Webapp.api.routes_config import load_parameters
from Webapp.service import RunConflict
from core.run_config import RunParameters
from core.run_config import UniverseFilters


bp = Blueprint("run", __name__, url_prefix="/api/run")


def _repository():
    return current_app.extensions["run_repository"]


def _public(record: dict, include_log: bool = True) -> dict:
    payload = dict(record)
    if include_log:
        payload["log"] = _repository().log(record["run_id"])
    payload["result"] = "available" if record["status"] == "success" else None
    return payload


@bp.post("/start")
def start_run():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"error": "The request body must be a JSON object."}), 400
    unknown = set(body) - {"parameters", "universe_filters"}
    if unknown:
        return jsonify({"error": f"Unknown run options: {sorted(unknown)}"}), 400
    try:
        parameters = (
            RunParameters.from_mapping(body["parameters"])
            if "parameters" in body
            else load_parameters()
        )
        universe_filters = UniverseFilters.from_mapping(body.get("universe_filters"))
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    try:
        payload = {
            "parameters": parameters.to_dict(),
            "universe_filters": universe_filters.to_dict(),
        }
        record = current_app.extensions["run_service"].start(payload)
    except RunConflict as error:
        return jsonify({"error": str(error)}), 409
    except Exception as error:
        current_app.logger.exception("Could not launch the LDI pipeline")
        return jsonify({"error": f"Could not launch the pipeline: {error}"}), 500
    return jsonify(_public(record, include_log=False)), 202


@bp.get("/status/<run_id>")
def run_status(run_id: str):
    record = _repository().get(run_id)
    if record is None:
        return jsonify({"error": "Run not found"}), 404
    return jsonify(_public(record))


@bp.get("/latest")
def latest_run():
    record = _repository().latest()
    if record is None:
        return jsonify({"status": "idle"})
    return jsonify(_public(record))


@bp.get("/history")
def run_history():
    return jsonify(
        [_public(record, include_log=False) for record in _repository().list()]
    )


@bp.get("/<run_id>/artifacts")
def artifacts(run_id: str):
    if _repository().get(run_id) is None:
        return jsonify({"error": "Run not found"}), 404
    directory = _repository().run_dir(run_id) / "artifacts"
    if not directory.exists():
        return jsonify([])
    return jsonify(
        [
            {"name": path.name, "size_bytes": path.stat().st_size}
            for path in sorted(directory.iterdir())
            if path.is_file()
        ]
    )


@bp.get("/<run_id>/artifacts/<name>")
def download_artifact(run_id: str, name: str):
    if _repository().get(run_id) is None:
        return jsonify({"error": "Run not found"}), 404
    try:
        path = _repository().artifact(run_id, name)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    if not path.exists():
        return jsonify({"error": "Artifact not found"}), 404
    return send_file(path, as_attachment=True, download_name=path.name)
