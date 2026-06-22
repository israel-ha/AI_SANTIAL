import React from 'react';
import { Target, ShieldAlert, Clock, User, Users } from 'lucide-react';

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const scoreColor = (v) => {
  if (v >= 80) return { bar: '#ef4444', text: '#f87171', ring: 'rgba(239,68,68,0.10)' };
  if (v >= 60) return { bar: '#f97316', text: '#fb923c', ring: 'rgba(249,115,22,0.08)' };
  if (v >= 40) return { bar: '#eab308', text: '#facc15', ring: 'rgba(234,179,8,0.06)'  };
  return               { bar: '#1e293b', text: '#334155', ring: 'transparent'           };
};

const ZONE_META = {
  Low:    { bg: 'rgba(234,179,8,0.07)',  border: '#78350f40', text: '#fbbf24', badge: '#854d0e', mult: '×1.2',  label: 'Low Risk Zone'  },
  Medium: { bg: 'rgba(249,115,22,0.09)', border: '#9a341240', text: '#fb923c', badge: '#9a3412', mult: '×1.5',  label: 'Med Risk Zone'  },
  High:   { bg: 'rgba(239,68,68,0.11)',  border: '#7f1d1d60', text: '#f87171', badge: '#991b1b', mult: 'ALERT', label: 'HIGH RISK ZONE' },
};

// ---------------------------------------------------------------------------
// LED-style indicator badge
// Default: muted/dark (off).  Active: coloured, glowing, pulsing (on).
// ---------------------------------------------------------------------------

const INDICATOR_ACTIVE = {
  red:    'bg-red-500/20 border-red-500/55 text-red-300 shadow-[0_0_10px_rgba(239,68,68,0.30)]',
  orange: 'bg-orange-500/20 border-orange-500/55 text-orange-300 shadow-[0_0_10px_rgba(249,115,22,0.30)]',
};
const INDICATOR_DOT = { red: 'bg-red-400', orange: 'bg-orange-400' };
const INDICATOR_OFF = 'bg-slate-950 border-slate-800/50 text-slate-700';

const Indicator = ({ label, active, variant = 'red' }) => (
  <div
    className={`flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 border
      text-[10px] font-bold tracking-widest transition-all duration-300 select-none
      ${active ? `${INDICATOR_ACTIVE[variant]} animate-pulse` : INDICATOR_OFF}`}
  >
    <span
      className={`w-1.5 h-1.5 rounded-full shrink-0 transition-colors duration-300
        ${active ? INDICATOR_DOT[variant] : 'bg-slate-800'}`}
    />
    {label}
  </div>
);

// ---------------------------------------------------------------------------
// Subject Card
// ---------------------------------------------------------------------------

