"""
In-process video pipeline — replaces the Edge Node's HTTP /ingest +
/stream_frame round trips with direct in-process function calls.

VideoWorker owns one video source (a looping local file or a cached Firebase
Storage file) and one Detector. It runs the full local pipeline that
run_edge.py used to split across threads + an HTTP hop, but in one background
thread:

  1. read a frame from the source (looping file)
  2. detector.process_frame(frame)            # YOLO every N frames
  3. every SEND_INTERVAL_SECONDS: build a TrackingPayload and call ingest_fn(...)
     directly (no HTTP) — this runs the full Re-ID/scoring/alert pipeline
  4. annotate the frame using the cached annotation state and emit
     'processed_frame' via Socket.IO
  5. sleep to pace the loop to STREAM_FPS

VideoWorkerManager owns the single active VideoWorker and switches between
"live" (LoopingFileSource) and "demo" (DemoVideoSource) modes.
"""
import base64
import threading
import time

import cv2
import numpy as np

from shared import config
from central_server import model_manager
from central_server.video_sources import LoopingFileSource, DemoVideoSource
from edge_node.detector import Detector
from edge_node.payload_builder import build_payload

# BGR colors for zone overlays burned into the recording
_ZONE_COLORS_CV = {
    "Low":    (0, 255, 255),   # yellow
    "Medium": (0, 165, 255),   # orange
    "High":   (0,   0, 255),   # red
}


