"""
Entry point — Central Server only.

Start this first, then start run_edge.py in a separate terminal.

Usage:
    python run_server.py
"""
import logging
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt= "%H:%M:%S",
)
# Suppress noisy third-party loggers
logging.getLogger("ultralytics").setLevel(logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

from shared import config
from central_server.app import create_app, socketio, purge_debug_rules, sync_rules_from_firebase

if __name__ == "__main__":
    print("=" * 60)
    print("  SMART EYE — Central Server")
    print("=" * 60)
    print(f"[INFO] Host       : {config.SERVER_HOST}:{config.SERVER_PORT}")
    print(f"[INFO] Camera ID  : {config.CAMERA_ID}")
    print(f"[INFO] Firebase DB: {config.FIREBASE_DB_URL}")
    print()

    print("[BOOT] Step 1/4 — creating Flask app + registering blueprints …")
    app = create_app()
    print("[BOOT] Step 1/4 — Flask app created.")

    # 1. Remove any dummy zones left over from a previous debug session.
    print("[BOOT] Step 2/4 — purging debug rules …")
    purge_debug_rules()
    print("[BOOT] Step 2/4 — done.")

    # 2. Fetch operator-configured rules from Firebase so the Spatial Engine
    #    has the correct polygons from the very first detection frame.
    print("[BOOT] Step 3/4 — syncing rules from Firebase …")
    sync_rules_from_firebase()
    print("[BOOT] Step 3/4 — done.")

    print("[BOOT] Step 4/4 — starting socketio server …")
    print()
    print("[INFO] Server ready. Waiting for Edge Node and Frontend connections …")
    print("[INFO] Frontend WebSocket : ws://localhost:5000")
    print("[INFO] Rules REST API     : http://localhost:5000/api/rules")
    print("[INFO] Health check       : http://localhost:5000/api/health")
    print()

    socketio.run(
        app,
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        debug=False,
    )
