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
import uuid

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

    def __init__(self, camera_id, source, detector, socketio, ingest_fn, annotate_fn, zone_fn, cache_fn,
                 run_id=None, active_run_id=None, frame_hook=None):
        super().__init__(daemon=True, name=f"video-worker-{camera_id}")
        self.camera_id      = camera_id
        self.source         = source
        self.detector       = detector
        self.socketio       = socketio
        self.ingest_fn      = ingest_fn
        self.annotate_fn    = annotate_fn
        self.zone_fn        = zone_fn
        self.cache_fn       = cache_fn
        self.frame_hook     = frame_hook
        self.run_id         = run_id            # unique epoch for this worker instance
        self._active_run_id = active_run_id     # shared [run_id] ref from VideoWorkerManager
        self._stop_event    = threading.Event()

    def stop(self):
        self._stop_event.set()

    def _superseded(self) -> bool:
        """True when the manager has started a newer worker — this instance is a zombie."""
        return (
            self._active_run_id is not None and
            self.run_id != self._active_run_id[0]
        )

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
            while not self._stop_event.is_set() and not self._superseded():
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
            while not self._stop_event.is_set() and not self._superseded():
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

        # Hardcap at 15 fps: enough for CCTV-quality playback while halving the
        # annotation + encode + emit CPU load compared to native 25-30 fps.
        # The compensated sleep (stream_interval - elapsed) keeps pace exactly —
        # loop overhead is absorbed so the hardcap is the true emitted rate.
        target_fps      = min(self.source.fps, 15.0)
        stream_interval = 1.0 / max(target_fps, 1.0)
        last_send       = 0.0

        print(
            f"[INFO] VideoWorker[{self.camera_id}]: started — "
            f"stream={target_fps:.1f} fps (hardcapped)  source={self.source.fps:.1f} fps  "
            f"yolo=continuous background  run_id={self.run_id}"
        )

        try:
            while not self._stop_event.is_set() and not self._superseded():
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

                # Zombie / stop guard — last checkpoint before touching the socket.
                # Catches the exact race where stop() fired while this thread was
                # sleeping in step 5 and it has just woken up ready to emit.
                if self._stop_event.is_set() or self._superseded():
                    break

                # Downscale to ≤640 px wide before encoding.  Annotations are already
                # drawn at full resolution; shrinking here cuts JPEG payload size and
                # cv2.imencode CPU time without affecting YOLO (which always receives
                # the full-res frame via the shared frame slot).
                out_h, out_w = annotated.shape[:2]
                if out_w > 640:
                    annotated = cv2.resize(
                        annotated, (640, int(out_h * 640 / out_w)),
                        interpolation=cv2.INTER_LINEAR,
                    )

                _, buf  = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 60])
                out_b64 = base64.b64encode(buf).decode("utf-8")
                self.socketio.emit("processed_frame", f"data:image/jpeg;base64,{out_b64}")

                # ── 5. Strict pace at STREAM_FPS ───────────────────────────────
                elapsed    = time.time() - t0
                sleep_time = stream_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            # Guaranteed cleanup even if the loop exits via exception or epoch mismatch.
            # Releasing the source cuts off its frame supply immediately.
            yolo_thread.join(timeout=2)
            ingest_thread.join(timeout=2)
            self.source.release()
            print(
                f"[INFO] VideoWorker[{self.camera_id}]: stopped "
                f"(run_id={self.run_id}  superseded={self._superseded()})."
            )


class VideoWorkerManager:
    """Owns the single active VideoWorker and switches between live/demo sources."""

    def __init__(self, socketio, ingest_fn, annotate_fn, zone_fn, cache_fn,
                 clear_cache_fn=None, frame_hook=None):
        self._socketio       = socketio
        self._ingest_fn      = ingest_fn
        self._annotate_fn    = annotate_fn
        self._zone_fn        = zone_fn
        self._cache_fn       = cache_fn
        self._clear_cache_fn = clear_cache_fn   # (camera_id) → clears stale annotation cache on switch
        self._frame_hook     = frame_hook
        self._worker:    "VideoWorker | None" = None
        self._mode:      str  = "stopped"
        self._camera_id: str  = config.CAMERA_ID
        self._source_desc: str = ""
        self._filename:  "str | None" = None
        self._switch_lock   = threading.Lock()   # prevents overlapping switch() calls
        self._active_run_id = [None]              # mutable epoch shared with the current VideoWorker

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
        Returns immediately (no-op) if a previous switch is still in progress.
        """
        if not self._switch_lock.acquire(blocking=False):
            print(f"[INFO] VideoWorkerManager: switch() already in progress — ignoring duplicate call (mode={mode!r}).")
            return
        try:
            self._do_switch(mode, camera_id, filename)
        finally:
            self._switch_lock.release()

    def _do_switch(self, mode: str, camera_id: str, filename) -> None:
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

        # Hard stop: block until the previous worker thread is confirmed dead.
        # This guarantees no frame emission overlap between old and new workers.
        self._stop_current()

        # Flush stale bounding-box cache so the new video's first frames are
        # rendered clean — no boxes from the previous video bleeding through.
        if self._clear_cache_fn is not None:
            self._clear_cache_fn(camera_id)

        # Advance the epoch BEFORE starting the new worker.
        # Any zombie thread that survived the join timeout will call _superseded(),
        # see a run_id mismatch, and self-terminate before its next emit().
        new_run_id = uuid.uuid4()
        self._active_run_id[0] = new_run_id

        detector = Detector(model=model_manager.get_model())
        worker = VideoWorker(
            camera_id      = camera_id,
            source         = source,
            detector       = detector,
            socketio       = self._socketio,
            ingest_fn      = self._ingest_fn,
            annotate_fn    = self._annotate_fn,
            zone_fn        = self._zone_fn,
            cache_fn       = self._cache_fn,
            run_id         = new_run_id,
            active_run_id  = self._active_run_id,
            frame_hook     = self._frame_hook,
        )
        worker.start()

        self._worker      = worker
        self._mode        = mode
        self._camera_id   = camera_id
        self._source_desc = source_desc
        self._filename    = filename
        print(f"[INFO] VideoWorkerManager: switched to '{mode}' mode (source={source_desc}).")

    def _stop_current(self) -> None:
        if self._worker is None:
            return
        self._worker.stop()
        # VideoWorker.run() joins its two sub-threads (yolo + ingest, 2 s each)
        # before exiting — typical stop is < 5 s.  8 s gives a generous margin.
        self._worker.join(timeout=8)
        if self._worker.is_alive():
            print(
                f"[WARNING] VideoWorkerManager: worker {self._worker.name} "
                f"did not stop within 8 s — proceeding anyway (thread is daemon)."
            )
        self._worker = None   # always clear; stale daemon thread will die with the process

    def status(self) -> dict:
        return {
            "mode":      self._mode,
            "running":   self._worker is not None and self._worker.is_alive(),
            "camera_id": self._camera_id,
            "source":    self._source_desc,
            "filename":  self._filename,
        }
