import React, { useEffect, useState, useRef, useCallback } from 'react';
import { io } from 'socket.io-client';
import { AlertTriangle, CheckCircle, TrendingUp, Pen, Film } from 'lucide-react';
import DrawingOverlay from '../components/DrawingOverlay';
import DynamicScoringPanel from '../components/DynamicScoringPanel';
import AlertHistoryModal from '../components/AlertHistoryModal';
import SystemLogs from '../components/SystemLogs';
import { database } from '../firebase';
import { ref, onChildAdded } from 'firebase/database';

const SERVER_URL = import.meta.env.VITE_SERVER_URL || 'http://localhost:5000';
const MJPEG_URL  = `${SERVER_URL}/api/stream.mjpeg`;

const ZONE_COLORS = {
  Low:    { fill: 'rgba(234, 179, 8, 0.15)',  stroke: '#eab308' },
  Medium: { fill: 'rgba(249, 115, 22, 0.15)', stroke: '#f97316' },
  High:   { fill: 'rgba(239, 68, 68, 0.20)',  stroke: '#ef4444' },
};

const MAX_LOG_ENTRIES    = 50;
const SCORE_JUMP_THRESHOLD = 15;
const BANNER_DURATION_MS = 7000;   // how long a threat banner stays visible after last update

const nowHHMMSS = () => new Date().toTimeString().slice(0, 8);
const makeLogId = (() => { let n = 0; return () => `log_${++n}`; })();

// ---------------------------------------------------------------------------
// Threat Banner helpers
// ---------------------------------------------------------------------------

const _fmtBehavior = (t) => t.charAt(0).toUpperCase() + t.slice(1);
const _fmtBehaviors = (arr) => arr.map(_fmtBehavior).join(' + ');

const _bannerLevel = (types) => {
  if (types.includes('climbing'))  return 'critical';
  if (types.includes('intrusion')) return 'high';
  return 'medium';
};

const BANNER_STYLE = {
  critical: { border: 'rgba(239,68,68,0.6)',  bg: 'rgba(60,8,8,0.93)',   accent: '#f87171', label: 'CRITICAL THREAT' },
  high:     { border: 'rgba(249,115,22,0.6)', bg: 'rgba(60,20,5,0.93)',  accent: '#fb923c', label: 'HIGH THREAT'      },
  medium:   { border: 'rgba(234,179,8,0.5)',  bg: 'rgba(54,28,4,0.93)',  accent: '#fbbf24', label: 'THREAT DETECTED'  },
};

