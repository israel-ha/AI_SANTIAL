"""
Shared data schemas used by both the Edge Node and Central Server.
These dataclasses are the single source of truth and match the JSON
schemas defined in API_CONTRACT.md exactly.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

@dataclass
class ZoneDefinition:
    """An operator-drawn zone with an explicit risk level."""
    id:         str
    points:     List[dict]   # [{"x": float, "y": float}, ...]  normalised [0,1]
    risk_level: str          # "Low" | "Medium" | "High"

    def to_dict(self) -> dict:
        return {
            "id":        self.id,
            "points":    self.points,
            "riskLevel": self.risk_level,
        }


@dataclass
class Point:
    """Normalized coordinate — both x and y are in the range [0.0, 1.0]."""
    x: float
    y: float

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


@dataclass
class RuleGeometry:
    type: str           # "polygon" | "line"
    points: List[Point]

    def to_dict(self) -> dict:
        return {"type": self.type, "points": [p.to_dict() for p in self.points]}


@dataclass
class RuleConditions:
    min_dwell_seconds:       int = 30
    loitering_zone_returns:  int = 3
    crossing_direction:      str = "any"    # "any" | "left_to_right" | "right_to_left"

    def to_dict(self) -> dict:
        return {
            "min_dwell_seconds":      self.min_dwell_seconds,
            "loitering_zone_returns": self.loitering_zone_returns,
            "crossing_direction":     self.crossing_direction,
        }


# ---------------------------------------------------------------------------
# Rule
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    rule_id:           str
    camera_id:         str
    name:              str
    rule_type:         str           # "zone" | "tripwire"
    alert_type:        str           # "loitering" | "climbing" | "intrusion"
    geometry:          RuleGeometry
    conditions:        RuleConditions
    active:            bool = True
    created_at:        str  = ""
    sensitivity_level: int  = 3      # 1 (low) – 5 (critical); zone rules only

    def to_dict(self) -> dict:
        return {
            "rule_id":           self.rule_id,
            "camera_id":         self.camera_id,
            "name":              self.name,
            "rule_type":         self.rule_type,
            "alert_type":        self.alert_type,
            "geometry":          self.geometry.to_dict(),
            "conditions":        self.conditions.to_dict(),
            "active":            self.active,
            "created_at":        self.created_at,
            "sensitivity_level": self.sensitivity_level,
        }


# ---------------------------------------------------------------------------
# Detection / Tracking
# ---------------------------------------------------------------------------

@dataclass
class DetectedPerson:
    person_id:             int
    feature:               Optional[List[float]]
    box:                   List[int]              # [x1, y1, x2, y2] in pixels
    last_position:         Dict[str, int]         # {"x": cx, "y": cy} in pixels
    positions:             List[Tuple[int, int]]  # last N centroids
    zone_visits:           List[Tuple[int, int]]  # spatial cluster history
    time_in_frame_seconds:  float
    avg_movement_pixels:    float
    area_spread_pixels:     float
    zone_returns:           int
    first_seen:             float                  # Unix timestamp
    last_seen:              float                  # Unix timestamp
    inside_restricted_zone: bool = False           # True when centroid is inside the operator-drawn zone

    def to_dict(self) -> dict:
        return {
            "person_id":              self.person_id,
            "feature":                self.feature,
            "box":                    self.box,
            "last_position":          self.last_position,
            "positions":              [list(p) for p in self.positions],
            "zone_visits":            [list(z) for z in self.zone_visits],
            "time_in_frame_seconds":  self.time_in_frame_seconds,
            "avg_movement_pixels":    self.avg_movement_pixels,
            "area_spread_pixels":     self.area_spread_pixels,
            "zone_returns":           self.zone_returns,
            "first_seen":             self.first_seen,
            "last_seen":              self.last_seen,
            "inside_restricted_zone": self.inside_restricted_zone,
        }


@dataclass
class TrackingPayload:
    camera_id:      str
    timestamp:      float
    frame_width:    int
    frame_height:   int
    frame_jpeg_b64: Optional[str]   # base64-encoded JPEG — None in debug mode
    persons:        List[DetectedPerson]

    def to_dict(self) -> dict:
        return {
            "camera_id":      self.camera_id,
            "timestamp":      self.timestamp,
            "frame_width":    self.frame_width,
            "frame_height":   self.frame_height,
            "frame_jpeg_b64": self.frame_jpeg_b64,
            "persons":        [p.to_dict() for p in self.persons],
        }


# ---------------------------------------------------------------------------
# Risk / Alert
# ---------------------------------------------------------------------------

@dataclass
class RiskResult:
    global_id:           str
    local_id:            int
    risk_score:          int            # 0 – 100
    alert_types:         List[str]      # e.g. ["loitering"]
    triggered_rule_id:   Optional[str]
    triggered_rule_name: Optional[str]
    in_zone:             bool
    zone_sensitivity:    int = 0        # sensitivity_level of the triggered zone (0 = not in zone)
    min_dwell_seconds:   int = 30       # from triggered rule conditions (used by scoring engine)
    zone_risk_level:     Optional[str] = None   # "Low" | "Medium" | "High" — from operator-drawn zones


@dataclass
class AlertDocument:
    alert_id:        str
    camera_id:       str
    global_id:       str
    alert_type:      str
    severity:        str    # "low" | "medium" | "high"
    status:          str    # "open" | "acknowledged" | "resolved"
    timestamp_iso:   str
    timestamp_unix:  float
    location:        Dict[str, Any]
    metrics:         Dict[str, Any]
    snapshot_url:    Optional[str]
    acknowledged_by: Optional[str]
    acknowledged_at: Optional[str]
    resolved_at:     Optional[str]
    scores:          Optional[Dict[str, Any]] = None   # climbing/loitering/total KPI scores
    trigger_type:    Optional[str]           = None   # "CLIMBING" | "LOITERING" | "COMBINED" | "INTRUSION"

    def to_dict(self) -> dict:
        return {
            "alert_id":        self.alert_id,
            "camera_id":       self.camera_id,
            "global_id":       self.global_id,
            "alert_type":      self.alert_type,
            "trigger_type":    self.trigger_type,
            "severity":        self.severity,
            "status":          self.status,
            "timestamp_iso":   self.timestamp_iso,
            "timestamp_unix":  self.timestamp_unix,
            "location":        self.location,
            "scores":          self.scores,
            "metrics":         self.metrics,
            "snapshot_url":    self.snapshot_url,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at,
            "resolved_at":     self.resolved_at,
        }
