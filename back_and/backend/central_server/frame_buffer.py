"""
Thread-safe single-slot JPEG frame buffer.

The VideoWorker pushes the latest encoded JPEG here; the /api/stream.mjpeg
generator polls it at ~30 fps.  Only the newest frame is kept — if the
consumer is slower than the producer the oldest frames are simply overwritten,
which is the correct behaviour for a live video stream (drop, never buffer).
"""
import threading

_lock:  threading.Lock = threading.Lock()
_frame: bytes          = b""


def push(jpeg_bytes: bytes) -> None:
    global _frame
    with _lock:
        _frame = jpeg_bytes


def latest() -> bytes:
    with _lock:
        return _frame
