"""
KPI Scoring Engine — per-person behavioral scores and alert trigger evaluation.

Computes three decoupled scores as defined in API_CONTRACT.md §8:
  climbing_score:     0-100, increments per climbing detection, decays after 120 s of silence
  loitering_score:    0-100, dwell-time ratio × zone sensitivity multiplier (÷3)
  total_person_score: 0-100, weighted combination (40% climbing + 60% loitering)

Hybrid zone-risk multipliers (applied after base KPI computation when the person
is inside an operator-drawn zone from zone_store):
  Low  → climbing_score × 1.2, loitering_score × 1.2
  Medium → climbing_score × 1.5, loitering_score × 1.5
  High   → both scores forced to 100 (instant alert)

Evaluates three distinct alert trigger conditions (§4.2):
  CLIMBING  — climbing_score alone crossed its threshold
  LOITERING — loitering_score alone crossed its threshold
  COMBINED  — neither individual threshold met, but total_person_score crossed the
               combined threshold (partial overlap of both behaviors)

Thresholds are defined in shared/config.py and can be tuned via environment variables.
"""
from typing import Dict, List, Optional, Tuple

from shared import config

_CLIMBING_POINTS_PER_EVENT = 25
_CLIMBING_DECAY_SECONDS    = 120


class ScoringEngine:

    def __init__(self):
        self._climb_counts:  Dict[str, int]   = {}   # global_id → event count
        self._climb_last_ts: Dict[str, float] = {}   # global_id → last climbing ts

    def compute(
        self,
        global_id:         str,
        alert_types:       list,
        eff_time_seconds:  float,
        min_dwell_seconds: int,
        zone_sensitivity:  int,            # 1-5; pass 0 if person is not in a loitering zone
        now:               float,
        zone_risk_level:   Optional[str] = None,  # "Low" | "Medium" | "High" from zone_store
    ) -> dict:
        """
        Compute KPI scores and determine which alert triggers are active.

        Returns a dict with keys:
          climbing_score, loitering_score, total_person_score  — integers 0-100
          triggers — list of (alert_type, trigger_type) tuples that are active this cycle
        """
        climbing_score  = self._climb_score(global_id, alert_types, now)
        loitering_score = self._loiter_score(eff_time_seconds, min_dwell_seconds, zone_sensitivity)

        # Apply hybrid zone-risk multipliers when inside an operator-drawn zone.
        if zone_risk_level == "High":
            climbing_score  = 100
            loitering_score = 100
        elif zone_risk_level == "Medium":
            climbing_score  = min(round(climbing_score  * 1.5), 100)
            loitering_score = min(round(loitering_score * 1.5), 100)
        elif zone_risk_level == "Low":
            climbing_score  = min(round(climbing_score  * 1.2), 100)
            loitering_score = min(round(loitering_score * 1.2), 100)

        total_person_score = round(0.4 * climbing_score + 0.6 * loitering_score)
        triggers           = self._evaluate_triggers(climbing_score, loitering_score, total_person_score)

        return {
            "climbing_score":     climbing_score,
            "loitering_score":    loitering_score,
            "total_person_score": total_person_score,
            "triggers":           triggers,
        }

    def purge_stale(self, now: float, ttl: float = 300.0) -> None:
        """Remove state for identities not seen for longer than ttl seconds."""
        stale = [gid for gid, ts in self._climb_last_ts.items() if now - ts > ttl]
        for gid in stale:
            self._climb_counts.pop(gid, None)
            self._climb_last_ts.pop(gid, None)

    # ------------------------------------------------------------------

    def _climb_score(self, global_id: str, alert_types: list, now: float) -> int:
        is_climbing = "climbing" in alert_types
        last_ts     = self._climb_last_ts.get(global_id, 0.0)

        if is_climbing:
            self._climb_counts[global_id]  = self._climb_counts.get(global_id, 0) + 1
            self._climb_last_ts[global_id] = now
        elif now - last_ts > _CLIMBING_DECAY_SECONDS:
            self._climb_counts[global_id] = 0

        return min(self._climb_counts.get(global_id, 0) * _CLIMBING_POINTS_PER_EVENT, 100)

    @staticmethod
    def _loiter_score(eff_time: float, min_dwell: int, zone_sensitivity: int) -> int:
        if zone_sensitivity <= 0 or min_dwell <= 0:
            return 0
        raw = min((eff_time / min_dwell) * 100.0, 100.0)
        return min(round(raw * (zone_sensitivity / 3.0)), 100)

    @staticmethod
    def _evaluate_triggers(
        climbing_score:     int,
        loitering_score:    int,
        total_person_score: int,
    ) -> List[Tuple[str, str]]:
        """
        Evaluate which of the 3 KPI-based alert conditions are active.

        Returns a list of (alert_type, trigger_type) tuples:
          ("climbing",  "CLIMBING")  — pure climbing threshold crossed
          ("loitering", "LOITERING") — pure loitering threshold crossed
          ("combined",  "COMBINED")  — partial combination threshold crossed

        Note: INTRUSION (tripwire/spatial) is handled separately in AlertManager
        because it is a pure spatial event with no KPI threshold.
        """
        triggers: List[Tuple[str, str]] = []

        climbing_met  = climbing_score  >= config.CLIMBING_ALERT_THRESHOLD
        loitering_met = loitering_score >= config.LOITERING_ALERT_THRESHOLD
        combined_met  = (
            total_person_score >= config.COMBINED_ALERT_THRESHOLD
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
