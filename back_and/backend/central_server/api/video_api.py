"""
REST API Blueprint for the Video Source toggle (Live / Demo).

GET  /api/video-source                       → current status
POST /api/video-source {"mode", "filename"}  → switch to "live" or "demo"
                                                ("filename" selects a Demo
                                                Mode video; ignored for "live")
GET  /api/available-videos                   → .mp4 filenames found in
                                                assets/videos/ on disk
                                                (dynamic — no code changes
                                                needed to add videos)
"""
import os
import traceback
from urllib.parse import unquote

from flask import Blueprint, jsonify, request

from shared import config
from central_server.video_sources import list_demo_videos

video_bp = Blueprint("video_source", __name__)

_manager = None   # VideoWorkerManager, injected by init_video_api()


def init_video_api(manager):
    global _manager
    _manager = manager


def _err(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message, "http_status": status}}), status


@video_bp.get("/api/video-source")
def get_video_source():
    return jsonify(_manager.status())


@video_bp.post("/api/video-source")
def set_video_source():
    data = request.get_json(silent=True)
    if not data:
        return _err("INVALID_PAYLOAD", "Request body must be valid JSON.", 400)

    mode = (data.get("mode") or "").strip().lower()
    if mode not in ("live", "demo"):
        return _err("INVALID_PAYLOAD", "mode must be 'live' or 'demo'.", 400)

    filename = data.get("filename")
    if filename is not None:
        # unquote handles the case where a browser URL-encodes spaces/special chars
        filename = unquote(str(filename).strip()) or None

    # Explicit existence check before handing off, so the error message clearly
    # names the missing file rather than surfacing a raw Python exception.
    if mode == "demo" and filename:
        full_path = os.path.join(config.DEMO_VIDEOS_DIR, filename)
        if not os.path.isfile(full_path):
            return _err(
                "VIDEO_SOURCE_NOT_FOUND",
                f"Video file not found on server: {filename!r}  "
                f"(looked in {config.DEMO_VIDEOS_DIR})",
                404,
            )

    try:
        _manager.switch(mode, filename=filename)
    except FileNotFoundError as exc:
        return _err("VIDEO_SOURCE_NOT_FOUND", str(exc), 404)
    except ValueError as exc:
        return _err("INVALID_PAYLOAD", str(exc), 400)
    except Exception as exc:
        # Catch-all so Flask never returns an HTML 500 page — always JSON.
        traceback.print_exc()
        return _err("SWITCH_FAILED", f"Failed to switch video source: {exc}", 500)

    return jsonify(_manager.status())


@video_bp.get("/api/available-videos")
def get_available_videos():
    return jsonify({"videos": list_demo_videos()})
