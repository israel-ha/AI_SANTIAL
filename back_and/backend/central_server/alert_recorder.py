"""
AlertRecorder — rolling pre-alert frame buffer + background video clip writer.

Architecture
------------
push_frame(frame) is called by VideoWorker at STREAM_FPS whenever the worker
emits a frame to the frontend.  Frames are stored in a fixed-size deque
(BUFFER_SIZE frames ≈ 5 s pre-alert context).

trigger(...) is called by AlertManager the moment a confirmed alert is
dispatched.  It:
  1. Snapshots the pre-alert deque
  2. Arms the post-alert capture (POST_FRAMES additional frames)
  3. When the post-capture is complete, ships a write job to a background
     thread so the live feed is never blocked

The background writer uses cv2.VideoWriter to produce an .mp4 clip and
upserts the alert's metadata into assets/alerts/alerts_db.json (newest-first
list, capped at 500 entries).
"""
import collections
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import cv2

from shared import config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BUFFER_SIZE = 50       # pre-alert frames in rolling deque  (≈ 5 s at 10 fps)
POST_FRAMES = 50       # frames captured after the alert    (≈ 5 s at 10 fps)
WRITE_FPS   = float(max(config.STREAM_FPS, 1))

# Minimum seconds between climbing clip recordings per camera.
# Belt-and-suspenders guard: AlertManager already gates at 10 s per local_id;
# this guard prevents back-to-back trigger() calls from abandoning each other's
# in-progress recordings (newer trigger overwrites _post_frames and _pending_meta).
_CLIMB_CLIP_COOLDOWN = 10.0

ALERTS_DIR = os.path.join(config.BASE_DIR, "assets", "alerts")
ALERTS_DB  = os.path.join(ALERTS_DIR, "alerts_db.json")

# Human-readable label map
_EVENT_LABEL = {
    "climbing":       "Climbing",
    "loitering":      "Loitering",
    "intrusion":      "Zone Intrusion",
    "combined":       "Zone Intrusion",
    "zone_intrusion": "Zone Intrusion",
}


