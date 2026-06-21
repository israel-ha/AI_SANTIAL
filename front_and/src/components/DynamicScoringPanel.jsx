import React, { useRef, useEffect } from 'react';
import { Target, ShieldAlert, Activity, TrendingUp, Clock, Zap, AlertTriangle, Users } from 'lucide-react';

// ---------------------------------------------------------------------------
// Color helpers  (avoid Tailwind JIT purge by using inline styles for dynamic values)
// ---------------------------------------------------------------------------

const scoreColors = (v) => {
  if (v >= 80) return { bar: '#ef4444', text: '#f87171', glow: '0 0 12px rgba(239,68,68,0.55)', ring: 'rgba(239,68,68,0.15)' };
  if (v >= 60) return { bar: '#f97316', text: '#fb923c', glow: '0 0 8px rgba(249,115,22,0.45)', ring: 'rgba(249,115,22,0.10)' };
  if (v >= 40) return { bar: '#eab308', text: '#facc15', glow: 'none', ring: 'rgba(234,179,8,0.08)' };
  return         { bar: '#10b981', text: '#34d399', glow: 'none', ring: 'transparent' };
};

const ZONE_META = {
  Low:    { bg: 'rgba(234,179,8,0.08)',   border: '#78350f', text: '#fbbf24', badge: '#854d0e', mult: '×1.2',          label: 'Low Risk Zone'    },
  Medium: { bg: 'rgba(249,115,22,0.10)',  border: '#9a3412', text: '#fb923c', badge: '#9a3412', mult: '×1.5',          label: 'Medium Risk Zone' },
  High:   { bg: 'rgba(239,68,68,0.12)',   border: '#7f1d1d', text: '#f87171', badge: '#991b1b', mult: 'INSTANT ALERT', label: 'HIGH — No-Go Zone' },
};

