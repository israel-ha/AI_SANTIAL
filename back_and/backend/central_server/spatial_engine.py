"""
Spatial Logic Engine — the core false-alarm filter.

Evaluates each detected person against:
  1. Active spatial Rules (from RulesStore) — loitering, climbing, tripwire
  2. Operator-drawn sensitive zones (from zone_store) — with hybrid risk-level logic

Hybrid zone multipliers (applied to base spatial score):
  Low  risk zone → base_score × 1.2
  Medium risk zone → base_score × 1.5
  High risk zone  → instant score = 100 (no-go zone, always triggers)

When a person is in multiple zones the highest-severity zone wins.
When both a drawn zone and a rule apply the higher spatial score wins,
but zone_risk_level is ALWAYS set if the person is inside any drawn zone
so the ScoringEngine can apply KPI multipliers regardless of which path won.

Rule-based score composition (see config.py for point values):
  +30  centroid is inside a restricted zone
  +20  dwell time >= rule.min_dwell_seconds
  +20  zone return count >= rule.loitering_zone_returns
  +15  area spread < LOITERING_AREA_MAX
  +15  avg movement < LOITERING_VELOCITY_MAX

Tripwire rules are binary: a crossing immediately returns score = 100.

All geometry is stored in normalised [0,1] space and converted to pixel
coordinates per-frame using the payload's frame dimensions.
"""
from typing import Dict, List, Optional, Tuple

from shared import config
from shared.schemas import Rule, RuleGeometry, DetectedPerson, RiskResult


# Severity priority used to pick the "worst" zone when a person overlaps several.
_RISK_PRIORITY: Dict[str, int] = {"Low": 1, "Medium": 2, "High": 3}

# Score multipliers for Low and Medium zones; High always returns 100 directly.
_RISK_MULTIPLIER: Dict[str, float] = {"Low": 1.2, "Medium": 1.5}


# ---------------------------------------------------------------------------
# Pure geometry helpers
# ---------------------------------------------------------------------------

def _point_in_polygon(px: float, py: float, poly: List[Tuple[float, float]]) -> bool:
    """Ray-casting algorithm — O(n) where n = number of polygon vertices."""
    n      = len(poly)
    inside = False
    j      = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / (yj - yi) + xi
        ):
            inside = not inside
        j = i
    return inside


