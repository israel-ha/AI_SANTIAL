import os

# Root of the backend/ directory — all paths are relative to this.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load .env from backend/ if python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
CAMERA_ID = os.environ.get("CAMERA_ID", "CAM_1001")
RTSP_USER = os.environ.get("RTSP_USER", "admin")
RTSP_PASS = os.environ.get("RTSP_PASS", "")
RTSP_IP   = os.environ.get("RTSP_IP",   "192.168.0.64")
RTSP_PORT = os.environ.get("RTSP_PORT", "554")
RTSP_PATH = os.environ.get("RTSP_PATH", "live/ch0")
RTSP_URL  = f"rtsp://{RTSP_USER}:{RTSP_PASS}@{RTSP_IP}:{RTSP_PORT}/{RTSP_PATH}"

# No RTSP password → no valid source yet; edge node waits for server to push config.
VIDEO_SOURCE = RTSP_URL if RTSP_PASS else None

# ---------------------------------------------------------------------------
# Edge Node — YOLO / Tracking
# ---------------------------------------------------------------------------
# Look for the model in models/ first, fall back to the legacy backend/ location.
_model_new    = os.path.join(BASE_DIR, "models", "yolov8n.pt")
_model_legacy = os.path.join(BASE_DIR, "yolov8n.pt")
YOLO_MODEL_PATH = _model_new if os.path.exists(_model_new) else _model_legacy

_tracker_new    = os.path.join(BASE_DIR, "models", "tracker.yaml")
_tracker_legacy = os.path.join(BASE_DIR, "tracker.yaml")
TRACKER_CONFIG_PATH = _tracker_new if os.path.exists(_tracker_new) else _tracker_legacy

YOLO_CONF_THRESHOLD = float(os.environ.get("YOLO_CONF_THRESHOLD", 0.25))
YOLO_INPUT_SIZE     = 320   # 320 is ~2× faster than 416 on CPU with minimal accuracy loss
YOLO_EVERY_N_FRAMES = 3

# ---------------------------------------------------------------------------
# Edge Node — Feature Extraction
# ---------------------------------------------------------------------------
FEATURE_INPUT_H = 256           # MobileNet V3 crop height
FEATURE_INPUT_W = 128           # MobileNet V3 crop width
IMAGENET_MEAN   = [0.485, 0.456, 0.406]
IMAGENET_STD    = [0.229, 0.224, 0.225]

# ---------------------------------------------------------------------------
# Edge Node — Tracking state
# ---------------------------------------------------------------------------
PERSON_MEMORY_SECONDS  = 5      # seconds before a lost track is purged
POSITION_HISTORY_LEN   = 60     # max centroid history kept per person
ZONE_VISIT_HISTORY_LEN = 20     # max zone-cluster history kept per person
ZONE_CLUSTER_RADIUS    = 80     # pixels — min distance to create a new zone cluster
SEND_INTERVAL_SECONDS  = 1.0    # seconds between AI payloads sent to Central Server
DETECT_EVERY_N_FRAMES  = int(os.environ.get("DETECT_EVERY_N_FRAMES", 3))   # display frames between detection submissions
STREAM_FPS             = int(os.environ.get("STREAM_FPS", 20))              # target frame rate for the video stream channel

# ---------------------------------------------------------------------------
# Capture resolution (applied to local webcam sources only; RTSP ignores this)
# ---------------------------------------------------------------------------
CAPTURE_WIDTH  = int(os.environ.get("CAPTURE_WIDTH",  640))
CAPTURE_HEIGHT = int(os.environ.get("CAPTURE_HEIGHT", 480))

# ---------------------------------------------------------------------------
# Central Server — Network
# ---------------------------------------------------------------------------
SERVER_HOST     = "0.0.0.0"
SERVER_PORT     = int(os.environ.get("PORT", 5000))
SERVER_BASE_URL = os.environ.get("CENTRAL_SERVER_URL", f"http://localhost:{SERVER_PORT}")
INGEST_URL      = f"{SERVER_BASE_URL}/ingest"

# ---------------------------------------------------------------------------
# Re-ID Engine
# ---------------------------------------------------------------------------
REID_MATCH_THRESHOLD     = 0.42   # cosine similarity — min score to match a gallery entry
REID_NEW_ANGLE_THRESHOLD = 0.92   # similarity below this adds a new viewing angle

# ---------------------------------------------------------------------------
# Spatial Risk Engine
# ---------------------------------------------------------------------------
RISK_ALERT_THRESHOLD       = 70   # accumulated score that triggers an alert

RISK_IN_ZONE_POINTS        = 30   # person centroid is inside a restricted zone
RISK_DWELL_POINTS          = 20   # dwell time >= rule.min_dwell_seconds
RISK_ZONE_RETURNS_POINTS   = 20   # zone return count >= rule.loitering_zone_returns
RISK_CONFINED_POINTS       = 15   # area spread below threshold (barely moving)
RISK_SLOW_POINTS           = 15   # average velocity below threshold (slow / stationary)

LOITERING_AREA_MAX         = 150.0   # pixels — diagonal spread ceiling for "confined"
LOITERING_VELOCITY_MAX     = 5.0     # pixels/frame — velocity ceiling for "slow"

