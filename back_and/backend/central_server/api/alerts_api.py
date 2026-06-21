"""
REST API for alert clip history.

GET /api/alerts              — list all recorded alert clips (newest first)
GET /api/alerts/video/<fn>   — stream a saved .mp4 clip to the browser
"""
import os

from flask import Blueprint, jsonify, send_from_directory, abort

alerts_bp = Blueprint("alerts", __name__)
_recorder = None


def init_alerts_api(recorder) -> None:
    global _recorder
    _recorder = recorder


@alerts_bp.get("/api/alerts")
def list_alerts():
    return jsonify(_recorder.get_alerts() if _recorder else [])


@alerts_bp.get("/api/alerts/video/<path:filename>")
def serve_video(filename: str):
    if not _recorder:
        abort(503)
    path      = _recorder.get_video_path(filename)
    directory = os.path.dirname(path)
    basename  = os.path.basename(path)
    if not os.path.isfile(path):
        abort(404)
    return send_from_directory(directory, basename, mimetype="video/mp4")