const LOG_STYLE = {
  critical: { dot: '#ef4444', text: '#fca5a5', dim: 'rgba(239,68,68,0.08)' },
  warning:  { dot: '#fb923c', text: '#fdba74', dim: 'rgba(249,115,22,0.06)' },
  info:     { dot: '#64748b', text: '#94a3b8', dim: 'transparent'           },
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const ScoreBar = ({ label, value, icon: Icon, isBold }) => {
  const c     = scoreColors(value);
  const fired = value >= 70;

  return (
    <div
      className="rounded-xl p-3 transition-all duration-500"
      style={{ background: c.ring, border: `1px solid ${fired ? 'rgba(100,116,139,0.3)' : 'rgba(30,41,59,0.8)'}` }}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Icon size={12} style={{ color: c.text }} />
          <span className={`text-xs ${isBold ? 'font-semibold text-slate-200' : 'text-slate-400'}`}>
            {label}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {fired && <AlertTriangle size={10} className="text-red-400 animate-pulse" />}
          <span
            className="text-sm font-bold tabular-nums transition-all duration-500"
            style={{ color: c.text }}
          >
            {value}
          </span>
          <span className="text-[10px] text-slate-600">/100</span>
        </div>
      </div>

      <div className="h-2 bg-slate-900 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700 ease-out"
          style={{ width: `${value}%`, background: c.bar, boxShadow: c.glow }}
        />
      </div>
    </div>
  );
};

const ZoneBadge = ({ level }) => {
  const m = ZONE_META[level];
  if (!m) return null;
  const isHigh = level === 'High';

  return (
    <div
      className={`flex items-center justify-between px-3 py-2 rounded-lg text-xs font-semibold ${isHigh ? 'animate-pulse' : ''}`}
      style={{ background: m.bg, border: `1px solid ${m.border}`, color: m.text }}
    >
      <div className="flex items-center gap-1.5">
        <ShieldAlert size={12} />
        <span>{m.label}</span>
      </div>
      <span
        className="px-1.5 py-0.5 rounded text-[10px] font-bold"
        style={{ background: m.badge, color: '#fff' }}
      >
        {m.mult}
      </span>
    </div>
  );
};

const LogEntry = ({ entry }) => {
  const s = LOG_STYLE[entry.severity] || LOG_STYLE.info;
  return (
    <div
      className="log-entry flex items-start gap-2 px-2 py-1.5 rounded-lg text-[11px]"
      style={{ background: s.dim }}
    >
      <span
        className="mt-[3px] shrink-0 w-1.5 h-1.5 rounded-full"
        style={{ background: s.dot }}
      />
      <div className="min-w-0 flex-1">
        <span className="text-slate-500 mr-1.5 font-mono">{entry.timestamp}</span>
        {entry.subjectId && (
          <span className="text-slate-500 mr-1">[{entry.subjectId}]</span>
        )}
        <span style={{ color: s.text }}>{entry.message}</span>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

const DynamicScoringPanel = ({ persons = [], activityLog = [] }) => {
  const logRef = useRef(null);

  // Scroll log to top whenever new entries arrive (newest-first list)
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = 0;
  }, [activityLog.length]);

  // Focus on the highest-risk person
  const primary = persons.reduce((best, p) => {
    const s = p.scores?.total_person_score ?? 0;
    return s > (best?.scores?.total_person_score ?? 0) ? p : best;
  }, null);

  const scores = {
    climbing_score:     primary?.scores?.climbing_score     ?? 0,
    loitering_score:    primary?.scores?.loitering_score    ?? 0,
    total_person_score: primary?.scores?.total_person_score ?? 0,
  };
  const zoneLevel  = primary?.zone_risk_level ?? null;
  const totalColor = scoreColors(scores.total_person_score);
  const isAlert    = scores.total_person_score >= 70 || scores.loitering_score >= 70 || scores.climbing_score >= 50;

  return (
    <>
      {/* Scoped keyframe for log-entry slide-in */}
      <style>{`
        @keyframes logSlideIn {
          from { opacity: 0; transform: translateY(-6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .log-entry { animation: logSlideIn 0.3s ease; }
      `}</style>

      <div className="bg-slate-900 rounded-2xl border border-slate-800 flex flex-col h-full overflow-hidden">

        {/* ── Header ───────────────────────────────────────────────── */}
        <div
          className="px-4 py-3 border-b border-slate-800 flex items-center justify-between transition-all duration-500"
          style={{ background: isAlert ? 'rgba(239,68,68,0.04)' : 'transparent' }}
        >
          <div className="flex items-center gap-2">
            <Target
              size={16}
              className="transition-colors duration-500"
              style={{ color: isAlert ? '#f87171' : '#818cf8' }}
            />
            <span className="font-semibold text-slate-100 text-sm">Live Threat Analysis</span>
          </div>

          <div className="flex items-center gap-2">
            {persons.length > 0 && (
              <div className="flex items-center gap-1 text-[10px] text-slate-500">
                <Users size={10} />
                {persons.length} subject{persons.length !== 1 ? 's' : ''}
              </div>
            )}
            {isAlert && (
              <span className="text-[10px] text-red-400 font-semibold animate-pulse bg-red-500/10 px-1.5 py-0.5 rounded">
                ALERT
              </span>
            )}
          </div>
        </div>

        {/* ── Subject info ──────────────────────────────────────────── */}
        <div className="px-4 pt-3 pb-0">
          {primary ? (
            <div className="flex items-center justify-between mb-3">
              <div>
                <span className="text-xs font-semibold text-slate-300">
                  Subject {primary.global_id}
                </span>
                <span className="text-[10px] text-slate-500 ml-2">
                  {primary.time_in_frame_seconds}s in frame
                </span>
              </div>
              <div
                className="text-xs font-bold px-2 py-0.5 rounded-md tabular-nums"
                style={{
                  color:      totalColor.text,
                  background: totalColor.ring,
                  border:     `1px solid ${totalColor.bar}30`,
                }}
              >
                Risk {primary.risk_score}
              </div>
            </div>
          ) : (
            <p className="text-xs text-slate-600 italic mb-3">No subjects detected — monitoring…</p>
          )}
        </div>

        {/* ── Zone badge ────────────────────────────────────────────── */}
        {zoneLevel && (
          <div className="px-4 pb-3">
            <ZoneBadge level={zoneLevel} />
          </div>
        )}

        {/* ── Score bars ────────────────────────────────────────────── */}
        <div className="px-4 space-y-2 pb-3">
          <ScoreBar
            label="Climbing Score"
            value={scores.climbing_score}
            icon={TrendingUp}
          />
          <ScoreBar
            label="Loitering Score"
            value={scores.loitering_score}
            icon={Clock}
          />
          <ScoreBar
            label="Total Score"
            value={scores.total_person_score}
            icon={Zap}
            isBold
          />
        </div>

        {/* ── Activity log ──────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col min-h-0 border-t border-slate-800/60">
          <div className="px-4 py-2 flex items-center justify-between shrink-0">
            <div className="flex items-center gap-1.5 text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
              <Activity size={10} />
              Activity Log
            </div>
            {activityLog.length > 0 && (
              <span className="text-[10px] text-slate-600">{activityLog.length} events</span>
            )}
          </div>

          <div
            ref={logRef}
            className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5 custom-scrollbar"
          >
            {activityLog.length === 0 ? (
              <p className="text-[10px] text-slate-700 italic px-2 pt-1">
                Events will appear here as scoring activity is detected…
              </p>
            ) : (
              activityLog.map(entry => (
                <LogEntry key={entry.id} entry={entry} />
              ))
            )}
          </div>
        </div>
      </div>
    </>
  );
};

export default DynamicScoringPanel;
