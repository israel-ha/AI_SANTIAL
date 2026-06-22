"""
Dynamic Threat Memory — stateful, per-track threat score manager.

Wraps the stateless SpatialEngine output with a persistent server-side score
that incorporates three reduction mechanisms to drastically cut false alarms:

  1. Time Decay
     Score erodes exponentially when no new suspicious evidence arrives.
     Formula: S_new = S · e^(−λ · Δt)   λ = ln(2) / THREAT_DECAY_HALF_LIFE
     → Score halves every THREAT_DECAY_HALF_LIFE seconds with no events.

  2. Zone-Exit Transition
     Score drops immediately (once) when a tracked ID leaves a high-risk zone.
     Formula: S_new = S · (1 − THREAT_ZONE_EXIT_PENALTY)
     → Prevents the score from remaining elevated after benign zone traversal.

  3. Benign Trajectory Recognition
     Score is rolled back each cycle while the person walks in a normal,
     consistent straight-line pattern at a natural speed.
     Formula: S_new = S · (1 − THREAT_BENIGN_REDUCTION_RATE · consistency)
     consistency = mean cosine-similarity of consecutive velocity vectors ∈ [0, 1]
     → A perfectly straight walk at normal speed applies the full reduction rate.

Architecture integration:
  ThreatMemory sits between SpatialEngine.evaluate() and AlertManager.process().
  It receives the raw evidence score and returns an adjusted persistent score.
  The adjusted score is written back into the RiskResult objects before any
  alert logic, zone-entry logging, or Socket.IO emission occurs.

  app.py call sequence:
    raw_results  = _spatial.evaluate(...)
    adjusted     = _threat_memory.adjust(raw_results, persons, global_ids, now)
    _alerts.process(adjusted, ...)
"""
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from shared import config
from shared.schemas import DetectedPerson, RiskResult


# ---------------------------------------------------------------------------
# Per-track state
# ---------------------------------------------------------------------------

@dataclass
class TrackState:
    """
    All mutable state maintained for a single Re-ID global track.

    Fields
    ------
    global_id       : "G-1", "G-2", … — key in ThreatMemory._tracks
    score           : current dynamic threat score  [0.0, 100.0]
    last_event_ts   : unix timestamp of the most recent suspicious-evidence event;
                      used only for diagnostics / future logging
    last_update_ts  : unix timestamp of the last call to ThreatMemory.adjust();
                      dt = now − last_update_ts drives incremental decay
    last_zone_name  : name of the zone the person was inside on the previous cycle,
                      or None if outside all zones; used to detect exit transitions
    vel_history     : rolling window of (dx, dy) displacement vectors computed at
                      the ingest rate (≈ 1 Hz); used for benign trajectory analysis
    last_pos        : last centroid (px, py) used to derive the next velocity vector
    """
    global_id:      str
    score:          float
    last_event_ts:  float
    last_update_ts: float
    last_zone_name: Optional[str]
    vel_history:    deque = field(default_factory=lambda: deque(maxlen=20))
    last_pos:       Optional[Tuple[float, float]] = None


# ---------------------------------------------------------------------------
# ThreatMemory
# ---------------------------------------------------------------------------