# ---------------------------------------------------------------------------
# Dynamic Threat Memory  (threat_memory.py)
# ---------------------------------------------------------------------------
#
# Time Decay
#   Formula: S(t) = S₀ · e^(−λ · Δt)   where λ = ln(2) / HALF_LIFE
#   A score with no new evidence halves every THREAT_DECAY_HALF_LIFE seconds.
THREAT_DECAY_HALF_LIFE       = 30.0   # seconds — 30 s → ×0.50, 60 s → ×0.25, 90 s → ×0.125

# Evidence Boost
#   Formula: S_new = S + BOOST_RATE · max(0, raw − S)
#   Smoothly closes the gap between current score and raw evidence ceiling.
#   Prevents a single bad frame from jumping directly to 100.
THREAT_BOOST_RATE            = 0.50   # fraction — 0.50 moves halfway to evidence in one cycle

# Zone-Exit Transition
#   Formula: S_new = S · (1 − ZONE_EXIT_PENALTY)
#   Applied exactly once when a tracked ID leaves a high-risk zone polygon.
#   At default 0.40: score=80 drops to 48, falling below the alert threshold.
THREAT_ZONE_EXIT_PENALTY     = 0.40   # fraction — immediate 40 % reduction on zone exit

# Benign Trajectory Reduction
#   Formula: S_new = S · (1 − BENIGN_REDUCTION_RATE · heading_consistency)
#   Applied each ingest cycle while a straight normal walk is sustained.
#   heading_consistency ∈ [0, 1]; 1 = perfectly straight, uniform-speed walk.
THREAT_BENIGN_REDUCTION_RATE = 0.05   # fraction — up to 5 % per cycle for a perfect walk

# Benign Trajectory Detection Thresholds
#   heading_consistency = mean cosine-similarity of consecutive velocity vectors
#   cos_sim(v_i, v_{i+1}) = (v_i · v_{i+1}) / (|v_i| · |v_{i+1}|)
BENIGN_MIN_SAMPLES           = 10     # minimum ingest-cycle velocity samples required
BENIGN_SPEED_MIN             =  5.0   # px / ingest-cycle — filters near-stationary persons
BENIGN_SPEED_MAX             = 50.0   # px / ingest-cycle — filters running / sudden sprints
BENIGN_HEADING_THRESHOLD     =  0.75  # cos ≈ 41.4° — minimum directional consistency

# Stale-track eviction from ThreatMemory (independent of edge PERSON_MEMORY_SECONDS)
THREAT_MEMORY_TTL            = 30.0   # seconds — remove tracks not seen within this window

# ---------------------------------------------------------------------------
# Alert Manager
# ---------------------------------------------------------------------------
ALERT_COOLDOWN_SECONDS = 60     # minimum gap between repeated alerts for the same identity

# KPI score thresholds that determine which of the 3 alert types fires.
# Each threshold can be tuned independently via environment variable.
#
#   CLIMBING  — fires when climbing_score reaches this value (2 events at 25 pts each)
#   LOITERING — fires when loitering_score reaches this value (matches RISK_ALERT_THRESHOLD)
#   COMBINED  — fires when total_person_score reaches this value AND neither individual
#               threshold is met (partial overlap of climbing + loitering activity)
CLIMBING_ALERT_THRESHOLD  = int(os.environ.get("CLIMBING_ALERT_THRESHOLD",  100))
LOITERING_ALERT_THRESHOLD = int(os.environ.get("LOITERING_ALERT_THRESHOLD", 100))
COMBINED_ALERT_THRESHOLD  = int(os.environ.get("COMBINED_ALERT_THRESHOLD",   50))

# ---------------------------------------------------------------------------
# Video Sources (Live = looping local file, Demo = looping local file)
# ---------------------------------------------------------------------------
VIDEOS_DIR = os.path.join(BASE_DIR, "assets", "videos")

LIVE_VIDEO_PATH = os.environ.get(
    "LIVE_VIDEO_PATH", os.path.join(VIDEOS_DIR, "live_demo.mp4")
)
DEFAULT_VIDEO_MODE = os.environ.get("DEFAULT_VIDEO_MODE", "live")

# Demo Mode videos are discovered dynamically by scanning VIDEOS_DIR for .mp4
# files (excluding the live-mode file) — drop a new file in and it becomes
# selectable in the frontend without any code changes (see
# central_server/video_sources.list_demo_videos()).
DEMO_VIDEOS_DIR = VIDEOS_DIR

# Optional: pin the filename used when "demo" mode is selected without an
# explicit filename. If unset, the first file returned by list_demo_videos()
# (alphabetical) is used.
DEFAULT_DEMO_VIDEO_FILENAME = os.environ.get("DEFAULT_DEMO_VIDEO_FILENAME")

# ---------------------------------------------------------------------------
# Firebase
# ---------------------------------------------------------------------------
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON")  # full JSON string (preferred on Azure)
FIREBASE_CRED_PATH = os.environ.get(
    "FIREBASE_CRED_PATH",
    os.path.join(BASE_DIR, "smart-eye-49d8b-firebase-adminsdk-fbsvc-c99e031407.json"),
)
FIREBASE_DB_URL = os.environ.get(
    "FIREBASE_DB_URL",
    "https://smart-eye-49d8b-default-rtdb.europe-west1.firebasedatabase.app",
)
# ---------------------------------------------------------------------------
# Annotation colors  (BGR for OpenCV)
# ---------------------------------------------------------------------------
COLOR_NORMAL    = (0, 255, 0)      # green
COLOR_LOITERING = (0, 165, 255)    # orange
COLOR_CLIMBING  = (0, 0, 255)      # red
COLOR_WARNING   = (0, 200, 200)    # yellow — approaching threshold
