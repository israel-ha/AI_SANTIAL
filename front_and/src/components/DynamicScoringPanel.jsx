import React from 'react';
import { Target, ShieldAlert, Clock, Zap, AlertTriangle, TrendingUp, Users } from 'lucide-react';

// ---------------------------------------------------------------------------
// Color helpers
// ---------------------------------------------------------------------------

const scoreColors = (v) => {
  if (v >= 80) return { bar: '#ef4444', text: '#f87171', glow: '0 0 10px rgba(239,68,68,0.5)',  ring: 'rgba(239,68,68,0.10)' };
  if (v >= 60) return { bar: '#f97316', text: '#fb923c', glow: '0 0 8px rgba(249,115,22,0.4)',  ring: 'rgba(249,115,22,0.08)' };
  if (v >= 40) return { bar: '#eab308', text: '#facc15', glow: 'none',                           ring: 'rgba(234,179,8,0.06)'  };
  return               { bar: '#334155', text: '#475569', glow: 'none',                           ring: 'transparent'           };
};

const ZONE_META = {
  Low:    { bg: 'rgba(234,179,8,0.07)',   border: '#78350f40', text: '#fbbf24', badge: '#854d0e', mult: '×1.2',          label: 'Low Risk Zone'    },
  Medium: { bg: 'rgba(249,115,22,0.09)',  border: '#9a341240', text: '#fb923c', badge: '#9a3412', mult: '×1.5',          label: 'Medium Risk Zone' },
  High:   { bg: 'rgba(239,68,68,0.11)',   border: '#7f1d1d60', text: '#f87171', badge: '#991b1b', mult: 'INSTANT ALERT', label: 'HIGH — No-Go Zone' },
};

// ---------------------------------------------------------------------------
// ScoreBar — heavily muted when idle (value = 0), animates to threat colors
// ---------------------------------------------------------------------------

