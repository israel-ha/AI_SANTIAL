import React, { useState, useRef, useEffect } from 'react';
import { Bell, Activity } from 'lucide-react';

const LOG_STYLE = {
  critical: { dot: '#ef4444', text: '#fca5a5', bg: 'rgba(239,68,68,0.06)'  },
  warning:  { dot: '#fb923c', text: '#fdba74', bg: 'rgba(249,115,22,0.04)' },
  info:     { dot: '#334155', text: '#475569', bg: 'transparent'            },
};

const SEV_STYLE = {
  high:     'bg-red-900/60   text-red-300   border-red-800/40',
  medium:   'bg-amber-900/60 text-amber-300 border-amber-800/40',
  low:      'bg-slate-800/60 text-slate-400 border-slate-700/40',
  critical: 'bg-red-900/60   text-red-300   border-red-800/40',
};

const SystemLogs = ({ alerts = [], activityLog = [] }) => {
  const [tab, setTab] = useState('alerts');
  const logRef        = useRef(null);

  useEffect(() => {
    if (tab === 'activity' && logRef.current) logRef.current.scrollTop = 0;
  }, [activityLog.length, tab]);

  const significantAlerts = alerts.filter(
    a => a.severity === 'high' || a.severity === 'medium' || a.severity === 'critical'
  );

  return (
    <div className="flex-1 min-h-0 bg-slate-900 rounded-2xl border border-slate-800 flex flex-col overflow-hidden">

      {/* ── Tab bar ─────────────────────────────────────────── */}
      <div className="flex border-b border-slate-800 shrink-0">
        <button
          onClick={() => setTab('alerts')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 text-[11px] font-semibold transition-colors border-b-2
            ${tab === 'alerts'
              ? 'text-slate-100 border-indigo-500'
              : 'text-slate-600 hover:text-slate-400 border-transparent'}`}
        >
          <Bell size={10} />
          Alerts
          {significantAlerts.length > 0 && (
            <span className="bg-red-500 text-white rounded-full px-1.5 py-px text-[9px] font-bold leading-none">
              {significantAlerts.length > 99 ? '99+' : significantAlerts.length}
            </span>
          )}
        </button>

        <button
          onClick={() => setTab('activity')}
          className={`flex-1 flex items-center justify-center gap-1.5 py-2.5 text-[11px] font-semibold transition-colors border-b-2
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

      {/* ── Content ─────────────────────────────────────────── */}
      <div className="flex-1 min-h-0 overflow-y-auto custom-scrollbar">
        {tab === 'alerts' ? (
          <div className="p-2 space-y-1">
            {significantAlerts.length === 0 ? (
              <p className="text-[10px] text-slate-700 italic text-center py-8 px-3 leading-relaxed">
                No high or medium alerts yet.<br />The system is monitoring quietly.
              </p>
            ) : significantAlerts.map((alert, idx) => (
              <div
                key={alert.alert_id ?? idx}
                className={`flex items-center gap-2 px-2 py-1.5 rounded-lg bg-slate-800/40 border ${SEV_STYLE[alert.severity] || SEV_STYLE.low}`}
              >
                <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold shrink-0 border ${SEV_STYLE[alert.severity] || SEV_STYLE.low}`}>
                  {(alert.severity ?? 'low').toUpperCase()}
                </span>
                <span className="text-[10px] text-red-300 font-semibold capitalize truncate flex-1">
                  {alert.alert_type ?? alert.eventType ?? '—'}
                </span>
                <span className="text-[10px] text-slate-600 font-mono shrink-0">
                  {alert.timestamp_iso
                    ? alert.timestamp_iso.split('T')[1]?.slice(0, 8)
                    : alert.timestamp
                      ? new Date(alert.timestamp).toTimeString().slice(0, 8)
                      : ''}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div ref={logRef} className="p-2 space-y-px">
            {activityLog.length === 0 ? (
              <p className="text-[10px] text-slate-700 italic text-center py-8 px-3 leading-relaxed">
                Subject activity will appear here<br />as the system detects motion.
              </p>
            ) : activityLog.map(entry => {
              const s = LOG_STYLE[entry.severity] || LOG_STYLE.info;
              return (
                <div
                  key={entry.id}
                  className="flex items-start gap-2 px-2 py-1 rounded text-[10px]"
                  style={{ background: s.bg }}
                >
                  <span
                    className="mt-[4px] w-1.5 h-1.5 rounded-full shrink-0"
                    style={{ background: s.dot }}
                  />
                  <div className="min-w-0 leading-relaxed">
                    <span className="text-slate-600 font-mono mr-1.5">{entry.timestamp}</span>
                    {entry.subjectId && (
                      <span className="text-slate-600 mr-1">[{entry.subjectId}]</span>
                    )}
                    <span style={{ color: s.text }}>{entry.message}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

export default SystemLogs;
