"""
Central Server — Flask + Socket.IO application factory.

Responsibilities:
  - Expose POST /ingest  (called by the Edge Node every second)
  - Run Re-ID, Spatial Risk Engine, Alert Manager on each payload
  - Annotate the received frame and emit it as processed_frame via Socket.IO
  - Emit tracking_update with per-person risk scores and alert types
  - Serve the REST API for rule management (/api/rules)
  - Serve GET /api/health for monitoring

Socket.IO events emitted (as defined in API_CONTRACT.md):
  processed_frame   → base64 JPEG data URI
  tracking_update   → {camera_id, timestamp, persons: [...]}
  alert_new         → AlertDocument dict
  camera_status     → {camera_id, status, timestamp}
"""
import base64
import collections
import time
from datetime import datetime, timezone, timedelta

import cv2
import numpy as np
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO

from shared import config
from shared.schemas import DetectedPerson

from central_server.reid_engine    import ReIDEngine
from central_server.spatial_engine import SpatialEngine
from central_server.threat_memory  import ThreatMemory
from central_server.scoring_engine import ScoringEngine
from central_server.rules_store    import RulesStore
from central_server.camera_store   import CameraStore
from central_server.firebase_client import FirebaseClient
from central_server.alert_manager  import AlertManager
from central_server import zone_store
from central_server import model_manager
from central_server.video_worker     import VideoWorkerManager
from central_server.api.rules_api    import rules_bp, init_rules_api
from central_server.api.cameras_api  import cameras_bp, init_cameras_api
from central_server.api.stream_api   import register_socket_events
from central_server.api.video_api    import video_bp, init_video_api
from central_server.api.alerts_api   import alerts_bp, init_alerts_api
from central_server.alert_recorder   import AlertRecorder

# ---------------------------------------------------------------------------
# Module-level singletons (shared across requests inside one process)
# ---------------------------------------------------------------------------
socketio        = SocketIO()
_reid           = ReIDEngine()
_spatial        = SpatialEngine()
_threat_memory  = ThreatMemory()      # dynamic scoring: decay · zone-exit · benign walk
_scoring        = ScoringEngine()     # KPI scores: climbing / loitering / total
_store          = RulesStore()
_camera_store   = CameraStore()       # dynamic camera RTSP configs
_firebase       = FirebaseClient(dry_run=False)
_alerts         = AlertManager(_firebase)
_known_cameras: set = set()   # tracks first-seen cameras for camera_status events

# Alert video clip recorder — maintains a rolling frame buffer and saves
# .mp4 clips to assets/alerts/ whenever the AlertManager fires an alert.
_alert_recorder = AlertRecorder()

# In-process video pipeline (Live = looping local file, Demo = Firebase Storage file).
# Wired up fully once create_app() registers _annotate / process_tracking_payload
# below; instantiated here so the api blueprint and /api/health can reference it.
_video_manager = VideoWorkerManager(
    socketio         = socketio,
    ingest_fn        = lambda *a, **kw: process_tracking_payload(*a, **kw),
    annotate_fn      = lambda *a, **kw: _annotate(*a, **kw),
    zone_fn          = zone_store.get,
    cache_fn         = lambda camera_id: _annotation_cache.get(camera_id, {}),
    clear_cache_fn   = lambda camera_id: _annotation_cache.pop(camera_id, None),
    frame_hook       = _alert_recorder.push_frame,
)

# Pending camera configs waiting to be delivered to the Edge Node via /ingest response.
# Written by POST /api/cameras; consumed and cleared by the next /ingest call.
_pending_camera_configs: dict = {}

# Cache of the latest annotation state per camera, written by /ingest and read
# by /stream_frame.  Decoupling these two endpoints is what enables smooth video:
# /ingest runs at 1/sec (AI pipeline), /stream_frame runs at STREAM_FPS (10/sec).
_annotation_cache: dict = {}   # camera_id → {risk_results, global_ids, effective_times, person_map}

# ---------------------------------------------------------------------------
# Dashboard metrics — all in-memory, pre-seeded from Firebase at startup
# ---------------------------------------------------------------------------

