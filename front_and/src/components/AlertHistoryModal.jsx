import React, { useState, useEffect, useRef } from 'react';
import {
  X, Video, Clock, TrendingUp, Shield, AlertTriangle,
  ChevronRight, RefreshCw, Film,
} from 'lucide-react';

const SERVER_URL = import.meta.env.VITE_SERVER_URL || 'http://localhost:5000';

// ---------------------------------------------------------------------------
// Event type styling
// ---------------------------------------------------------------------------
const EVENT_STYLES = {
  Climbing: {
    bg: 'rgba(59,130,246,0.12)', border: '#1d4ed8',
    text: '#60a5fa', dot: '#3b82f6', icon: TrendingUp,
  },
  Loitering: {
    bg: 'rgba(234,179,8,0.10)', border: '#854d0e',
    text: '#fbbf24', dot: '#eab308', icon: Clock,
  },
  'Zone Intrusion': {
    bg: 'rgba(239,68,68,0.12)', border: '#7f1d1d',
    text: '#f87171', dot: '#ef4444', icon: Shield,
  },
};
const DEFAULT_STYLE = {
  bg: 'rgba(100,116,139,0.10)', border: '#334155',
  text: '#94a3b8', dot: '#64748b', icon: AlertTriangle,
};

const fmtTimestamp = (iso) => {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString() + '  ' + d.toTimeString().slice(0, 8);
  } catch {
    return iso;
  }
};

// ---------------------------------------------------------------------------
// Alert card (left list)
// ---------------------------------------------------------------------------
const AlertCard = ({ alert, isSelected, onClick }) => {
  const s    = EVENT_STYLES[alert.eventType] || DEFAULT_STYLE;
  const Icon = s.icon;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-xl p-3 border transition-all duration-200
        ${isSelected
          ? 'border-indigo-600 bg-indigo-900/20'
          : 'border-slate-800 hover:border-slate-700 bg-slate-900/60 hover:bg-slate-900'}`}
    >
      <div className="flex items-start gap-2.5">
        <div
          className="mt-0.5 w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
          style={{ background: s.bg, border: `1px solid ${s.border}` }}
        >
          <Icon size={13} style={{ color: s.dot }} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1">
            <span className="text-xs font-semibold" style={{ color: s.text }}>
              {alert.eventType}
            </span>
            <span className="text-[10px] font-bold text-slate-500 tabular-nums">
              Score {alert.score}
            </span>
          </div>
          <p className="text-[10px] text-slate-500 mt-0.5 truncate font-mono">
            {fmtTimestamp(alert.timestamp)}
          </p>
          {alert.camera_id && (
            <p className="text-[10px] text-slate-600">{alert.camera_id}</p>
          )}
        </div>
        {isSelected && (
          <ChevronRight size={12} className="text-indigo-400 shrink-0 mt-1" />
        )}
      </div>
    </button>
  );
};

// ---------------------------------------------------------------------------
// Main modal
// ---------------------------------------------------------------------------
export default function AlertHistoryModal({ isOpen, onClose }) {
  const [alerts, setAlerts]     = useState([]);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(null);
  const [selected, setSelected] = useState(null);
  const videoRef                = useRef(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`${SERVER_URL}/api/alerts`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setAlerts(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      load();
      setSelected(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // Reload video src whenever selection changes
  useEffect(() => {
    if (selected && videoRef.current) {
      videoRef.current.load();
      videoRef.current.play().catch(() => {});
    }
  }, [selected]);

  if (!isOpen) return null;

  const selStyle = selected ? (EVENT_STYLES[selected.eventType] || DEFAULT_STYLE) : null;
  const SelIcon  = selStyle?.icon;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/75 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="relative z-10 bg-slate-950 border border-slate-800 rounded-2xl shadow-2xl w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden">

        {/* ── Header ───────────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 shrink-0">
          <div className="flex items-center gap-3">
            <Film size={18} className="text-indigo-400" />
            <h2 className="text-base font-bold text-slate-100">Alert History</h2>
            <span className="text-[10px] text-slate-500 bg-slate-800/80 px-2 py-0.5 rounded-full">
              {alerts.length} recording{alerts.length !== 1 ? 's' : ''}
            </span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={load}
              disabled={loading}
              title="Refresh"
              className="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800 transition-colors"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-200 p-1.5 rounded-lg hover:bg-slate-800 transition-colors"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* ── Body ─────────────────────────────────────────────────── */}
        <div className="flex flex-1 min-h-0">

          {/* Left — scrollable alert list */}
          <div className="w-72 shrink-0 border-r border-slate-800 flex flex-col">
            <div className="flex-1 overflow-y-auto p-2 space-y-1.5 custom-scrollbar">
              {loading && (
                <div className="flex items-center justify-center py-16 text-slate-600 text-xs">
                  Loading clips…
                </div>
              )}
              {error && (
                <div className="text-red-400 text-xs text-center px-3 py-8">
                  Failed to load: {error}
                </div>
              )}
              {!loading && !error && alerts.length === 0 && (
                <div className="text-slate-700 text-xs text-center py-16 italic px-4 leading-relaxed">
                  No alert clips yet.
                  <br />Clips are saved automatically when an alert fires.
                </div>
              )}
              {alerts.map(alert => (
                <AlertCard
                  key={alert.id}
                  alert={alert}
                  isSelected={selected?.id === alert.id}
                  onClick={() => setSelected(alert)}
                />
              ))}
            </div>
          </div>

          {/* Right — video player */}
          <div className="flex-1 flex flex-col items-center justify-center bg-black/30 p-6 min-w-0">
            {!selected ? (
              <div className="text-center text-slate-700">
                <Video size={52} className="mx-auto mb-4 opacity-20" />
                <p className="text-sm font-medium">Select a clip to play</p>
                <p className="text-xs mt-1 text-slate-800">
                  Recordings include 5 s before and 5 s after the alert
                </p>
              </div>
            ) : (
              <div className="w-full max-w-2xl flex flex-col gap-3">

                {/* Metadata banner */}
                <div
                  className="flex items-center justify-between px-4 py-2.5 rounded-xl text-xs"
                  style={{
                    background: selStyle.bg,
                    border:     `1px solid ${selStyle.border}`,
                  }}
                >
                  <div className="flex items-center gap-2">
                    <SelIcon size={13} style={{ color: selStyle.dot }} />
                    <span className="font-semibold" style={{ color: selStyle.text }}>
                      {selected.eventType}
                    </span>
                    <span className="text-slate-500">·</span>
                    <span className="text-slate-400">Score {selected.score}</span>
                    {selected.zone_risk_level && (
                      <>
                        <span className="text-slate-500">·</span>
                        <span style={{ color: selStyle.text }}>
                          {selected.zone_risk_level} Zone
                        </span>
                      </>
                    )}
                  </div>
                  <span className="text-slate-500 font-mono">
                    {fmtTimestamp(selected.timestamp)}
                  </span>
                </div>

                {/* Video element */}
                <div className="bg-black rounded-xl overflow-hidden border border-slate-800 shadow-xl">
                  <video
                    ref={videoRef}
                    controls
                    className="w-full"
                    style={{ maxHeight: '52vh' }}
                  >
                    <source
                      src={`${SERVER_URL}/api/alerts/video/${selected.filename}`}
                      type="video/mp4"
                    />
                    Your browser does not support video playback.
                  </video>
                </div>

                <p className="text-[10px] text-slate-700 text-center font-mono">
                  {selected.filename} · {selected.camera_id}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