// One banner per subject — shows current behaviors and escalation path if applicable.
const ThreatBanner = ({ gid, banner }) => {
  const s           = BANNER_STYLE[banner.level] || BANNER_STYLE.medium;
  const isEscalated = banner.escalationFrom?.length > 0;

  return (
    <div
      className="rounded-xl px-3 py-2.5 backdrop-blur-md flex flex-col gap-1.5 shadow-2xl"
      style={{ border: `1px solid ${s.border}`, background: s.bg, minWidth: 210 }}
    >
      {/* Header row */}
      <div className="flex items-center justify-between gap-6">
        <div className="flex items-center gap-1.5">
          <span
            className="w-1.5 h-1.5 rounded-full animate-pulse shrink-0"
            style={{ background: s.accent, boxShadow: `0 0 6px ${s.accent}` }}
          />
          <span className="text-[9px] font-bold tracking-[0.15em]" style={{ color: s.accent }}>
            {s.label}
          </span>
        </div>
        <span className="text-[9px] text-slate-500 font-mono shrink-0">#{gid}</span>
      </div>

      {/* Behavior row — escalation path or current state */}
      {isEscalated ? (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[11px] text-slate-400">{_fmtBehaviors(banner.escalationFrom)}</span>
          <span className="text-slate-600">→</span>
          <span className="text-[12px] font-bold" style={{ color: s.accent }}>
            {_fmtBehaviors(banner.behaviors)}
          </span>
          <span className="text-[9px] font-bold text-red-400 animate-pulse">↑ ESCALATED</span>
        </div>
      ) : (
        <span className="text-[13px] font-bold" style={{ color: s.accent }}>
          {_fmtBehaviors(banner.behaviors)}
        </span>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------

const LiveRoom = () => {
  const [alerts, setAlerts]                     = useState([]);
  const [activeDetections, setActiveDetections] = useState([]);
  const [isDrawingMode, setIsDrawingMode]       = useState(false);
  const [restrictedZones, setRestrictedZones]   = useState([]);
  const [videoMode, setVideoMode]               = useState(null);
  const [trackingPersons, setTrackingPersons]   = useState([]);
  const [activityLog, setActivityLog]           = useState([]);
  const [showHistory, setShowHistory]           = useState(false);

  const socketRef         = useRef(null);
  const imgRef            = useRef(null);
  const canvasRef         = useRef(null);
  const videoContainerRef = useRef(null);
  const prevPersonsRef    = useRef({});
  const bannerTimersRef   = useRef({});   // global_id → expiry timeout handle

  const [threatBanners, setThreatBanners] = useState(new Map());   // global_id → banner state

  // Derived: is the operator being asked to make a decision right now?
  const hasActiveAlert = activeDetections.length > 0;

  // Is any tracked subject currently exhibiting climbing behaviour?
  const climbingActive = trackingPersons.some(p => p.alert_types?.includes('climbing'));

  // ── Fetch initial video mode ───────────────────────────────────────────
  useEffect(() => {
    fetch(`${SERVER_URL}/api/video-source`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.mode) setVideoMode(data.mode); })
      .catch(() => {});
  }, []);

  // ── Activity log helper ────────────────────────────────────────────────
  const pushLog = useCallback((message, severity = 'info', subjectId = null) => {
    setActivityLog(prev => [{
      id: makeLogId(), timestamp: nowHHMMSS(), message, severity, subjectId,
    }, ...prev].slice(0, MAX_LOG_ENTRIES));
  }, []);

  // ── WebSocket ──────────────────────────────────────────────────────────
  useEffect(() => {
    socketRef.current = io(SERVER_URL, {
      transports: ['websocket', 'polling'],
      reconnectionAttempts: 5,
    });

    socketRef.current.on('connect', () => {
      socketRef.current.emit('subscribe_camera', { camera_id: 'CAM_1001' });
    });

    socketRef.current.on('restricted_zones_updated', (data) => {
      setRestrictedZones(data.zones || []);
    });

    socketRef.current.on('alert_batch', (detections) => {
      setActiveDetections(detections);
      if (detections.length > 0) {
        setAlerts(prev => [detections[0], ...prev].slice(0, MAX_LOG_ENTRIES));
      }
    });

    socketRef.current.on('tracking_update', (payload) => {
      const persons = payload.persons || [];
      setTrackingPersons(persons);

      const prev    = prevPersonsRef.current;
      const nowSeen = new Set(persons.map(p => p.global_id));

      // Log subjects who left the frame
      Object.keys(prev).forEach(gid => {
        if (!nowSeen.has(gid)) {
          pushLog(`Subject ${gid} left the frame`, 'info', gid);
          delete prev[gid];
        }
      });

      persons.forEach(p => {
        const gid        = p.global_id;
        const zone       = p.zone_risk_level;
        const scores     = p.scores ?? {};
        const alertTypes = p.alert_types ?? [];
        const total      = scores.total_person_score ?? 0;
        const loit       = scores.loitering_score    ?? 0;
        const pData      = prev[gid];

        if (!pData) {
          pushLog(`Subject ${gid} detected`, 'info', gid);
          if (zone) {
            const mult = zone === 'High' ? 'INSTANT ALERT' : zone === 'Medium' ? '×1.5' : '×1.2';
            pushLog(`Entered ${zone} Risk Zone → ${mult}`, zone === 'High' ? 'critical' : 'warning', gid);
          }
        } else {
          // Zone transition
          if (zone !== pData.zone_risk_level) {
            if (zone) {
              const mult = zone === 'High' ? 'INSTANT ALERT' : zone === 'Medium' ? '×1.5' : '×1.2';
              pushLog(`${zone} Risk Zone — ${mult}`, zone === 'High' ? 'critical' : 'warning', gid);
            } else if (pData.zone_risk_level) {
              pushLog(`Exited ${pData.zone_risk_level} zone`, 'info', gid);
            }
          }

          // Climbing: fires on rising edge (was absent, now present)
          const prevAlertTypes = pData.alert_types ?? [];
          if (alertTypes.includes('climbing') && !prevAlertTypes.includes('climbing')) {
            pushLog('CLIMBING detected — kinematic trigger fired', 'critical', gid);
          }

          // Score escalations
          const prevTotal = pData.scores?.total_person_score ?? 0;
          const prevLoit  = pData.scores?.loitering_score    ?? 0;

          if (total >= 100 && prevTotal < 100) {
            pushLog('Max threat score reached — immediate review required', 'critical', gid);
          } else if (total - prevTotal >= SCORE_JUMP_THRESHOLD) {
            pushLog(`Threat score escalated ${prevTotal} → ${total}`, total >= 70 ? 'critical' : 'warning', gid);
          }
          if (loit - prevLoit >= SCORE_JUMP_THRESHOLD) {
            pushLog(`Loitering score escalated ${prevLoit} → ${loit}`, loit >= 70 ? 'critical' : 'warning', gid);
          }
        }

        // ── Threat banner aggregation ─────────────────────────────────────
        // One banner per subject. If new behaviors are added to an existing
        // banner, record the escalation path and reset the expiry timer so
        // the operator has a full 7 s to read the updated state.
        if (alertTypes.length > 0) {
          setThreatBanners(prev => {
            const next     = new Map(prev);
            const existing = next.get(gid);
            const prevBeh  = existing?.behaviors ?? [];
            const added    = alertTypes.filter(t => !prevBeh.includes(t));
            const allBeh   = [...new Set([...prevBeh, ...alertTypes])];
            next.set(gid, {
              behaviors:      allBeh,
              // Capture the "before" snapshot only on the first escalation event
              escalationFrom: added.length > 0 && prevBeh.length > 0
                ? prevBeh
                : (existing?.escalationFrom ?? null),
              level: _bannerLevel(allBeh),
            });
            return next;
          });
          clearTimeout(bannerTimersRef.current[gid]);
          bannerTimersRef.current[gid] = setTimeout(() => {
            setThreatBanners(m => { const n = new Map(m); n.delete(gid); return n; });
          }, BANNER_DURATION_MS);
        }

        prev[gid] = { zone_risk_level: zone, scores, alert_types: alertTypes };
      });
    });

    return () => {
      socketRef.current.disconnect();
      Object.values(bannerTimersRef.current).forEach(clearTimeout);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Firebase Realtime Database alert subscription ──────────────────────
  useEffect(() => {
    const alertsRef  = ref(database, '/alerts/CAM_1001');
    const unsubscribe = onChildAdded(alertsRef, (snapshot) => {
      const alert = snapshot.val();
      if (alert && alert.status === 'open') {
        setAlerts(prev => [alert, ...prev].slice(0, MAX_LOG_ENTRIES));
      }
    });
    return () => unsubscribe();
  }, []);

  // ── Canvas bounding-box overlay ────────────────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    const img    = imgRef.current;
    if (!canvas || !img) return;

    const ctx = canvas.getContext('2d');
    if (canvas.width !== img.clientWidth || canvas.height !== img.clientHeight) {
      canvas.width  = img.clientWidth;
      canvas.height = img.clientHeight;
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!isDrawingMode) {
      activeDetections.forEach(det => _drawBox(ctx, det, canvas.width, canvas.height));
    }
  }, [activeDetections, isDrawingMode]);

  const _drawBox = (ctx, detection, w, h) => {
    const { x, y, w: bw, h: bh } = detection.bbox;
    const rx = x * w, ry = y * h, rw = bw * w, rh = bh * h;

    ctx.save();
    ctx.strokeStyle = '#22d3ee';
    ctx.lineWidth   = 1.5;
    ctx.strokeRect(rx, ry, rw, rh);
    ctx.font        = 'bold 11px monospace';
    ctx.fillStyle   = '#22d3ee';
    ctx.fillText(
      `${detection.label} ${(detection.confidence * 100).toFixed(0)}%`,
      rx + 4, ry + 14,
    );
    ctx.restore();
  };

  // ── Operator decision ──────────────────────────────────────────────────
  const handleDecision = async (status) => {
    if (activeDetections.length === 0) return;
    const target = activeDetections[0];

    socketRef.current.emit('feedback', { eventId: target.id, status });
    try {
      await fetch(`${SERVER_URL}/api/feedback`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ eventId: target.id, status }),
      });
    } catch (err) {
      console.error('[LiveRoom] feedback POST failed:', err);
    }
    setActiveDetections([]);
  };

  // ── Render ─────────────────────────────────────────────────────────────
  return (
    <>
      <div className="grid grid-cols-12 gap-4 h-[calc(100vh-8rem)]">

        {/* ── LEFT: video feed + action bar ───────────────────── */}
        <div className="col-span-8 flex flex-col gap-3">

          {/* Video — fills all available height above the action bar */}
          <div
            ref={videoContainerRef}
            className="relative bg-black rounded-2xl overflow-hidden flex-1 min-h-0 border border-slate-800"
          >
            {/* MJPEG stream — browser decodes natively, no Socket.IO overhead */}
            <img
              ref={imgRef}
              src={MJPEG_URL}
              className="w-full h-full object-contain"
              alt="Live camera stream"
            />

            {/* Client-side bounding-box canvas */}
            <canvas
              ref={canvasRef}
              className={`absolute top-0 left-0 w-full h-full pointer-events-none z-10 ${isDrawingMode ? 'opacity-0' : ''}`}
            />

            {/* Zone polygon SVG overlay */}
            {restrictedZones.length > 0 && !isDrawingMode && (
              <svg
                className="absolute inset-0 w-full h-full pointer-events-none z-10"
                viewBox="0 0 1 1"
                preserveAspectRatio="none"
              >
                {restrictedZones.map(zone => {
                  if (!zone.points || zone.points.length < 3) return null;
                  const c = ZONE_COLORS[zone.riskLevel] || ZONE_COLORS.Medium;
                  return (
                    <polygon
                      key={zone.id}
                      points={zone.points.map(p => `${p.x},${p.y}`).join(' ')}
                      fill={c.fill}
                      stroke={c.stroke}
                      strokeWidth="2"
                      vectorEffect="non-scaling-stroke"
                    />
                  );
                })}
              </svg>
            )}

            {/* VCA drawing overlay */}
            {isDrawingMode && (
              <DrawingOverlay
                containerRef={videoContainerRef}
                onClose={() => setIsDrawingMode(false)}
                initialZones={restrictedZones}
                onSubmitZones={(zones) => {
                  setRestrictedZones(zones);
                  socketRef.current?.emit('update_restricted_zone', { zones });
                }}
              />
            )}

            {/* Top-left: mode badge */}
            <div className="absolute top-3 left-3 z-20">
              {videoMode && (
                <span className={`text-[10px] px-2 py-0.5 rounded font-mono font-bold border
                  ${videoMode === 'live'
                    ? 'bg-cyan-900/70 text-cyan-300 border-cyan-700/50'
                    : 'bg-slate-800/80 text-slate-500 border-slate-700/50'}`}>
                  {videoMode === 'live' ? '● LIVE' : 'DEMO'}
                </span>
              )}
            </div>

            {/* Top-right: active threat banners — one per subject, auto-expire after 7 s */}
            {threatBanners.size > 0 && (
              <div className="absolute top-3 right-3 z-30 flex flex-col gap-2 items-end">
                {[...threatBanners.entries()].map(([gid, banner]) => (
                  <ThreatBanner key={gid} gid={gid} banner={banner} />
                ))}
              </div>
            )}

            {/* Bottom overlay: climbing kinematic alert — visible only when triggered */}
            {climbingActive && (
              <div className="absolute bottom-0 left-0 right-0 z-20 flex items-center justify-center gap-2 py-2 bg-red-600/90 animate-pulse">
                <TrendingUp size={14} className="text-white" />
                <span className="text-white text-xs font-bold tracking-[0.12em]">
                  ⚠ CLIMBING BEHAVIOUR DETECTED
                </span>
              </div>
            )}
          </div>

          {/* ── Action bar ──────────────────────────────────────── */}
          {/*
            Fixed-height slot: quietly shows "Armed" when idle;
            reveals Confirm / False buttons the moment a detection arrives.
            No layout shift — the height never changes.
          */}
          <div className="h-17 flex gap-3 shrink-0">

            {hasActiveAlert ? (
              /* Decision buttons — rendered only when operator action is required */
              <>
                <button
                  onClick={() => handleDecision('confirmed')}
                  disabled={isDrawingMode}
                  className="flex-1 rounded-xl flex items-center justify-center gap-2 text-sm font-bold
                    bg-red-500 hover:bg-red-600 active:scale-[0.98] text-white
                    transition-all duration-150 shadow-lg shadow-red-900/40
                    disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <AlertTriangle size={18} />
                  Confirm Alarm
                </button>
                <button
                  onClick={() => handleDecision('false_alarm')}
                  disabled={isDrawingMode}
                  className="flex-1 rounded-xl flex items-center justify-center gap-2 text-sm font-bold
                    bg-slate-700 hover:bg-emerald-700 active:scale-[0.98] text-white
                    transition-all duration-150 shadow-lg
                    disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <CheckCircle size={18} />
                  Mark as False
                </button>
              </>
            ) : (
              /* Calm status indicator — no visual noise when system is idle */
              <div className="flex-1 bg-slate-900/50 rounded-xl border border-slate-800/80 flex items-center justify-center gap-3">
                <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_6px_rgba(52,211,153,0.6)] animate-pulse" />
                <span className="text-[11px] text-slate-600 tracking-[0.14em] uppercase font-semibold select-none">
                  System Armed &amp; Monitoring
                </span>
              </div>
            )}

            {/* Secondary controls */}
            <div className="flex flex-col gap-1.5 shrink-0">
              <button
                onClick={() => setIsDrawingMode(v => !v)}
                title="Draw VCA zone"
                className={`flex items-center gap-1.5 px-3 text-xs rounded-lg flex-1 transition-colors
                  ${isDrawingMode
                    ? 'bg-indigo-600 text-white'
                    : 'bg-slate-800 hover:bg-slate-700 text-slate-500 border border-slate-700'}`}
              >
                <Pen size={11} />
                {isDrawingMode ? 'Drawing…' : 'Zone'}
              </button>
              <button
                onClick={() => setShowHistory(true)}
                title="Alert History"
                className="flex items-center gap-1.5 px-3 text-xs rounded-lg flex-1
                  bg-slate-800 hover:bg-slate-700 text-slate-500 border border-slate-700 transition-colors"
              >
                <Film size={11} />
                History
              </button>
            </div>
          </div>
        </div>

        {/* ── RIGHT: threat panel + unified logs ──────────────── */}
        <div className="col-span-4 flex flex-col gap-3 min-h-0">

          {/* Threat analysis — content-sized, shrinks when idle */}
          <div className="shrink-0">
            <DynamicScoringPanel persons={trackingPersons} />
          </div>

          {/* System logs — tabbed Alerts / Activity — takes all remaining height */}
          <SystemLogs alerts={alerts} activityLog={activityLog} />
        </div>
      </div>

      <AlertHistoryModal isOpen={showHistory} onClose={() => setShowHistory(false)} />
    </>
  );
};

export default LiveRoom;
