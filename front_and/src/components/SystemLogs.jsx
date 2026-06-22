import React, { useState } from 'react';
import {
  Bell, Activity, TrendingUp, Clock, ShieldAlert,
  Zap, AlertTriangle,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Alert-type metadata — drives icon + color for each rich card
// ---------------------------------------------------------------------------

const TYPE_META = {
  climbing:  { Icon: TrendingUp,   color: '#f87171', bg: 'rgba(239,68,68,0.10)',   border: 'rgba(239,68,68,0.28)'   },
  intrusion: { Icon: ShieldAlert,  color: '#fb923c', bg: 'rgba(249,115,22,0.10)',  border: 'rgba(249,115,22,0.28)'  },
  loitering: { Icon: Clock,        color: '#fbbf24', bg: 'rgba(234,179,8,0.10)',   border: 'rgba(234,179,8,0.28)'   },
  combined:  { Icon: Zap,          color: '#c084fc', bg: 'rgba(192,132,252,0.10)', border: 'rgba(192,132,252,0.28)' },
};
const TYPE_DEFAULT = { Icon: AlertTriangle, color: '#94a3b8', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.20)' };

const SEV_PILL = {
  critical: 'bg-red-900/60   text-red-300   border-red-800/50',
  high:     'bg-red-900/60   text-red-300   border-red-800/50',
  medium:   'bg-amber-900/60 text-amber-300 border-amber-800/50',
  low:      'bg-slate-800    text-slate-500 border-slate-700',
};

const fmtTime = (alert) => {
  const raw = alert.timestamp_iso ?? alert.timestamp;
  if (!raw) return '—';
  try {
    const d = new Date(raw);
    return d.toTimeString().slice(0, 8);
  } catch { return '—'; }
};

// ---------------------------------------------------------------------------
// Rich alert card — one per event, arranged in a CSS grid
// ---------------------------------------------------------------------------

const RichAlertCard = ({ alert }) => {
  const type  = (alert.alert_type ?? alert.eventType ?? 'alert').toLowerCase();
  const meta  = TYPE_META[type] || TYPE_DEFAULT;
  const { Icon } = meta;
  const sev   = alert.severity ?? 'medium';
  const gid   = alert.global_id ?? null;
  const score = alert.metrics?.risk_score ?? alert.score ?? null;

  return (
    <div
      className="rounded-xl p-3 flex flex-col gap-2 border"
      style={{ background: meta.bg, borderColor: meta.border }}
    >
      {/* ── Row 1: icon + type name + severity pill ──── */}
      <div className="flex items-start justify-between gap-1">
        <div className="flex items-center gap-1.5">
          <div
            className="w-6 h-6 rounded-md flex items-center justify-center shrink-0"
            style={{ background: `${meta.color}22` }}
          >
            <Icon size={12} style={{ color: meta.color }} />
          </div>
          <span className="text-xs font-bold capitalize leading-tight" style={{ color: meta.color }}>
            {type}
          </span>
        </div>
        <span className={`text-[8px] px-1.5 py-0.5 rounded border font-bold shrink-0 ${SEV_PILL[sev] || SEV_PILL.low}`}>
          {sev.toUpperCase()}
        </span>
      </div>

      {/* ── Row 2: subject ID + risk score ───────────── */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] text-slate-400 font-mono">
          {gid ? `Subject ${gid}` : '—'}
        </span>
        {score !== null && (
          <span className="text-[10px] font-bold tabular-nums" style={{ color: meta.color }}>
            R:{score}
          </span>
        )}
      </div>

      {/* ── Row 3: timestamp ─────────────────────────── */}
      <div
        className="text-[9px] text-slate-600 font-mono border-t pt-1.5"
        style={{ borderColor: 'rgba(30,41,59,0.8)' }}
      >
        {fmtTime(alert)}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Activity row — compact single-line entries for the Activity tab
// ---------------------------------------------------------------------------

const LOG_DOT = {
  critical: '#ef4444',
  warning:  '#fb923c',
  info:     '#334155',
};
const LOG_TEXT = {
  critical: '#fca5a5',
  warning:  '#fdba74',
  info:     '#475569',
};

const ActivityRow = ({ entry }) => {
  const dot  = LOG_DOT[entry.severity]  || LOG_DOT.info;
  const text = LOG_TEXT[entry.severity] || LOG_TEXT.info;
  return (
    <div className="flex items-start gap-2 px-1 py-1 text-[10px]">
      <span className="mt-1 w-1.5 h-1.5 rounded-full shrink-0" style={{ background: dot }} />
      <div className="min-w-0 leading-relaxed">
        <span className="text-slate-600 font-mono mr-1.5">{entry.timestamp}</span>
        {entry.subjectId && <span className="text-slate-600 mr-1">[{entry.subjectId}]</span>}
        <span style={{ color: text }}>{entry.message}</span>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// SystemLogs — tabbed: Alerts (rich grid) | Activity (compact list)
// No fixed height, no scrollbar — content naturally sized, capped at 4 items.
// ---------------------------------------------------------------------------

const MAX_VISIBLE = 4;

const SystemLogs = ({ alerts = [], activityLog = [] }) => {
  const [tab, setTab] = useState('alerts');

  const significantAlerts = alerts
    .filter(a => ['high', 'medium', 'critical'].includes(a.severity))
    .slice(0, MAX_VISIBLE);

  const recentActivity = activityLog.slice(0, MAX_VISIBLE);

  return (
    <div className="bg-slate-900 rounded-2xl border border-slate-800 flex flex-col">

      {/* ── Tab bar ───────────────────────────────────────────── */}
      <div className="flex border-b border-slate-800 shrink-0">
        <button
          onClick={() => setTab('alerts')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-semibold transition-colors border-b-2
            ${tab === 'alerts'
              ? 'text-slate-100 border-indigo-500'
              : 'text-slate-600 hover:text-slate-400 border-transparent'}`}
        >
          <Bell size={10} />
          Alerts
          {significantAlerts.length > 0 && (
            <span className="bg-red-500 text-white rounded-full px-1.5 py-px text-[9px] font-bold leading-none">
              {significantAlerts.length}
            </span>
          )}
        </button>

        <button
          onClick={() => setTab('activity')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2 text-[11px] font-semibold transition-colors border-b-2
            ${tab === 'activity'
              ? 'text-slate-100 border-indigo-500'
              : 'text-slate-600 hover:text-slate-400 border-transparent'}`}
        >
          <Activity size={10} />
          Activity
          {activityLog.length > 0 && (
            <span className="text-[9px] text-slate-700 font-mono">{activityLog.length}</span>
          )}
        </button>
      </div>

      {/* ── Content ───────────────────────────────────────────── */}
      <div className="p-3">
        {tab === 'alerts' ? (
          significantAlerts.length === 0 ? (
            <p className="text-[10px] text-slate-700 italic text-center py-2">
              No high or medium alerts yet — monitoring quietly
            </p>
          ) : (
            /* 4-column grid: each card ~¼ of the video width */
            <div className="grid grid-cols-4 gap-2">
              {significantAlerts.map((alert, idx) => (
                <RichAlertCard key={alert.alert_id ?? idx} alert={alert} />
              ))}
            </div>
          )
        ) : (
          recentActivity.length === 0 ? (
            <p className="text-[10px] text-slate-700 italic text-center py-2">
              Activity will appear as motion is detected
            </p>
          ) : (
            <div className="space-y-0.5">
              {recentActivity.map(entry => (
                <ActivityRow key={entry.id} entry={entry} />
              ))}
            </div>
          )
        )}
      </div>
    </div>
  );
};

export default SystemLogs;