# Ring buffer of the last 200 detection events (zone entries).
# Each entry: {event_id, timestamp, timestamp_unix, camera_id, event_type, person_id, risk_score}
_events_log: collections.deque = collections.deque(maxlen=200)

# Rolling windows for latency metrics (last 30 samples).
_ingest_intervals_ms: collections.deque = collections.deque(maxlen=30)   # ms between /ingest calls
_processing_times_ms: collections.deque = collections.deque(maxlen=30)   # ms to run AI pipeline

# Per-camera bookkeeping.
_last_ingest_ts:  dict = {}   # camera_id → last ingest unix timestamp
_in_zone_persons: dict = {}   # camera_id → set of global_ids currently inside a zone

# Wire Firebase into the RulesStore so every REST write is also synced
# to /rules/{camera_id}/{rule_id} in the Realtime Database.
_store.set_firebase(_firebase)


def _relay_camera_config(camera_id: str, config: dict) -> str:
    """
    Store the camera config as a pending delivery for the next /ingest call.
    Returns "sent" if the edge ingested recently (≤ 5 s ago), "queued" otherwise.
    The actual delivery happens via the /ingest response — the Edge Node polls
    at 1 Hz so the config will be applied within one ingest cycle.
    """
    _pending_camera_configs[camera_id] = dict(config)
    is_live = time.time() - _last_ingest_ts.get(camera_id, 0) <= 5
    return "sent" if is_live else "queued"


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def _init_events_log() -> None:
    """
    Pre-populate the in-memory events log from Firebase alert history so the
    dashboard is not blank immediately after a server restart.
    Converts AlertDocument format → lightweight event format.
    """
    alerts = _firebase.fetch_all_alerts(limit=200)
    for alert in reversed(alerts):          # reversed so newest ends up at front
        _events_log.appendleft({
            "event_id":       alert.get("alert_id", ""),
            "timestamp":      alert.get("timestamp_iso", ""),
            "timestamp_unix": alert.get("timestamp_unix", 0.0),
            "camera_id":      alert.get("camera_id", ""),
            "event_type":     alert.get("alert_type", "intrusion"),
            "person_id":      alert.get("global_id", ""),
            "risk_score":     alert.get("metrics", {}).get("risk_score", 0),
        })
    if alerts:
        print(f"[INFO] Events log seeded with {len(alerts)} historical alert(s).")


def _make_event(camera_id: str, event_type: str, person_id: str, risk_score: int) -> dict:
    """Build a lightweight event dict for the in-memory log and Firebase /events path."""
    now = datetime.now(timezone.utc)
    return {
        "event_id":       f"evt_{int(now.timestamp() * 1000)}",
        "timestamp":      now.isoformat(),
        "timestamp_unix": now.timestamp(),
        "camera_id":      camera_id,
        "event_type":     event_type,
        "person_id":      person_id,
        "risk_score":     risk_score,
    }


def _hourly_trends() -> list:
    """Aggregate _events_log into hourly counts for the last 24 hours."""
    now     = datetime.now(timezone.utc)
    buckets = {}
    for i in range(24):
        h = (now - timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)
        buckets[h.isoformat()] = 0

    for evt in _events_log:
        try:
            ts  = datetime.fromisoformat(evt["timestamp"])
            key = ts.replace(minute=0, second=0, microsecond=0).isoformat()
            if key in buckets:
                buckets[key] += 1
        except Exception:
            pass

    return [{"hour": h, "count": c} for h, c in sorted(buckets.items())]


def sync_rules_from_firebase():
    """
    Fetch all rules stored in Firebase and merge them into the local store.
    Called by run_server.py after create_app() so the Spatial Engine has
    the correct operator-configured polygons from the very first frame,
    even if they were created while the server was offline.
    """
    imported = _store.load_from_firebase()
    if imported:
        print(f"[INFO] Rules sync complete — {imported} rule(s) loaded from Firebase.")
    else:
        print("[INFO] Rules sync complete — ruleset is empty, awaiting frontend configuration.")