const ScoreBar = ({ label, value, icon: Icon }) => {
  const idle = value === 0;
  const c    = scoreColors(value);

  return (
    <div
      className="rounded-lg px-3 py-2.5 transition-all duration-500"
      style={{
        background: idle ? 'transparent' : c.ring,
        border:     `1px solid ${idle ? 'rgba(30,41,59,0.6)' : 'rgba(100,116,139,0.25)'}`,
        opacity:    idle ? 0.4 : 1,
      }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <Icon size={11} style={{ color: idle ? '#334155' : c.text }} />
          <span className="text-[11px] text-slate-500">{label}</span>
        </div>
        <div className="flex items-center gap-1">
          {value >= 70 && !idle && (
            <AlertTriangle size={10} className="text-red-400 animate-pulse" />
          )}
          <span
            className="text-xs font-bold tabular-nums transition-all duration-500"
            style={{ color: idle ? '#334155' : c.text }}
          >
            {value}
          </span>
          <span className="text-[10px] text-slate-700">/100</span>
        </div>
      </div>

      <div className="h-1.5 bg-slate-900 rounded-full overflow-hidden">
        {!idle && (
          <div
            className="h-full rounded-full transition-all duration-700 ease-out"
            style={{ width: `${value}%`, background: c.bar, boxShadow: c.glow }}
          />
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// ZoneBadge
// ---------------------------------------------------------------------------

const ZoneBadge = ({ level }) => {
  const m = ZONE_META[level];
  if (!m) return null;

  return (
    <div
      className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs font-semibold ${level === 'High' ? 'animate-pulse' : ''}`}
      style={{ background: m.bg, border: `1px solid ${m.border}`, color: m.text }}
    >
      <div className="flex items-center gap-1.5">
        <ShieldAlert size={11} />
        <span>{m.label}</span>
      </div>
      <span
        className="px-1.5 py-0.5 rounded text-[9px] font-bold text-white"
        style={{ background: m.badge }}
      >
        {m.mult}
      </span>
    </div>
  );
};

// ---------------------------------------------------------------------------
// ClimbingFlash — appears only when kinematic trigger fires
// ---------------------------------------------------------------------------

const ClimbingFlash = () => (
  <div
    className="flex items-center justify-center gap-2 px-3 py-2 rounded-lg animate-pulse"
    style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.45)' }}
  >
    <TrendingUp size={13} className="text-red-400 shrink-0" />
    <span className="text-xs font-bold text-red-300 tracking-widest">CLIMBING DETECTED</span>
  </div>
);

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

const DynamicScoringPanel = ({ persons = [] }) => {
  // Focus on the highest-risk person
  const primary = persons.reduce((best, p) => {
    const s = p.scores?.total_person_score ?? 0;
    return s > (best?.scores?.total_person_score ?? 0) ? p : best;
  }, null);

  const scores = {
    loitering_score:    primary?.scores?.loitering_score    ?? 0,
    total_person_score: primary?.scores?.total_person_score ?? 0,
  };

  const alertTypes  = primary?.alert_types ?? [];
  const zoneLevel   = primary?.zone_risk_level ?? null;
  const isClimbing  = alertTypes.includes('climbing');
  const isLoitering = alertTypes.includes('loitering') || scores.loitering_score >= 70;
  const isAlert     = isClimbing || isLoitering || scores.total_person_score >= 70;

  const totalColor = scoreColors(scores.total_person_score);

  return (
    <div
      className="bg-slate-900 rounded-2xl border flex flex-col overflow-hidden transition-colors duration-500"
      style={{ borderColor: isAlert ? 'rgba(239,68,68,0.3)' : 'rgb(30,41,59)' }}
    >

      {/* ── Header ──────────────────────────────────────────── */}
      <div
        className="px-4 py-3 flex items-center justify-between shrink-0 transition-colors duration-500"
        style={{ borderBottom: '1px solid rgb(30,41,59)', background: isAlert ? 'rgba(239,68,68,0.03)' : 'transparent' }}
      >
        <div className="flex items-center gap-2">
          <Target size={14} style={{ color: isAlert ? '#f87171' : '#6366f1' }} className="transition-colors duration-500" />
          <span className="text-sm font-semibold text-slate-100">Threat Analysis</span>
        </div>
        <div className="flex items-center gap-2">
          {persons.length > 0 && (
            <div className="flex items-center gap-1 text-[10px] text-slate-600">
              <Users size={10} />
              {persons.length}
            </div>
          )}
          {isAlert && (
            <span className="text-[9px] text-red-400 font-bold animate-pulse bg-red-500/10 px-1.5 py-0.5 rounded tracking-wider">
              ALERT
            </span>
          )}
        </div>
      </div>

      {/* ── Subject header ──────────────────────────────────── */}
      <div className="px-4 pt-3 pb-2 shrink-0">
        {primary ? (
          <div className="flex items-center justify-between">
            <div>
              <span className="text-xs font-semibold text-slate-300">Subject {primary.global_id}</span>
              <span className="text-[10px] text-slate-600 ml-2 font-mono">{primary.time_in_frame_seconds}s</span>
            </div>
            {/* Badge uses total_person_score — single source of truth matching the score bars below */}
            <div
              className="text-xs font-bold px-2 py-0.5 rounded tabular-nums transition-all duration-500"
              style={{
                color:      totalColor.text,
                background: totalColor.ring,
                border:     `1px solid ${scores.total_person_score > 0 ? totalColor.bar + '40' : 'rgb(30,41,59)'}`,
              }}
            >
              {scores.total_person_score > 0 ? `Score ${scores.total_person_score}` : 'Clear'}
            </div>
          </div>
        ) : (
          <p className="text-[11px] text-slate-700 italic">No subjects detected — monitoring…</p>
        )}
      </div>

      {/* ── Dynamic indicators (only render when relevant) ──── */}
      {primary && (
        <div className="px-4 pb-3 space-y-2 shrink-0">
          {/* Zone badge */}
          {zoneLevel && <ZoneBadge level={zoneLevel} />}

          {/* Climbing kinematic trigger */}
          {isClimbing && <ClimbingFlash />}

          {/* Loitering score — visible always when subject present, muted when 0 */}
          <ScoreBar
            label="Loitering"
            value={scores.loitering_score}
            icon={Clock}
          />

          {/* Total threat score — single KPI source of truth */}
          <ScoreBar
            label="Threat Score"
            value={scores.total_person_score}
            icon={Zap}
          />
        </div>
      )}
    </div>
  );
};

export default DynamicScoringPanel;