def _segments_cross(p1, p2, p3, p4) -> bool:
    """Returns True if segment p1→p2 crosses segment p3→p4."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    d1 = cross(p3, p4, p1)
    d2 = cross(p3, p4, p2)
    d3 = cross(p1, p2, p3)
    d4 = cross(p1, p2, p4)
    return (
        ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and
        ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0))
    )


def _to_pixels(geometry: RuleGeometry, fw: int, fh: int) -> List[Tuple[float, float]]:
    """Convert normalised RuleGeometry points → pixel coordinates."""
    return [(p.x * fw, p.y * fh) for p in geometry.points]


def _raw_to_pixels(points: list, fw: int, fh: int) -> List[Tuple[float, float]]:
    """Convert zone_store raw {x, y} dicts → pixel coordinates."""
    return [(p["x"] * fw, p["y"] * fh) for p in points]


def _near_any_zone(cx: float, cy: float, px_zones: list, buffer: float = 80.0) -> bool:
    """True if (cx, cy) is within `buffer` pixels of any drawn zone's bounding box.

    Used to gate climbing detection to subjects near the fence/gate area when
    the operator has drawn zones.  A bounding-box expansion is deliberately
    simple: it is fast, explainable, and covers every convex zone shape well.
    """
    for z in px_zones:
        pts = z.get("pixels", [])
        if len(pts) < 3:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if (min(xs) - buffer <= cx <= max(xs) + buffer and
                min(ys) - buffer <= cy <= max(ys) + buffer):
            return True
    return False


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SpatialEngine:
    """Stateless evaluator — create once, call evaluate() on every ingest cycle."""

    def evaluate(
        self,
        persons:         List[DetectedPerson],
        rules:           List[Rule],
        frame_width:     int,
        frame_height:    int,
        global_ids:      dict,   # {str(local_id): "G-N"}
        effective_times: dict,   # {str(local_id): seconds}
        zones:           Optional[list] = None,   # zone_store list: [{id, points, riskLevel}]
    ) -> List[RiskResult]:
        """
        Evaluate every person against every active rule AND every drawn zone.

        Returns one RiskResult per person, ordered the same as `persons`.
        """
        active_rules = [r for r in rules if r.active]

        # Pre-convert rule geometries to pixel space (done once per call).
        px_geoms: Dict[str, List[Tuple]] = {
            r.rule_id: _to_pixels(r.geometry, frame_width, frame_height)
            for r in active_rules
        }

        # Pre-convert drawn zone points to pixel space (skip degenerate zones).
        px_zones = []
        for z in (zones or []):
            pts = z.get("points", [])
            if len(pts) >= 3:
                px_zones.append({
                    "id":        z.get("id", ""),
                    "pixels":    _raw_to_pixels(pts, frame_width, frame_height),
                    "riskLevel": z.get("riskLevel", "Medium"),
                })

        results = []
        for person in persons:
            local_key = str(person.person_id)
            gid       = global_ids.get(local_key, f"L-{person.person_id}")
            eff_time  = effective_times.get(local_key, int(person.time_in_frame_seconds))

            cx = float(person.last_position["x"])
            cy = float(person.last_position["y"])

            # ── 1. Rule-based scoring (existing logic) ──────────────────
            best_rule_score = 0
            best_rule: Optional[Rule] = None

            for rule in active_rules:
                score = self._score_rule(person, rule, px_geoms[rule.rule_id], cx, cy, eff_time)
                if score > best_rule_score:
                    best_rule_score = score
                    best_rule       = rule

            # ── 2. Zone-based hybrid scoring ────────────────────────────
            # Find EVERY drawn zone the person is currently inside.
            matched_zones = [
                z for z in px_zones if _point_in_polygon(cx, cy, z["pixels"])
            ]

            best_zone_score = 0
            top_zone: Optional[dict] = None

            if matched_zones:
                # Pick the highest-priority (most severe) zone.
                matched_zones.sort(
                    key=lambda z: _RISK_PRIORITY.get(z["riskLevel"], 0),
                    reverse=True,
                )
                top_zone        = matched_zones[0]
                best_zone_score = self._score_zone_hybrid(
                    person, top_zone["pixels"], cx, cy, eff_time, top_zone["riskLevel"]
                )

            # ── 3. Merge: pick the higher spatial score ─────────────────
            # zone_risk_level is always forwarded if the person is in ANY drawn zone,
            # so the ScoringEngine can apply KPI multipliers even when the rule wins.
            zone_risk_level = top_zone["riskLevel"] if top_zone else None

            if best_zone_score >= best_rule_score:
                final_score = best_zone_score
                final_rule  = None
                final_sens  = 3     # neutral default sensitivity for zone-store zones
                final_dwell = config.ZONE_DEFAULT_DWELL_SECONDS
                alert_types = ["intrusion"] if best_zone_score >= config.RISK_ALERT_THRESHOLD else []
            else:
                final_score = best_rule_score
                final_rule  = best_rule
                final_sens  = (best_rule.sensitivity_level
                               if (best_rule and best_rule.rule_type == "zone") else 0)
                final_dwell = (best_rule.conditions.min_dwell_seconds if best_rule else 30)
                in_zone_rule = final_score >= config.RISK_ALERT_THRESHOLD
                # Zone-type rules always emit "intrusion" — their alert_type field is
                # NOT used to fire climbing, preventing zone membership from masquerading
                # as kinematic detection.  Tripwire rules keep their own alert_type.
                if in_zone_rule and best_rule:
                    alert_types = (
                        ["intrusion"] if best_rule.rule_type == "zone"
                        else [best_rule.alert_type]
                    )
                else:
                    alert_types = []

            # Kinematic climbing detection — fires ONLY on Y-axis displacement evidence.
            # Condition A (spatial gate): if the operator has drawn any zones, only
            # subjects within CLIMB_PROXIMITY_PX of a zone bounding box are checked.
            # This eliminates false positives from subjects far from the fence.
            # If no zones are drawn the gate is skipped and climbing fires globally.
            # This check is INDEPENDENT of whether the person triggered intrusion —
            # the two alerts have completely separate trigger paths.
            CLIMB_PROXIMITY_PX = 80.0
            near_fence = not px_zones or _near_any_zone(cx, cy, px_zones, CLIMB_PROXIMITY_PX)
            if near_fence and self._is_climbing(person) and "climbing" not in alert_types:
                alert_types = list(alert_types) + ["climbing"]

            in_zone = final_score >= config.RISK_ALERT_THRESHOLD

            results.append(RiskResult(
                global_id           = gid,
                local_id            = person.person_id,
                risk_score          = min(final_score, 100),
                alert_types         = alert_types,
                triggered_rule_id   = final_rule.rule_id   if final_rule else None,
                triggered_rule_name = final_rule.name      if final_rule else None,
                in_zone             = in_zone,
                zone_sensitivity    = final_sens,
                min_dwell_seconds   = final_dwell,
                zone_risk_level     = zone_risk_level,
            ))

        return results

    # ------------------------------------------------------------------
    # Rule scoring (unchanged from original)
    # ------------------------------------------------------------------

    def _score_rule(self, person, rule, pixel_pts, cx, cy, eff_time) -> int:
        if rule.rule_type == "zone":
            return self._score_rule_zone(person, rule, pixel_pts, cx, cy, eff_time)
        elif rule.rule_type == "tripwire":
            return self._score_tripwire(person, pixel_pts)
        return 0

    def _score_rule_zone(self, person, rule, pixel_pts, cx, cy, eff_time) -> int:
        if not _point_in_polygon(cx, cy, pixel_pts):
            return 0

        score = config.RISK_IN_ZONE_POINTS

        if eff_time >= rule.conditions.min_dwell_seconds:
            score += config.RISK_DWELL_POINTS

        if person.zone_returns >= rule.conditions.loitering_zone_returns:
            score += config.RISK_ZONE_RETURNS_POINTS

        if person.area_spread_pixels < config.LOITERING_AREA_MAX:
            score += config.RISK_CONFINED_POINTS

        if person.avg_movement_pixels < config.LOITERING_VELOCITY_MAX:
            score += config.RISK_SLOW_POINTS

        return score

    @staticmethod
    def _score_tripwire(person, pixel_pts) -> int:
        if len(pixel_pts) < 2 or len(person.positions) < 2:
            return 0
        prev = person.positions[-2]
        curr = person.positions[-1]
        if _segments_cross(prev, curr, pixel_pts[0], pixel_pts[1]):
            return 100
        return 0

    # ------------------------------------------------------------------
    # Motion-pattern climbing detector
    # ------------------------------------------------------------------

    @staticmethod
    def _is_climbing(person) -> bool:
        """
        Three-gate climbing state machine.

        Real-time cadence context
        ─────────────────────────
        YOLO_SKIP_N=6 on a 12-fps stream → 2 YOLO inferences / second.
        Each entry in person.positions is one inference centroid.
        • positions[-20:] ≈ 10 s of real history  (net-rise window)
        • positions[-8:]  ≈  4 s of recent motion (streak + velocity window)

        Image-coordinate convention
        ───────────────────────────
        Y increases DOWNWARD.  Climbing UP means Y DECREASES over time.
            net_rise = oldest_y − newest_y  →  positive = subject rose in the frame

        Gate 1 — Net rise (10-second window)
            net_rise >= 20 px over the last 20 inference frames.
            Rejects subjects whose overall position is flat (pacing, walking
            horizontally, standing still with bbox jitter).

        Gate 2 — Sustained consecutive upward streak (4-second tail)
            The longest unbroken run of upward-moving consecutive centroid pairs
            within the last 8 inference frames must reach MIN_CONSECUTIVE (= 3).
            Eliminates isolated jitter bounces: pacing typically produces streaks
            of 0–2 before a reversal, while real climbing produces 4–8+.

        Gate 3 — Vertical velocity (4-second tail)
            Average Y-decrease per inference frame across the 8-frame tail must
            reach MIN_VELOCITY (= 3 px/frame = ~6 px/s at 2 inf/s).
            Pacing near the gate causes ≤ 1–2 px/frame drift; actual climbing
            produces 10–50 px/frame.  This gate self-resets when the subject
            descends or pauses: tail_net_rise goes to 0, disabling the alert.
        """
        positions = person.positions
        if len(positions) < 5:
            return False

        # ── Gate 1: overall net rise ───────────────────────────────────────
        window   = positions[-20:]
        net_rise = window[0][1] - window[-1][1]   # positive → rose

        # ── Gate 2: consecutive upward streak in recent tail ──────────────
        MIN_CONSECUTIVE = 3
        tail = positions[-8:]
        consecutive_ups = 0
        max_consecutive = 0
        for i in range(1, len(tail)):
            if tail[i][1] < tail[i - 1][1]:       # y decreased → moved up
                consecutive_ups += 1
                if consecutive_ups > max_consecutive:
                    max_consecutive = consecutive_ups
            else:
                consecutive_ups = 0                # streak broken

        # ── Gate 3: average vertical velocity in tail ──────────────────────
        MIN_VELOCITY = 3.0   # px per inference frame
        tail_net_rise = tail[0][1] - tail[-1][1]  # positive if net-upward in tail
        avg_velocity  = tail_net_rise / max(len(tail) - 1, 1)

        result = (
            net_rise        >= 20
            and max_consecutive >= MIN_CONSECUTIVE
            and avg_velocity    >= MIN_VELOCITY
        )

        # ── DEBUG console output ───────────────────────────────────────────
        print(
            f"[CLIMB-DEBUG] Subject {person.person_id} | "
            f"Rise: {net_rise:.1f} | "
            f"ConsecutiveUps: {max_consecutive} | "
            f"Velocity: {avg_velocity:.1f}px/f | "
            f"ClimbingStatus: {result}"
        )

        return result

    # ------------------------------------------------------------------
    # Hybrid zone scoring (drawn zones from zone_store)
    # ------------------------------------------------------------------

    def _score_zone_hybrid(
        self,
        person,
        pixel_pts: List[Tuple],
        cx: float,
        cy: float,
        eff_time: int,
        risk_level: str,
    ) -> int:
        """
        Score a zone_store zone using the hybrid risk-level logic.

          High   → instant 100 (no-go zone, regardless of dwell)
          Medium → base_score × 1.5  (capped at 100)
          Low    → base_score × 1.2  (capped at 100)
        """
        if not _point_in_polygon(cx, cy, pixel_pts):
            return 0

        if risk_level == "High":
            return 100   # Strict no-go — immediate alert

        # Base score uses default dwell/velocity/spread thresholds
        # (drawn zones don't carry rule.conditions; use config defaults).
        base = config.RISK_IN_ZONE_POINTS

        if eff_time >= 30:   # default min_dwell_seconds
            base += config.RISK_DWELL_POINTS

        if person.area_spread_pixels < config.LOITERING_AREA_MAX:
            base += config.RISK_CONFINED_POINTS

        if person.avg_movement_pixels < config.LOITERING_VELOCITY_MAX:
            base += config.RISK_SLOW_POINTS

        multiplier = _RISK_MULTIPLIER.get(risk_level, 1.0)
        return min(int(base * multiplier), 100)
