"""
Singleton YOLO model loader.

Loading YOLOv8 weights (and the underlying torch model) is expensive — both in
time and memory. In the merged cloud architecture, the same weights must be
shared by every VideoWorker created over the lifetime of the process (e.g. when
the user toggles between Live and Demo video sources), instead of being
reloaded from disk on every switch.
"""
import os
import threading

# Must be set before ultralytics is imported — ultralytics reads YOLO_AUTOINSTALL
# once at module load time to decide whether to run pip auto-install for lap.
# With lap==0.5.13 pinned this check passes instantly, but the env var is a
# second layer of defence so a stale pip cache never triggers a startup delay.
os.environ.setdefault("YOLO_AUTOINSTALL", "False")

from ultralytics import YOLO

from shared import config

_lock  = threading.Lock()
_model = None


def get_model() -> YOLO:
    """Return the process-wide YOLO model, loading it on first use."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                print(f"[INFO] ModelManager: loading weights from {config.YOLO_MODEL_PATH} …")
                _model = YOLO(config.YOLO_MODEL_PATH)
                # Warmup removed — running model.track() on a dummy tensor during startup
                # caused a GIL / threading deadlock under Flask-SocketIO (threading mode).
                # PyTorch initialises its own thread pool lazily on the first real inference
                # frame, which is fast enough and avoids the deadlock entirely.
                print("[INFO] ModelManager: model loaded and ready.")
    return _model


def is_loaded() -> bool:
    return _model is not None
