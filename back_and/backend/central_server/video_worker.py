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

from shared import config
from central_server import model_manager
from central_server.video_sources import LoopingFileSource, DemoVideoSource
from edge_node.detector import Detector
from edge_node.payload_builder import build_payload


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
        self.frame_hook  = frame_hook   # called with each emitted frame (for alert recording)
        self._stop       = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        last_send       = 0.0
        last_emit       = 0.0
        stream_interval = 1.0 / max(config.STREAM_FPS, 1)
        frame_interval  = 1.0 / max(self.source.fps, 1.0)

        print(f"[INFO] VideoWorker[{self.camera_id}]: started.")

        while not self._stop.is_set():
            loop_start = time.time()

            frame = self.source.read()
            if frame is None:
                time.sleep(0.05)
                continue

            self.detector.process_frame(frame)

            # Determine which tracked persons are inside any drawn zone.
            # zone_fn returns [{id, points, riskLevel}] (multi-zone format).
            inside_zone_ids: set = set()
            zones = self.zone_fn() or []
            if zones:
                fw, fh = self.detector.frame_size
                # Build pixel-space polygon for every zone with ≥3 points.
                all_polys = [
                    [(p["x"] * fw, p["y"] * fh) for p in z.get("points", [])]
                    for z in zones
                    if len(z.get("points", [])) >= 3
                ]
                for pid, data in self.detector.tracked.items():
                    if not data.get("box_active"):
                        continue
                    x1, y1, x2, y2 = data["box"]
                    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    for poly in all_polys:
                        if _point_in_polygon(cx, cy, poly):
                            inside_zone_ids.add(pid)
                            break

            now = time.time()

            # Run the AI pipeline at most once every SEND_INTERVAL_SECONDS.
            if now - last_send >= config.SEND_INTERVAL_SECONDS:
                payload = build_payload(
                    detector        = self.detector,
                    camera_id       = self.camera_id,
                    frame           = None,
                    include_frame   = False,
                    inside_zone_ids = inside_zone_ids,
                )
                if payload.persons:
                    try:
                        self.ingest_fn(
                            self.camera_id, payload.persons,
                            payload.frame_width, payload.frame_height, payload.timestamp,
                        )
                    except Exception as exc:
                        print(f"[ERROR] VideoWorker[{self.camera_id}]: pipeline error — {exc}")
                last_send = now

            # Annotate and emit at STREAM_FPS.
            if now - last_emit >= stream_interval:
                if self.frame_hook:
                    try:
                        self.frame_hook(frame)
                    except Exception:
                        pass   # never let the recorder crash the live feed
                cached    = self.cache_fn(self.camera_id)
                annotated = self.annotate_fn(
                    frame,
                    cached.get("risk_results",    []),
                    cached.get("global_ids",      {}),
                    cached.get("effective_times", {}),
                    cached.get("person_map",      {}),
                )
                _, buf  = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 75])
                out_b64 = base64.b64encode(buf).decode("utf-8")
                self.socketio.emit("processed_frame", f"data:image/jpeg;base64,{out_b64}")
                last_emit = now

            # Pace the loop to the source's native FPS.
            elapsed = time.time() - loop_start
            time.sleep(max(0.0, frame_interval - elapsed))

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