class ThreatMemory:
    """
    Stateful threat score manager for all concurrently tracked Re-ID identities.

    Usage
    -----
    Create one instance at server startup and hold it as a module-level
    singleton (same lifetime as ReIDEngine / SpatialEngine).

        _threat_memory = ThreatMemory()

    Each ingest cycle call:

        adjusted_results = _threat_memory.adjust(
            raw_results, persons, global_ids, time.time()
        )

    `adjust` mutates the RiskResult objects in-place *and* returns the same list
    for convenience.  Purge stale tracks afterwards:

        _threat_memory.purge_stale(now)
    """

    def __init__(self) -> None:
        self._tracks: Dict[str, TrackState] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def adjust(
        self,
        risk_results: List[RiskResult],
        persons:      List[DetectedPerson],
        global_ids:   Dict[str, str],   # {str(local_id): "G-N"}
        now:          float,
    ) -> List[RiskResult]:
        """
        Apply all three reduction mechanisms to every RiskResult in-place.

        Parameters
        ----------
        risk_results : output of SpatialEngine.evaluate() — mutated in-place
        persons      : same DetectedPerson list passed to SpatialEngine
        global_ids   : local-id → global-id mapping from ReIDEngine
        now          : current unix timestamp (pass time.time() from the caller
                       to keep all math consistent within a single ingest cycle)

        Returns
        -------
        The same risk_results list (mutated) for chaining convenience.
        """
        person_by_lid = {p.person_id: p for p in persons}

        for result in risk_results:
            person = person_by_lid.get(result.local_id)
            if person is None:
                continue

            pos       = (float(person.last_position["x"]),
                         float(person.last_position["y"]))
            zone_name = result.triggered_rule_name if result.in_zone else None

            adjusted = self._update_track(
                global_id = result.global_id,
                raw_score  = float(result.risk_score),
                zone_name  = zone_name,
                position   = pos,
                now        = now,
            )

            # Kinematic alerts (climbing) are independent of zone score — preserve them
            # even when the spatial/zone score falls below the alert threshold.
            _KINEMATIC = {"climbing"}
            result.risk_score = int(round(adjusted))
            result.in_zone    = adjusted >= config.RISK_ALERT_THRESHOLD
            if not result.in_zone:
                result.alert_types = [a for a in result.alert_types if a in _KINEMATIC]

        return risk_results

    def get_score(self, global_id: str) -> float:
        """Return the current persistent score without triggering any update."""
        state = self._tracks.get(global_id)
        return state.score if state else 0.0

    def purge_stale(self, now: float) -> None:
        """
        Evict tracks whose last_update_ts is older than THREAT_MEMORY_TTL seconds.
        Call once per ingest cycle after adjust() to bound memory usage.
        """
        ttl   = config.THREAT_MEMORY_TTL
        stale = [gid for gid, s in self._tracks.items()
                 if now - s.last_update_ts > ttl]
        for gid in stale:
            del self._tracks[gid]

    # ------------------------------------------------------------------
    # Core update — called once per track per ingest cycle
    # ------------------------------------------------------------------

    def _update_track(
        self,
        global_id: str,
        raw_score:  float,
        zone_name:  Optional[str],
        position:   Tuple[float, float],
        now:        float,
    ) -> float:
        """
        Full per-track update pipeline.  Returns the clamped adjusted score.

        Update order
        ------------
        1. Velocity history  — must happen before benign analysis
        2. Evidence boost OR time decay  (mutually exclusive per cycle)
        3. Zone-exit transition  (one-shot, only on the crossing frame)
        4. Benign trajectory reduction  (rolling, applied while pattern holds)
        5. Clamp to [0, 100]
        """
        state = self._get_or_create(global_id, now)
        dt    = now - state.last_update_ts   # seconds since last update

        # ── 1. Velocity history update ──────────────────────────────────
        if state.last_pos is not None:
            dx = position[0] - state.last_pos[0]
            dy = position[1] - state.last_pos[1]
            state.vel_history.append((dx, dy))
        state.last_pos = position

        # ── 2. Evidence boost / time decay ─────────────────────────────
        if raw_score > 0:
            state.score       = _boost(state.score, raw_score)
            state.last_event_ts = now
        else:
            state.score = _decay(state.score, dt)

        # ── 3. Zone-exit transition (one-shot on the exiting frame) ────
        if state.last_zone_name is not None and zone_name is None:
            state.score = _zone_exit_reduction(state.score)

        state.last_zone_name = zone_name   # persist for next cycle's comparison

        # ── 4. Benign trajectory reduction ─────────────────────────────
        consistency = _heading_consistency(state.vel_history)
        if consistency > 0.0:
            state.score = _benign_reduction(state.score, consistency)

        # ── 5. Clamp & persist ─────────────────────────────────────────
        state.score        = max(0.0, min(100.0, state.score))
        state.last_update_ts = now
        return state.score

    # ------------------------------------------------------------------
    # State initialisation
    # ------------------------------------------------------------------

    def _get_or_create(self, global_id: str, now: float) -> TrackState:
        if global_id not in self._tracks:
            self._tracks[global_id] = TrackState(
                global_id      = global_id,
                score          = 0.0,
                last_event_ts  = now,
                last_update_ts = now,
                last_zone_name = None,
            )
        return self._tracks[global_id]


# ---------------------------------------------------------------------------
# Pure mathematical helpers (module-level, no instance state)
# ---------------------------------------------------------------------------

def _decay(score: float, dt: float) -> float:
    """
    Incremental exponential time decay.

    Applied each ingest cycle (dt ≈ 1 s) when no suspicious evidence is present.

    Formula
    -------
        S_new = S · e^(−λ · dt)
        λ = ln(2) / THREAT_DECAY_HALF_LIFE

    With THREAT_DECAY_HALF_LIFE = 30 s:
        dt =  1 s → S × 0.977   (−2.3 % per cycle)
        dt = 30 s → S × 0.500   (halved)
        dt = 60 s → S × 0.250   (quarter)
        dt = 90 s → S × 0.125   (eighth — approaches zero asymptotically)
    """
    if score <= 0.0 or dt <= 0.0:
        return score
    lam = math.log(2.0) / config.THREAT_DECAY_HALF_LIFE
    return score * math.exp(-lam * dt)


def _boost(current: float, raw: float) -> float:
    """
    Smooth evidence boost toward the SpatialEngine raw score ceiling.

    Formula
    -------
        gap     = max(0, raw − current)
        S_new   = current + THREAT_BOOST_RATE · gap

    Properties
    ----------
    • Only ever increases the score (gap is non-negative).
    • Closes half the remaining gap per cycle at the default rate of 0.50,
      so the score ramps up smoothly: 0 → 30 → 45 → 52.5 → 56.25 → …
      rather than jumping directly to `raw` on the first suspicious frame.
    • Multiple consecutive cycles of evidence accumulate naturally.

    Example (raw = 60, BOOST_RATE = 0.50):
        cycle 1: 0  + 0.50·60  = 30.0
        cycle 2: 30 + 0.50·30  = 45.0
        cycle 3: 45 + 0.50·15  = 52.5
        cycle 4: 52.5 + ...    = 56.25  (asymptotically approaches 60)
    """
    gap = max(0.0, raw - current)
    return current + config.THREAT_BOOST_RATE * gap