const SubjectCard = ({ person }) => {
  const alertTypes  = person.alert_types ?? [];
  const scores      = person.scores ?? {};
  const loit        = scores.loitering_score    ?? 0;
  const total       = scores.total_person_score ?? 0;
  const zone        = person.zone_risk_level;

  const isClimbing  = alertTypes.includes('climbing');
  const isIntrusion = alertTypes.includes('intrusion') || zone === 'High';
  const isAnyAlert  = isClimbing || isIntrusion || alertTypes.includes('loitering') || total >= 70;

  const tc = scoreColor(total);
  const lc = scoreColor(loit);
  const zm = ZONE_META[zone];

  return (
    <div
      className="rounded-xl p-3 flex flex-col gap-2.5 transition-all duration-500 border shrink-0"
      style={{
        background:  isAnyAlert ? 'rgba(25,5,5,0.9)'        : 'rgb(15,23,42)',
        borderColor: isAnyAlert ? 'rgba(239,68,68,0.28)'    : 'rgb(30,41,59)',
        boxShadow:   isAnyAlert ? '0 0 18px rgba(239,68,68,0.07)' : 'none',
      }}
    >
      {/* ── Header row ─────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-lg bg-slate-800/80 border border-slate-700/50 flex items-center justify-center shrink-0">
            <User size={13} className="text-slate-500" />
          </div>
          <div>
            <div className="text-xs font-bold text-slate-200 leading-none">
              Subject {person.global_id}
            </div>
            <div className="text-[10px] text-slate-600 font-mono mt-0.5">
              {person.time_in_frame_seconds}s in frame
            </div>
          </div>
        </div>

        {/* Threat score pill — bound to total_person_score (KPI engine) */}
        <div
          className="text-xs font-bold px-2 py-0.5 rounded tabular-nums transition-all duration-500 shrink-0"
          style={{
            color:      tc.text,
            background: tc.ring,
            border:     `1px solid ${total > 0 ? tc.bar + '35' : 'rgb(30,41,59)'}`,
          }}
        >
          {total > 0 ? total : '—'}
        </div>
      </div>

      {/* ── Zone badge (conditional) ───────────────────────── */}
      {zm && (
        <div
          className={`flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[10px] font-semibold
            ${zone === 'High' ? 'animate-pulse' : ''}`}
          style={{ background: zm.bg, border: `1px solid ${zm.border}`, color: zm.text }}
        >
          <div className="flex items-center gap-1.5">
            <ShieldAlert size={10} />
            <span>{zm.label}</span>
          </div>
          <span
            className="px-1 py-0.5 rounded text-[9px] font-bold text-white"
            style={{ background: zm.badge }}
          >
            {zm.mult}
          </span>
        </div>
      )}

      {/* ── Loitering score bar (continuous, 0 → 100) ─────── */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-1">
            <Clock size={10} style={{ color: loit > 0 ? lc.text : '#334155' }} />
            <span className="text-[10px] text-slate-600">Loitering</span>
          </div>
          <span
            className="text-[10px] font-bold tabular-nums"
            style={{ color: loit > 0 ? lc.text : '#334155' }}
          >
            {loit}
            <span className="text-slate-800 font-normal">/100</span>
          </span>
        </div>
        <div className="h-1.5 rounded-full overflow-hidden bg-slate-950">
          <div
            className="h-full rounded-full transition-all duration-700 ease-out"
            style={{
              width:     `${loit}%`,
              background: lc.bar,
              boxShadow:  loit >= 60 ? `0 0 6px ${lc.bar}` : 'none',
            }}
          />
        </div>
      </div>

      {/* ── Discrete event indicators ──────────────────────── */}
      {/* Muted by default; light up instantly when backend fires the trigger */}
      <div className="grid grid-cols-2 gap-1.5">
        <Indicator label="CLIMBING"  active={isClimbing}  variant="red"    />
        <Indicator label="INTRUSION" active={isIntrusion} variant="orange"  />
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Panel wrapper
// ---------------------------------------------------------------------------

const DynamicScoringPanel = ({ persons = [] }) => {
  // Sort: alerted subjects first, then by descending total score
  const sorted = [...persons].sort((a, b) => {
    const aHot = (a.alert_types?.length ?? 0) > 0;
    const bHot = (b.alert_types?.length ?? 0) > 0;
    if (aHot !== bHot) return bHot - aHot;
    return (b.scores?.total_person_score ?? 0) - (a.scores?.total_person_score ?? 0);
  });

  return (
    <div className="flex flex-col h-full min-h-0">

      {/* Panel header */}
      <div className="flex items-center justify-between px-0.5 pb-2.5 shrink-0">
        <div className="flex items-center gap-2">
          <Target size={13} className="text-indigo-400" />
          <span className="text-xs font-semibold text-slate-300">Subject Tracking</span>
        </div>
        {persons.length > 0 && (
          <div className="flex items-center gap-1 text-[10px] text-slate-600">
            <Users size={10} />
            <span>{persons.length} active</span>
          </div>
        )}
      </div>

      {/* Card list — scrollable */}
      <div className="flex-1 min-h-0 overflow-y-auto space-y-2 custom-scrollbar">
        {sorted.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 py-10">
            <div className="w-10 h-10 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center">
              <User size={18} className="text-slate-700" />
            </div>
            <p className="text-[11px] text-slate-700 italic text-center leading-relaxed">
              No subjects detected<br />System is monitoring…
            </p>
          </div>
        ) : (
          sorted.map(p => <SubjectCard key={p.global_id} person={p} />)
        )}
      </div>
    </div>
  );
};

export default DynamicScoringPanel;