def purge_debug_rules():
    """
    Remove any rules left over from a previous debug session.
    Called by run_server.py at startup so production always begins
    with only operator-configured rules.
    """
    stale = [r for r in _store.get_rules() if r.rule_id.startswith("debug_")]
    for rule in stale:
        _store.delete_rule(rule.rule_id)
    if stale:
        print(f"[INFO] Purged {len(stale)} debug rule(s) — production ruleset is clean.")
    else:
        print("[INFO] No debug rules found — ruleset is clean.")


def process_tracking_payload(camera_id: str, persons: list, frame_w: int, frame_h: int, timestamp: float) -> dict:
    """
    Core AI pipeline — Re-ID, spatial risk, threat memory, KPI scoring, alerts,
    event/activity logging, annotation cache update, and tracking_update emit.

    Used by both the legacy POST /ingest route and the in-process VideoWorker,
    so the entire scoring/alert/Firebase pipeline is exercised identically
    regardless of where the frames come from.
    """
    pipeline_start = time.time()   # wall-clock start for processing-time metric

    # Track inter-ingest interval for the AI-latency health metric.
    if camera_id in _last_ingest_ts:
        _ingest_intervals_ms.append((pipeline_start - _last_ingest_ts[camera_id]) * 1000)
    _last_ingest_ts[camera_id] = pipeline_start

    # Emit camera_status: connected on first payload from this camera.
    if camera_id not in _known_cameras:
        _known_cameras.add(camera_id)
        socketio.emit("camera_status", {
            "camera_id": camera_id,
            "status":    "connected",
            "timestamp": timestamp,
        })
        print(f"[INFO] Camera online: {camera_id}")

    person_map = {p.person_id: p for p in persons}
    box_map    = {p.person_id: p.box for p in persons}

    # 1. Re-ID
    global_ids, effective_times = _reid.process(persons)

    # 2. Spatial risk evaluation — stateless, computes raw evidence score
    rules        = _store.get_rules(camera_id)
    risk_results = _spatial.evaluate(
        persons, rules, frame_w, frame_h, global_ids, effective_times,
        zones=zone_store.get(),
    )

    # 2.5 Dynamic Threat Memory — applies score decay, zone-exit penalty, and
    #     benign-trajectory reduction before any alert or event logic runs.
    #     Mutates risk_results in-place; also purges tracks gone > THREAT_MEMORY_TTL.
    _threat_memory.adjust(risk_results, persons, global_ids, pipeline_start)
    _threat_memory.purge_stale(pipeline_start)

    # 2.6 KPI Scoring — compute climbing_score, loitering_score, total_person_score
    #     for each tracked identity and build a global_id → scores lookup dict.
    scores_map: dict = {}
    for r in risk_results:
        eff = effective_times.get(str(r.local_id), int(
            next((p.time_in_frame_seconds for p in persons if p.person_id == r.local_id), 0)
        ))
        scores_map[r.global_id] = _scoring.compute(
            global_id         = r.global_id,
            alert_types       = r.alert_types,
            eff_time_seconds  = eff,
            min_dwell_seconds = r.min_dwell_seconds,
            zone_sensitivity  = r.zone_sensitivity,
            now               = pipeline_start,
            zone_risk_level   = r.zone_risk_level,
        )
    _scoring.purge_stale(pipeline_start)

    # 3. Alert management (dedup + Firebase dispatch + socket emit)
    _alerts.process(risk_results, camera_id, person_map, box_map, scores_map)

    # 4. Zone-entry event logging — fire once per new intrusion (enter transition only).
    #    Uses a separate in-memory log and Firebase /events path; not subject to
    #    AlertManager's 60-second cooldown, so every distinct zone entry is captured.
    now_in_zone  = {r.global_id for r in risk_results if r.in_zone}
    prev_in_zone = _in_zone_persons.get(camera_id, set())
    new_entries  = now_in_zone - prev_in_zone     # global IDs that just entered a zone
    _in_zone_persons[camera_id] = now_in_zone

    result_by_gid = {r.global_id: r for r in risk_results}
    for gid in new_entries:
        r          = result_by_gid.get(gid)
        event_type = r.alert_types[0] if (r and r.alert_types) else "intrusion"
        evt        = _make_event(camera_id, event_type, gid, r.risk_score if r else 0)
        _events_log.appendleft(evt)
        _firebase.push_event(evt)

    # 4.5 Daily Activity Log — upsert one document per (date, camera, identity).
    #     Runs for every tracked person, not only those in alert state.
    now_dt   = datetime.now(timezone.utc)
    date_key = now_dt.strftime("%Y%m%d")
    date_str = now_dt.strftime("%Y-%m-%d")
    now_iso  = now_dt.isoformat()
    for r in risk_results:
        person  = person_map.get(r.local_id)
        t_frame = effective_times.get(str(r.local_id),
                                     int(person.time_in_frame_seconds) if person else 0)
        zones   = ([r.triggered_rule_name] if r.triggered_rule_name and r.in_zone else [])
        fired   = r.alert_types if r.in_zone else []
        _firebase.upsert_activity_log(
            camera_id             = camera_id,
            global_id             = r.global_id,
            time_in_frame_seconds = t_frame,
            zones_visited         = zones,
            alert_types           = fired,
            scores                = scores_map.get(r.global_id, {}),
            now_iso               = now_iso,
            date_key              = date_key,
            date_str              = date_str,
        )

    # 5. Cache annotation state — /stream_frame and the VideoWorker read this to
    #    annotate frames at STREAM_FPS without re-running the AI pipeline each time.
    _annotation_cache[camera_id] = {
        "risk_results":    risk_results,
        "global_ids":      global_ids,
        "effective_times": effective_times,
        "person_map":      person_map,
    }

    # 6. Emit tracking_update (sidebar data for the React dashboard)
    socketio.emit("tracking_update", _build_tracking_update(
        camera_id, timestamp, persons, risk_results, global_ids, effective_times, scores_map
    ))

    # Record server-side AI pipeline processing time.
    _processing_times_ms.append((time.time() - pipeline_start) * 1000)

    # 7. Build response — KPI scores plus everything the legacy /ingest contract returns.
    return {
        "global_ids":      global_ids,
        "effective_times": effective_times,
        "alerts":          [r.local_id for r in risk_results if r.in_zone],
        "alert_types":     {
            str(r.local_id): r.alert_types
            for r in risk_results if r.alert_types
        },
        "scores": {
            str(r.local_id): scores_map.get(r.global_id, {})
            for r in risk_results
        },
    }


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    socketio.init_app(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
    )

    # Inject socketio.emit into the alert manager so it can push alert_new events.
    _alerts.set_emit_fn(socketio.emit)

    # Wire alert recorder: called once per dispatched alert (after cooldown check).
    def _on_alert(alert):
        score = alert.metrics.get("risk_score", 0) if isinstance(alert.metrics, dict) else 0
        _alert_recorder.trigger(
            alert_id        = alert.alert_id,
            alert_type      = alert.alert_type,
            score           = score,
            camera_id       = alert.camera_id,
        )
    _alerts.set_on_alert_fn(_on_alert)

    # Load persisted restricted zone from disk.
    zone_store.load()

    # Seed in-memory events log from Firebase alert history.
    _init_events_log()

    # REST blueprints
    init_rules_api(_store, _firebase)
    app.register_blueprint(rules_bp)

    init_cameras_api(_camera_store, _relay_camera_config)
    app.register_blueprint(cameras_bp)

    init_video_api(_video_manager)
    app.register_blueprint(video_bp)

    init_alerts_api(_alert_recorder)
    app.register_blueprint(alerts_bp)

    # Socket.IO event handlers
    register_socket_events(socketio)

    # Start the default video worker (Live or Demo, per config.DEFAULT_VIDEO_MODE).
    # Boot continues even if the video file/asset is missing — /api/health and
    # /api/video-source will report status="stopped" until a valid mode is set.
    try:
        _video_manager.switch(config.DEFAULT_VIDEO_MODE)
    except Exception as exc:
        print(f"[WARNING] VideoWorkerManager: could not start '{config.DEFAULT_VIDEO_MODE}' mode at startup — {exc}")

    # ------------------------------------------------------------------
    # POST /ingest  — called by the Edge Node
    # ------------------------------------------------------------------

    @app.post("/ingest")
    def ingest():
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "empty payload"}), 400

        camera_id   = data.get("camera_id",   config.CAMERA_ID)
        frame_w     = int(data.get("frame_width",  1280))
        frame_h     = int(data.get("frame_height",  720))
        timestamp   = float(data.get("timestamp", time.time()))
        raw_persons = data.get("persons", [])
        persons     = [_deserialise_person(p) for p in raw_persons]

        response = process_tracking_payload(camera_id, persons, frame_w, frame_h, timestamp)

        if camera_id in _pending_camera_configs:
            response["camera_config"] = _pending_camera_configs.pop(camera_id)

        return jsonify(response)

    # ------------------------------------------------------------------
    # POST /stream_frame  — called by the Edge Node at STREAM_FPS (10/sec)
    #
    # Accepts a raw JPEG body (Content-Type: image/jpeg).
    # Annotates the frame with the latest cached AI state from /ingest,
    # then emits processed_frame to all connected frontend clients.
    # This endpoint does NO AI work — it is intentionally lightweight.
    # ------------------------------------------------------------------

    @app.post("/stream_frame")
    def stream_frame():
        camera_id = request.args.get("camera_id", config.CAMERA_ID)
        raw = request.get_data()
        if not raw:
            return "", 204

        arr   = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return "", 204

        cached = _annotation_cache.get(camera_id, {})
        annotated = _annotate(
            frame,
            cached.get("risk_results",    []),
            cached.get("global_ids",      {}),
            cached.get("effective_times", {}),
            cached.get("person_map",      {}),
        )

        _, buf  = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
        out_b64 = base64.b64encode(buf).decode("utf-8")
        socketio.emit("processed_frame", f"data:image/jpeg;base64,{out_b64}")
        return "", 204

    # ------------------------------------------------------------------
    # GET /api/restricted_zone
    # Returns the current restricted zone polygon (normalised coordinates).
    # ------------------------------------------------------------------

    @app.get("/api/restricted_zone")
    def get_restricted_zone():
        return jsonify({"zones": zone_store.get()})

    # ------------------------------------------------------------------
    # GET /api/stats
    #
    # Returns aggregated system health and detection trend data for the
    # Dashboard page.  All data comes from in-memory structures populated
    # during /ingest processing — no Firebase read on this hot path.
    # ------------------------------------------------------------------

    @app.get("/api/stats")
    def stats():
        # Total events today (UTC midnight → now).
        today_start_ts = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        total_today = sum(
            1 for e in _events_log if e.get("timestamp_unix", 0) >= today_start_ts
        )

        # Rolling average latency metrics.
        avg_interval   = (sum(_ingest_intervals_ms) / len(_ingest_intervals_ms)
                          if _ingest_intervals_ms else 0.0)
        avg_processing = (sum(_processing_times_ms) / len(_processing_times_ms)
                          if _processing_times_ms else 0.0)

        # Camera online = at least one ingest in the last 10 seconds.
        now = time.time()
        cameras_online = [
            cam for cam, ts in _last_ingest_ts.items() if now - ts <= 10
        ]

        # Optional CPU metric (graceful fallback if psutil is not installed).
        cpu_percent = None
        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=None)
        except ImportError:
            pass

        return jsonify({
            "total_alerts_today": total_today,
            "system_health": {
                "status":               "online" if cameras_online else "idle",
                "cameras_online":       len(cameras_online),
                "cpu_percent":          cpu_percent,
                "ingest_interval_ms":   round(avg_interval),
                "server_processing_ms": round(avg_processing),
                "ai_latency_ms":        round(avg_interval + avg_processing),
            },
            "detection_trends": {
                "hourly": _hourly_trends(),
            },
        })

    # ------------------------------------------------------------------
    # GET /api/events
    #
    # Returns the last N detection events for the Events Log page.
    # Events are zone-entry transitions (fired once per intrusion, not
    # once per frame), sourced from the in-memory ring buffer which is
    # pre-seeded from Firebase on startup so history survives restarts.
    #
    # Query params:
    #   limit   — max events to return (default 50, max 200)
    #   camera_id — filter by camera (optional)
    # ------------------------------------------------------------------

    @app.get("/api/events")
    def events():
        limit     = min(int(request.args.get("limit", 50)), 200)
        camera_id = request.args.get("camera_id")

        result = list(_events_log)
        if camera_id:
            result = [e for e in result if e.get("camera_id") == camera_id]

        return jsonify({"events": result[:limit], "total": len(result)})

    # ------------------------------------------------------------------
    # GET /api/health
    # ------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        model_loaded = model_manager.is_loaded()
        return jsonify({
            "status":  "ok" if model_loaded else "degraded",
            "service": "smart-eye-central-server",
            "model": {
                "loaded": model_loaded,
                "path":   config.YOLO_MODEL_PATH,
            },
            "video_source": _video_manager.status(),
            "firebase_live": _firebase.is_live,
        })

    return app