def _zone_exit_reduction(score: float) -> float:
    """
    Immediate proportional penalty applied exactly once when a tracked ID
    transitions from inside a high-risk zone polygon to outside it.

    Formula
    -------
        S_new = S · (1 − THREAT_ZONE_EXIT_PENALTY)

    With THREAT_ZONE_EXIT_PENALTY = 0.40:
        score = 80  →  80 · 0.60 = 48  (drops below RISK_ALERT_THRESHOLD = 70)
        score = 60  →  60 · 0.60 = 36
        score = 40  →  40 · 0.60 = 24

    Rationale: a person who entered a restricted zone but then voluntarily
    returned to a safe area has demonstrated non-threatening intent.  Cutting
    the score below the alert threshold on exit prevents the system from
    generating repeated alerts for the same normal traversal.
    """
    return score * (1.0 - config.THREAT_ZONE_EXIT_PENALTY)


def _benign_reduction(score: float, consistency: float) -> float:
    """
    Rolling score reduction applied each cycle while a benign trajectory holds.

    Formula
    -------
        S_new = S · (1 − THREAT_BENIGN_REDUCTION_RATE · consistency)

    `consistency` is the normalised heading-consistency value in [0, 1]
    returned by _heading_consistency().

    With THREAT_BENIGN_REDUCTION_RATE = 0.05 and consistency = 1.0:
        Reduction per cycle = 5 %.
        After 10 sustained cycles: score × 0.95^10 ≈ 0.60  (−40 %)
        After 20 sustained cycles: score × 0.95^20 ≈ 0.36  (−64 %)
    """
    return score * (1.0 - config.THREAT_BENIGN_REDUCTION_RATE * consistency)


def _heading_consistency(vel_history: deque) -> float:
    """
    Measure how "straight and steady" the recent trajectory is.

    Returns a normalised consistency value in [0.0, 1.0]:
        0.0 — not enough data, or erratic / stationary / too-fast motion
        1.0 — perfectly straight line at uniform speed (maximum benign signal)

    Algorithm
    ---------
    1. Guard checks:
       • Require at least BENIGN_MIN_SAMPLES velocity vectors.
       • Average speed must be in [BENIGN_SPEED_MIN, BENIGN_SPEED_MAX].
         Filters out near-stationary persons (who may be loitering) and
         sprinting persons (panic / chase — not normal behaviour).

    2. Heading consistency:
       For each consecutive pair of velocity vectors (v_i, v_{i+1}):

           cos θ_i = (v_i · v_{i+1}) / (|v_i| · |v_{i+1}|)

       Pairs where either vector is near-zero (< 0.5 px) are skipped to
       avoid division noise.

       heading_consistency = mean(cos θ_i)   ∈ [−1, 1]

    3. Threshold gate:
       If heading_consistency < BENIGN_HEADING_THRESHOLD → return 0.0.
       This prevents even mild directional jitter from registering as benign.

    4. Normalisation to [0, 1]:
       Maps [BENIGN_HEADING_THRESHOLD, 1.0] → [0.0, 1.0] linearly so that
       the reduction formula scales proportionally with confidence.

           normalised = (consistency − threshold) / (1.0 − threshold)
    """
    vels = list(vel_history)
    if len(vels) < config.BENIGN_MIN_SAMPLES:
        return 0.0

    # ── Speed guard ──────────────────────────────────────────────────────
    speeds    = [math.hypot(dx, dy) for dx, dy in vels]
    avg_speed = sum(speeds) / len(speeds)
    if avg_speed < config.BENIGN_SPEED_MIN or avg_speed > config.BENIGN_SPEED_MAX:
        return 0.0

    # ── Heading consistency ───────────────────────────────────────────────
    cos_sims: List[float] = []
    for i in range(1, len(vels)):
        v1, v2 = vels[i - 1], vels[i]
        mag1 = math.hypot(*v1)
        mag2 = math.hypot(*v2)
        if mag1 < 0.5 or mag2 < 0.5:   # skip near-zero frames
            continue
        dot = v1[0] * v2[0] + v1[1] * v2[1]
        cos_sims.append(dot / (mag1 * mag2))

    if not cos_sims:
        return 0.0

    consistency = sum(cos_sims) / len(cos_sims)
    threshold   = config.BENIGN_HEADING_THRESHOLD

    if consistency < threshold:
        return 0.0

    # Normalise [threshold, 1.0] → [0.0, 1.0]
    return (consistency - threshold) / (1.0 - threshold)