def _point_in_polygon(cx: float, cy: float, poly: list) -> bool:
    """Ray-casting point-in-polygon test (pixel coordinates)."""
    n, inside, j = len(poly), False, len(poly) - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > cy) != (yj > cy)) and (cx < (xj - xi) * (cy - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _draw_zones_cv(frame: np.ndarray, zones: list) -> np.ndarray:
    """Burn zone polygons onto *frame* using risk-level colors.

    Returns a new frame (does not mutate the input).
    Used so the recorded .mp4 clip contains visible zone overlays for forensics.
    """
    if not zones:
        return frame
    fh, fw = frame.shape[:2]
    out = frame.copy()
    for zone in zones:
        pts_raw = zone.get("points", [])
        if len(pts_raw) < 3:
            continue
        risk  = zone.get("riskLevel", "Medium")
        color = _ZONE_COLORS_CV.get(risk, _ZONE_COLORS_CV["Medium"])
        pts   = np.array(
            [(int(p["x"] * fw), int(p["y"] * fh)) for p in pts_raw],
            dtype=np.int32,
        ).reshape((-1, 1, 2))
        # Semi-transparent fill (alpha blend)
        overlay = out.copy()
        cv2.fillPoly(overlay, [pts], color)
        out = cv2.addWeighted(overlay, 0.15, out, 0.85, 0)
        # Solid border
        cv2.polylines(out, [pts], isClosed=True, color=color, thickness=2)
        # Risk-level label at the first vertex
        x0, y0 = pts_raw[0]["x"], pts_raw[0]["y"]
        cv2.putText(
            out, risk.upper(),
            (int(x0 * fw) + 4, int(y0 * fh) - 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
        )
    return out


class VideoWorker(threading.Thread):
    """Runs detection + the AI pipeline against one looping video source."""

    def __init__(self, camera_id, source, detector, socketio, ingest_fn, annotate_fn, zone_fn, cache_fn, frame_hook=None):
        super().__init__(daemon=True, name=f"video-worker-{camera_id}")
        self.camera_id   = camera_id
        self.source      = source
        self.detector    = detector
        self.socketio    = socketio
        self.ingest_fn   = ingest_fn
        self.annotate_fn = annotate_fn
        self.zone_fn     = zone_fn
        self.cache_fn    = cache_fn
        self.frame_hook   = frame_hook   # called with each emitted frame (for alert recording)
        self._stop_event  = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        # ──────────────────────────────────────────────────────────────────────
        # Shared frame slot: the stream loop writes the latest raw frame here.
        # The YOLO thread reads from it continuously without blocking the loop.
        #
        # A monotonic sequence counter lets the YOLO thread skip frames it has
        # already processed instead of re-running inference on the same image.
        # ──────────────────────────────────────────────────────────────────────
        _frame_lock = threading.Lock()
        _latest_frame = [None]   # newest raw frame from the video source
        _frame_seq    = [0]      # incremented each time a new frame is stored

        # ── YOLO worker — runs at full CPU speed, never blocks the stream ──
        def _yolo_worker():
            last_seq = -1
            while not self._stop_event.is_set():
                with _frame_lock:
                    seq   = _frame_seq[0]
                    frame = _latest_frame[0]
                if seq == last_seq or frame is None:
                    # No new frame yet — yield briefly and try again.
                    time.sleep(0.005)
                    continue
                last_seq = seq
                try:
                    # run_inference() bypasses YOLO_EVERY_N_FRAMES so this
                    # thread runs at maximum CPU throughput.
                    self.detector.run_inference(frame)
                except Exception as exc:
                    print(f"[WARN] VideoWorker[{self.camera_id}]: YOLO — {exc}")

        # ── Ingest worker — AI pipeline off the critical path ─────────────
        _ingest_lock  = threading.Lock()
        _ingest_data  = [None]   # (camera_id, persons, fw, fh, timestamp)
        _ingest_event = threading.Event()

        def _ingest_worker():
            while not self._stop_event.is_set():
                if not _ingest_event.wait(timeout=0.1):
                    continue
                _ingest_event.clear()
                with _ingest_lock:
                    data = _ingest_data[0]
                if data is None:
                    continue
                try:
                    cam, persons, fw, fh, ts = data
                    self.ingest_fn(cam, persons, fw, fh, ts)
                except Exception as exc:
                    print(f"[ERROR] VideoWorker[{self.camera_id}]: ingest — {exc}")

        yolo_thread   = threading.Thread(target=_yolo_worker,   daemon=True,
                                         name=f"yolo-{self.camera_id}")
        ingest_thread = threading.Thread(target=_ingest_worker, daemon=True,
                                         name=f"ingest-{self.camera_id}")
        yolo_thread.start()
        ingest_thread.start()

        # Stream loop paces at STREAM_FPS — one frame read + one emit per tick.
        stream_interval = 1.0 / max(config.STREAM_FPS, 1)
        last_send       = 0.0

        print(
            f"[INFO] VideoWorker[{self.camera_id}]: started — "
            f"stream={config.STREAM_FPS} fps  yolo=continuous background  "
            f"src={self.source.fps:.1f} fps"
        )

        while not self._stop_event.is_set():
            t0 = time.time()

            # ── 1. Read next frame from source ─────────────────────────────
            frame = self.source.read()
            if frame is None:
                time.sleep(0.02)
                continue

            # Share with YOLO thread (atomic under CPython GIL + Lock)
            with _frame_lock:
                _latest_frame[0] = frame
                _frame_seq[0]   += 1

            now = time.time()

            # ── 2. Zone containment (reads detector.tracked — GIL-safe) ────
            # detector.tracked is updated by the YOLO thread; dict access is
            # serialised by the GIL so no additional lock is needed here.
            inside_zone_ids: set = set()
            zones = self.zone_fn() or []
            if zones:
                fw, fh = self.detector.frame_size
                all_polys = [
                    [(p["x"] * fw, p["y"] * fh) for p in z.get("points", [])]
                    for z in zones
                    if len(z.get("points", [])) >= 3
                ]
                for pid, d in list(self.detector.tracked.items()):
                    if not d.get("box_active"):
                        continue
                    x1, y1, x2, y2 = d["box"]
                    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    for poly in all_polys:
                        if _point_in_polygon(cx, cy, poly):
                            inside_zone_ids.add(pid)
                            break

            # ── 3. Queue AI ingest payload (non-blocking) ──────────────────
            if now - last_send >= config.SEND_INTERVAL_SECONDS:
                payload = build_payload(
                    detector        = self.detector,
                    camera_id       = self.camera_id,
                    frame           = None,
                    include_frame   = False,
                    inside_zone_ids = inside_zone_ids,
                )
                if payload.persons:
                    with _ingest_lock:
                        _ingest_data[0] = (
                            self.camera_id, payload.persons,
                            payload.frame_width, payload.frame_height, payload.timestamp,
                        )
                    _ingest_event.set()
                last_send = now

            # ── 4. Annotate + encode + emit ────────────────────────────────
            # annotate_fn reads from the cache written by the ingest thread;
            # it is a fast OpenCV drawing call that never blocks on YOLO.
            cached    = self.cache_fn(self.camera_id)
            annotated = self.annotate_fn(
                frame,
                cached.get("risk_results",    []),
                cached.get("global_ids",      {}),
                cached.get("effective_times", {}),
                cached.get("person_map",      {}),
            )
            if zones:
                annotated = _draw_zones_cv(annotated, zones)
            if self.frame_hook:
                try:
                    self.frame_hook(annotated)
                except Exception:
                    pass   # never let the recorder crash the stream
            _, buf  = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 65])
            out_b64 = base64.b64encode(buf).decode("utf-8")
            self.socketio.emit("processed_frame", f"data:image/jpeg;base64,{out_b64}")

            # ── 5. Strict pace at STREAM_FPS ───────────────────────────────
            elapsed    = time.time() - t0
            sleep_time = stream_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        yolo_thread.join(timeout=2)
        ingest_thread.join(timeout=2)
        self.source.release()
        print(f"[INFO] VideoWorker[{self.camera_id}]: stopped.")


class VideoWorkerManager:
    """Owns the single active VideoWorker and switches between live/demo sources."""

    def __init__(self, socketio, ingest_fn, annotate_fn, zone_fn, cache_fn, frame_hook=None):
        self._socketio    = socketio
        self._ingest_fn   = ingest_fn
        self._annotate_fn = annotate_fn
        self._zone_fn     = zone_fn
        self._cache_fn    = cache_fn
        self._frame_hook  = frame_hook   # forwarded to each VideoWorker
        self._worker:    "VideoWorker | None" = None
        self._mode:      str  = "stopped"
        self._camera_id: str  = config.CAMERA_ID
        self._source_desc: str = ""
        self._filename:  "str | None" = None

    def switch(self, mode: str, camera_id: str = config.CAMERA_ID, filename: str = None) -> None:
        """Stop the current worker (if any) and start a new one for `mode`.

        `filename` selects which .mp4 under config.DEMO_VIDEOS_DIR to play
        when mode == "demo" (defaults to config.DEFAULT_DEMO_VIDEO_FILENAME,
        or the first file returned by list_demo_videos() if that's unset).
        Ignored for "live" mode.

        Raises FileNotFoundError if the requested source's video file is
        unavailable (missing local file, no demo videos in DEMO_VIDEOS_DIR,
        or the requested filename doesn't exist there).
        Raises ValueError if `filename` is not a bare filename.
        """
        mode = mode.lower()
        if mode not in ("live", "demo"):
            raise ValueError(f"Unknown video mode: {mode!r}")

        if mode == "live":
            source      = LoopingFileSource(config.LIVE_VIDEO_PATH)
            source_desc = config.LIVE_VIDEO_PATH
            filename    = None
        else:
            source      = DemoVideoSource(filename)
            filename    = source.filename
            source_desc = source.path

        # Only stop the previous worker once the new source has loaded successfully,
        # so a failed switch leaves the previous mode running uninterrupted.
        self._stop_current()

        detector = Detector(model=model_manager.get_model())
        worker = VideoWorker(
            camera_id   = camera_id,
            source      = source,
            detector    = detector,
            socketio    = self._socketio,
            ingest_fn   = self._ingest_fn,
            annotate_fn = self._annotate_fn,
            zone_fn     = self._zone_fn,
            cache_fn    = self._cache_fn,
            frame_hook  = self._frame_hook,
        )
        worker.start()

        self._worker      = worker
        self._mode        = mode
        self._camera_id   = camera_id
        self._source_desc = source_desc
        self._filename    = filename
        print(f"[INFO] VideoWorkerManager: switched to '{mode}' mode (source={source_desc}).")

    def _stop_current(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.join(timeout=5)
            self._worker = None

    def status(self) -> dict:
        return {
            "mode":      self._mode,
            "running":   self._worker is not None and self._worker.is_alive(),
            "camera_id": self._camera_id,
            "source":    self._source_desc,
            "filename":  self._filename,
        }