# ---------------------------------------------------------------------------
# Frame annotation
# ---------------------------------------------------------------------------

def _annotate(frame, risk_results, global_ids, effective_times, person_map):
    annotated  = frame.copy()
    result_map = {r.local_id: r for r in risk_results}

    for local_id, person in person_map.items():
        result   = result_map.get(local_id)
        gid      = global_ids.get(str(local_id), str(local_id))
        eff_time = effective_times.get(str(local_id), int(person.time_in_frame_seconds))
        score    = result.risk_score  if result else 0
        alerts   = result.alert_types if result else []

        x1, y1, x2, y2 = person.box

        if "climbing" in alerts:
            color = config.COLOR_CLIMBING
        elif alerts:
            color = config.COLOR_LOITERING
        elif score >= 40:
            color = config.COLOR_WARNING
        else:
            color = config.COLOR_NORMAL

        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

        label = f"ID:{gid} | {eff_time}s | R:{score}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
        cv2.rectangle(annotated, (x1, y1 - th - 10), (x1 + tw, y1), color, -1)
        cv2.putText(annotated, label, (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)

        if alerts:
            cv2.putText(annotated, " | ".join(alerts).upper(),
                        (x1, y2 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

    return annotated


# ---------------------------------------------------------------------------
# Tracking update builder
# ---------------------------------------------------------------------------

def _build_tracking_update(camera_id, timestamp, persons, risk_results, global_ids, effective_times, scores_map=None):
    result_map  = {r.local_id: r for r in risk_results}
    person_list = []

    for person in persons:
        lid    = person.person_id
        result = result_map.get(lid)
        gid    = global_ids.get(str(lid), f"L-{lid}")
        person_list.append({
            "global_id":             gid,
            "local_id":              lid,
            "time_in_frame_seconds": effective_times.get(str(lid), int(person.time_in_frame_seconds)),
            "last_position":         person.last_position,
            "alert_types":           result.alert_types if result else [],
            "box":                   person.box,
            "risk_score":            result.risk_score       if result else 0,
            "zone_risk_level":       result.zone_risk_level  if result else None,
            "scores":                (scores_map or {}).get(gid, {
                "climbing_score": 0, "loitering_score": 0, "total_person_score": 0
            }),
        })

    return {"camera_id": camera_id, "timestamp": timestamp, "persons": person_list}


# ---------------------------------------------------------------------------
# Deserialisation helper
# ---------------------------------------------------------------------------

def _deserialise_person(d: dict) -> DetectedPerson:
    return DetectedPerson(
        person_id             = int(d["person_id"]),
        feature               = d.get("feature"),
        box                   = d.get("box", [0, 0, 0, 0]),
        last_position         = d.get("last_position", {"x": 0, "y": 0}),
        positions             = [tuple(p) for p in d.get("positions", [])],
        zone_visits           = [tuple(z) for z in d.get("zone_visits", [])],
        time_in_frame_seconds = float(d.get("time_in_frame_seconds", 0.0)),
        avg_movement_pixels   = float(d.get("avg_movement_pixels", 0.0)),
        area_spread_pixels    = float(d.get("area_spread_pixels", 999.0)),
        zone_returns          = int(d.get("zone_returns", 0)),
        first_seen            = float(d.get("first_seen", time.time())),
        last_seen             = float(d.get("last_seen",  time.time())),
    )