class AlertRecorder:
    """Thread-safe rolling-buffer video recorder for alert clips."""

    def __init__(self) -> None:
        os.makedirs(ALERTS_DIR, exist_ok=True)

        self._buffer: collections.deque = collections.deque(maxlen=BUFFER_SIZE)
        self._lock = threading.Lock()

        # State for ongoing post-alert capture (None = idle)
        self._post_frames: Optional[list] = None
        self._post_needed: int = 0
        self._pending_meta: Optional[dict] = None

        # Per-camera climbing clip cooldown (camera_id → last armed unix timestamp)
        self._last_climb_ts: Dict[str, float] = {}

        # Background writer
        self._write_queue: queue.Queue = queue.Queue()
        threading.Thread(
            target=self._writer_loop, daemon=True, name="alert-recorder-writer"
        ).start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push_frame(self, frame) -> None:
        """Feed a live frame into the rolling pre-alert buffer.

        Called at STREAM_FPS from VideoWorker.  Must be fast — no I/O here.
        """
        copied = frame.copy()
        self._buffer.append(copied)

        with self._lock:
            if self._post_frames is not None and self._post_needed > 0:
                self._post_frames.append(copied)
                self._post_needed -= 1
                if self._post_needed <= 0:
                    # Post-alert capture complete — hand off to the writer
                    self._write_queue.put((self._pending_meta, list(self._post_frames)))
                    self._post_frames  = None
                    self._pending_meta = None

    def trigger(
        self,
        alert_id:        str,
        alert_type:      str,
        score:           int,
        camera_id:       str,
        zone_risk_level: Optional[str] = None,
    ) -> None:
        """Arm a clip recording centred on this alert.

        Snapshots the current pre-alert buffer and starts collecting
        POST_FRAMES additional frames from subsequent push_frame() calls.
        If a recording is already in progress it is replaced (newer alert wins).
        Thread-safe.
        """
        # Gate climbing clips: prevent a ReID-induced storm from spawning
        # back-to-back triggers that each overwrite the previous recording.
        if alert_type == "climbing":
            now_ts   = time.time()
            last_ts  = self._last_climb_ts.get(camera_id, 0.0)
            elapsed  = now_ts - last_ts
            if elapsed < _CLIMB_CLIP_COOLDOWN:
                print(
                    f"[INFO] AlertRecorder: climbing clip suppressed — "
                    f"cooldown active ({elapsed:.1f}s < {_CLIMB_CLIP_COOLDOWN}s)"
                )
                return
            self._last_climb_ts[camera_id] = now_ts

        now      = datetime.now(timezone.utc)
        slug     = now.strftime("%Y%m%d_%H%M%S")
        filename = f"alert_{slug}_{alert_id[-8:]}.mp4"

        meta = {
            "id":              alert_id,
            "timestamp":       now.isoformat(),
            "eventType":       _EVENT_LABEL.get(alert_type, "Zone Intrusion"),
            "alert_type":      alert_type,
            "score":           score,
            "camera_id":       camera_id,
            "zone_risk_level": zone_risk_level,
            "filename":        filename,
            "videoPath":       os.path.join(ALERTS_DIR, filename),
        }

        with self._lock:
            pre_frames         = list(self._buffer)   # snapshot
            self._post_frames  = list(pre_frames)     # seed post-list with pre-alert frames
            self._post_needed  = POST_FRAMES
            self._pending_meta = meta

        print(
            f"[INFO] AlertRecorder: clip armed — {filename} "
            f"({len(pre_frames)} pre-frames buffered)"
        )

    def get_alerts(self) -> list:
        """Return persisted alert metadata, newest first."""
        return _load_db()

    def get_video_path(self, filename: str) -> str:
        """Resolve a bare filename to its absolute path within ALERTS_DIR."""
        return os.path.join(ALERTS_DIR, os.path.basename(filename))

    # ------------------------------------------------------------------
    # Background writer (runs in its own daemon thread)
    # ------------------------------------------------------------------

    def _writer_loop(self) -> None:
        while True:
            meta, frames = self._write_queue.get()
            try:
                self._write_clip(meta, frames)
            except Exception as exc:
                print(f"[ERROR] AlertRecorder: failed to write clip — {exc}")

    def _write_clip(self, meta: dict, frames: list) -> None:
        if not frames:
            print("[WARNING] AlertRecorder: no frames available — clip skipped.")
            return

        path     = meta["videoPath"]
        tmp_path = path + ".tmp.mp4"
        h, w     = frames[0].shape[:2]

        # Try H.264 first (browser-native, no decode delay); fall back to MPEG-4.
        writer = None
        for fourcc_str in ("avc1", "X264", "mp4v"):
            fourcc = cv2.VideoWriter_fourcc(*fourcc_str)
            candidate = cv2.VideoWriter(tmp_path, fourcc, WRITE_FPS, (w, h))
            if candidate.isOpened():
                writer = candidate
                print(f"[INFO] AlertRecorder: codec '{fourcc_str}' selected")
                break
            candidate.release()

        if writer is None:
            print("[ERROR] AlertRecorder: no working video codec — clip skipped.")
            return

        for frm in frames:
            writer.write(frm)
        writer.release()

        # Re-encode with ffmpeg: H.264 + moov-faststart so the browser can start
        # playing immediately without downloading the whole file first.
        _ffmpeg_faststart(tmp_path, path)
        print(f"[INFO] AlertRecorder: saved {len(frames)}-frame clip → {path}")

        # Persist metadata (upsert by alert id, newest first, max 500 rows)
        db = _load_db()
        db = [e for e in db if e.get("id") != meta["id"]]
        db.insert(0, meta)
        db = db[:500]
        _save_db(db)


# ---------------------------------------------------------------------------
# ffmpeg post-processing
# ---------------------------------------------------------------------------

def _ffmpeg_faststart(src: str, dst: str) -> None:
    """Re-encode with H.264 + moov-faststart for instant browser streaming.

    Moves the moov atom to the front of the file so the browser can begin
    playback before the download completes.  Falls back to a plain file rename
    if ffmpeg is not installed or the encode fails.
    """
    if shutil.which("ffmpeg"):
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i",        src,
                    "-c:v",      "libx264",
                    "-preset",   "fast",
                    "-crf",      "23",
                    "-movflags", "+faststart",
                    dst,
                ],
                capture_output=True,
                timeout=120,
            )
            if os.path.exists(src):
                os.remove(src)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.decode(errors="replace")[:300])
            return
        except Exception as exc:
            print(f"[WARNING] AlertRecorder: ffmpeg faststart failed ({exc}); keeping raw file.")
            # fall through to rename
    # ffmpeg not available or failed — move temp file to final path as-is
    if os.path.exists(src):
        shutil.move(src, dst)


# ---------------------------------------------------------------------------
# JSON DB helpers  (module-level so they can be tested independently)
# ---------------------------------------------------------------------------

def _load_db() -> list:
    if not os.path.exists(ALERTS_DB):
        return []
    try:
        with open(ALERTS_DB, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def _save_db(entries: list) -> None:
    with open(ALERTS_DB, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)
