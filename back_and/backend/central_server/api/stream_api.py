"""
Socket.IO event handlers for the video-stream namespace.
Kept separate from app.py so the app factory stays uncluttered.

Registered in app.py via:
    from central_server.api.stream_api import register_socket_events
    register_socket_events(socketio)
"""
import uuid

from flask_socketio import SocketIO

from central_server import zone_store

# sid → camera_id subscriptions kept in memory (cleared on disconnect).
_subscriptions: dict = {}


def register_socket_events(socketio: SocketIO):

    @socketio.on("subscribe_camera")
    def on_subscribe(data):
        from flask import request as flask_req
        camera_id = data.get("camera_id")
        if camera_id:
            _subscriptions[flask_req.sid] = camera_id
            print(f"[INFO] Socket.IO: client {flask_req.sid} subscribed to {camera_id}")

    @socketio.on("update_restricted_zone")
    def on_update_restricted_zone(data):
        """
        Receive zone updates from the frontend.

        New multi-zone format (preferred):
            {"zones": [{"id": "...", "points": [{x, y}], "riskLevel": "Low|Medium|High"}, ...]}

        Legacy single-zone format (auto-migrated):
            {"zone": [{"x": ..., "y": ...}, ...]}

        Saves zones to disk and broadcasts restricted_zones_updated to all
        connected clients so every frontend immediately reflects the change.
        """
        zones = data.get("zones")

        if zones is None:
            # Back-compat: old single-zone payload → wrap as one Medium zone
            points = data.get("zone", [])
            if points:
                zones = [{
                    "id":        f"zone_{uuid.uuid4().hex[:8]}",
                    "points":    points,
                    "riskLevel": "Medium",
                }]
            else:
                zones = []

        zone_store.save(zones)
        socketio.emit("restricted_zones_updated", {"zones": zones})
        print(f"[INFO] Socket.IO: zones updated ({len(zones)} zone(s)).")
        return {"status": "success", "message": f"Zones updated ({len(zones)} zone(s))"}

    @socketio.on("disconnect")
    def on_disconnect():
        from flask import request as flask_req
        _subscriptions.pop(flask_req.sid, None)
        print(f"[INFO] Socket.IO: client {flask_req.sid} disconnected.")
