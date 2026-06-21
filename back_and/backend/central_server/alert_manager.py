"""
Alert lifecycle manager.

Responsibilities:
  1. Trigger evaluation — uses KPI scores to decide which of the 3 alert types fires:
       CLIMBING  — climbing_score alone crossed its threshold
       LOITERING — loitering_score alone crossed its threshold
       COMBINED  — partial overlap; total_person_score crossed the combined threshold
       INTRUSION — pure spatial tripwire event (no KPI threshold)
  2. Deduplication — a 60-second cooldown prevents the same identity/alert-type
     pair from firing repeatedly while a person stays in violation.
  3. AlertDocument construction — embeds all metrics, KPI scores, and trigger_type
     in the schema defined by API_CONTRACT.md.
  4. Dispatch — writes to Firebase and emits a Socket.IO "alert_new" event.
"""
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from shared import config
from shared.schemas import AlertDocument, DetectedPerson, RiskResult
from central_server.firebase_client import FirebaseClient


_SEVERITY: Dict[str, str] = {
    "loitering": "medium",
    "climbing":  "high",
    "combined":  "high",
    "intrusion": "high",
}


class AlertManager:

    def __init__(
        self,
        firebase: FirebaseClient,
        emit_fn:  Optional[Callable] = None,
    ):
        self._firebase   = firebase
        self._emit       = emit_fn
        self._on_alert:  Optional[Callable] = None
        self._cooldowns: Dict[str, float] = {}

    def set_emit_fn(self, fn: Callable):
        self._emit = fn

    def set_on_alert_fn(self, fn: Callable) -> None:
        """Register a callback invoked with the AlertDocument each time an alert fires."""
        self._on_alert = fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        risk_results:    List[RiskResult],
        camera_id:       str,
        person_metrics:  Dict[int, DetectedPerson],
        person_boxes:    Dict[int, List[int]],
        scores_map:      Optional[Dict[str, dict]] = None,
    ):
        """
        For each tracked identity, evaluate all 3 KPI trigger conditions plus
        any pure spatial INTRUSION events, and fire an alert for each that passes
        the cooldown check.
        """
        for result in risk_results:
            scores   = (scores_map or {}).get(result.global_id, {})
            triggers = _collect_triggers(result, scores)

            for alert_type, trigger_type in triggers:
                if not self._cooldown_ok(camera_id, result.global_id, alert_type):
                    continue

                alert = self._build(
                    result, alert_type, trigger_type,
                    camera_id, person_metrics, person_boxes, scores or None,
                )
                self._firebase.push_alert(alert)

                if self._emit:
                    self._emit("alert_new", alert.to_dict())

                if self._on_alert:
                    self._on_alert(alert)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cooldown_ok(self, camera_id: str, global_id: str, alert_type: str) -> bool:
        key  = f"{camera_id}:{global_id}:{alert_type}"
        now  = time.time()
        last = self._cooldowns.get(key, 0.0)
        if now - last > config.ALERT_COOLDOWN_SECONDS:
            self._cooldowns[key] = now
            return True
        return False

    def _build(
        self,
        result:         RiskResult,
        alert_type:     str,
        trigger_type:   str,
        camera_id:      str,
        person_metrics: Dict[int, DetectedPerson],
        person_boxes:   Dict[int, List[int]],
        scores:         Optional[dict],
    ) -> AlertDocument:
        now      = datetime.now(timezone.utc)
        gid_slug = result.global_id.replace("-", "")
        alert_id = f"alert_{now.strftime('%Y%m%d_%H%M%S')}_{gid_slug}"

        person = person_metrics.get(result.local_id)
        box    = person_boxes.get(result.local_id, [])

        location = {
            "last_position":    person.last_position if person else {},
            "bounding_box":     box,
            "zone_name":        result.triggered_rule_name,
            "rule_id":          result.triggered_rule_id,
            "zone_sensitivity": result.zone_sensitivity if result.zone_sensitivity else None,
        }

        metrics: dict = {}
        if person:
            metrics = {
                "time_in_frame_seconds": person.time_in_frame_seconds,
                "zone_returns":          person.zone_returns,
                "area_spread_pixels":    person.area_spread_pixels,
                "avg_movement_pixels":   person.avg_movement_pixels,
                "risk_score":            result.risk_score,
            }

        return AlertDocument(
            alert_id        = alert_id,
            camera_id       = camera_id,
            global_id       = result.global_id,
            alert_type      = alert_type,
            trigger_type    = trigger_type,
            severity        = _SEVERITY.get(alert_type, "medium"),
            status          = "open",
            timestamp_iso   = now.isoformat(),
            timestamp_unix  = now.timestamp(),
            location        = location,
            metrics         = metrics,
            snapshot_url    = None,
            acknowledged_by = None,
            acknowledged_at = None,
            resolved_at     = None,
            scores          = scores,
        )


# ---------------------------------------------------------------------------
# Trigger evaluation  (module-level, pure function — easy to unit-test)
# ---------------------------------------------------------------------------

def _collect_triggers(
    result: RiskResult,
    scores: dict,
) -> List[Tuple[str, str]]:
    """
    Return all (alert_type, trigger_type) pairs that are active for this identity.

    Evaluation order:
      1. INTRUSION  — pure spatial tripwire event; fires independently of KPI scores
      2. CLIMBING   — climbing_score alone crossed CLIMBING_ALERT_THRESHOLD
      3. LOITERING  — loitering_score alone crossed LOITERING_ALERT_THRESHOLD
      4. COMBINED   — total_person_score crossed COMBINED_ALERT_THRESHOLD and
                      neither individual threshold was met

    Multiple conditions can be simultaneously active (e.g., a person can trigger
    both CLIMBING and LOITERING in the same ingest cycle).
    """
    triggers: List[Tuple[str, str]] = []

    # INTRUSION — spatial-only, not mediated by KPI scores
    if "intrusion" in result.alert_types:
        triggers.append(("intrusion", "INTRUSION"))

    if not scores:
        return triggers

    c = scores.get("climbing_score",     0)
    l = scores.get("loitering_score",    0)
    t = scores.get("total_person_score", 0)

    climbing_met  = c >= config.CLIMBING_ALERT_THRESHOLD
    loitering_met = l >= config.LOITERING_ALERT_THRESHOLD
    combined_met  = (
        t >= config.COMBINED_ALERT_THRESHOLD
        and not climbing_met
        and not loitering_met
    )

    if climbing_met:
        triggers.append(("climbing",  "CLIMBING"))
    if loitering_met:
        triggers.append(("loitering", "LOITERING"))
    if combined_met:
        triggers.append(("combined",  "COMBINED"))

    return triggers
