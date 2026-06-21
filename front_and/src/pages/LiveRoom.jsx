import React, { useEffect, useState, useRef, useCallback } from 'react';
import { io } from 'socket.io-client';
import { AlertTriangle, CheckCircle, Activity, Pen, Film } from 'lucide-react';
import DrawingOverlay from '../components/DrawingOverlay';
import DynamicScoringPanel from '../components/DynamicScoringPanel';
import AlertHistoryModal from '../components/AlertHistoryModal';
import { database } from '../firebase';
import { ref, onChildAdded } from 'firebase/database';

const SERVER_URL = import.meta.env.VITE_SERVER_URL || 'http://localhost:5000';

const ZONE_COLORS = {
  Low:    { fill: 'rgba(234, 179, 8, 0.15)',  stroke: '#eab308' },
  Medium: { fill: 'rgba(249, 115, 22, 0.15)', stroke: '#f97316' },
  High:   { fill: 'rgba(239, 68, 68, 0.20)',  stroke: '#ef4444' },
};

const MAX_LOG_ENTRIES = 50;
const SCORE_JUMP_THRESHOLD = 15;

const nowHHMMSS = () => new Date().toTimeString().slice(0, 8);

const makeLogId = (() => { let n = 0; return () => `log_${++n}`; })();

const LiveRoom = () => {
  const [alerts, setAlerts]                 = useState([]);
  const [activeDetections, setActiveDetections] = useState([]);
  const [isDrawingMode, setIsDrawingMode]   = useState(false);

  const socketRef         = useRef(null);
  const imgRef            = useRef(null);
  const canvasRef         = useRef(null);
  const videoContainerRef = useRef(null);
  const prevPersonsRef    = useRef({});   // gid → { zone_risk_level, scores, in_frame }

  const [frameSrc, setFrameSrc]               = useState('');
  const [restrictedZones, setRestrictedZones] = useState([]);
  const [videoMode, setVideoMode]             = useState(null); // 'live' | 'demo' | null
  const [trackingPersons, setTrackingPersons] = useState([]);
  const [activityLog, setActivityLog]         = useState([]);
  const [showHistory, setShowHistory]         = useState(false);

  // ── Fetch current video mode once on mount (for the status badge) ───
  useEffect(() => {
    fetch(`${SERVER_URL}/api/video-source`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.mode) setVideoMode(data.mode); })
      .catch(() => {});
  }, []);

  // ── Activity log helper ──────────────────────────────────────────────
  const pushLog = useCallback((message, severity = 'info', subjectId = null) => {
    setActivityLog(prev => [{
      id: makeLogId(), timestamp: nowHHMMSS(), message, severity, subjectId,
    }, ...prev].slice(0, MAX_LOG_ENTRIES));
  }, []);

  // ── WebSocket connection ─────────────────────────────────────────────
  useEffect(() => {
    socketRef.current = io(SERVER_URL, {
      transports: ['websocket', 'polling'],   // websocket preferred (contract §2.3)
      reconnectionAttempts: 5,
    });

    // Workflow 1 – subscribe to the camera stream on connect
    socketRef.current.on('connect', () => {
      socketRef.current.emit('subscribe_camera', { camera_id: 'CAM_1001' });
    });

    // Workflow 1 – update the <img> with each incoming annotated frame
    socketRef.current.on('processed_frame', (dataUri) => {
      setFrameSrc(dataUri);
    });

    // Workflow 2 – receive the confirmed zones broadcast from the server
    socketRef.current.on('restricted_zones_updated', (data) => {
      setRestrictedZones(data.zones || []);
    });

    socketRef.current.on('alert_batch', (detections) => {
      console.log(`📦 Received batch of ${detections.length} objects`);
      setActiveDetections(detections);
      if (detections.length > 0) {
        setAlerts(prev => [detections[0], ...prev].slice(0, 10));
      }
    });

    // tracking_update — per-person scores + zone risk levels
    socketRef.current.on('tracking_update', (payload) => {
      const persons = payload.persons || [];
      setTrackingPersons(persons);

      const prev = prevPersonsRef.current;
      const nowSeen = new Set(persons.map(p => p.global_id));

      // Detect persons who left the frame
      Object.keys(prev).forEach(gid => {
        if (!nowSeen.has(gid)) {
          pushLog(`Subject ${gid} left the frame`, 'info', gid);
          delete prev[gid];
        }
      });

      persons.forEach(p => {
        const gid    = p.global_id;
        const zone   = p.zone_risk_level;
        const scores = p.scores ?? {};
        const total  = scores.total_person_score ?? 0;
        const climb  = scores.climbing_score     ?? 0;
        const loit   = scores.loitering_score    ?? 0;
        const pData  = prev[gid];

        if (!pData) {
          // First time we see this person
          pushLog(`Subject ${gid} detected in frame`, 'info', gid);
          if (zone) {
            const mult = zone === 'High' ? 'INSTANT ALERT' : zone === 'Medium' ? '×1.5' : '×1.2';
            pushLog(`Entered ${zone} Risk Zone → score ${mult}`, zone === 'High' ? 'critical' : 'warning', gid);
          }
        } else {
          // Zone change
          if (zone !== pData.zone_risk_level) {
            if (zone) {
              const mult = zone === 'High' ? 'INSTANT ALERT' : zone === 'Medium' ? '×1.5' : '×1.2';
              pushLog(`Motion in ${zone} Risk Zone detected → score ${mult}`, zone === 'High' ? 'critical' : 'warning', gid);
            } else if (pData.zone_risk_level) {
              pushLog(`Exited ${pData.zone_risk_level} Risk Zone`, 'info', gid);
            }
          }

          // Score jumps
          const prevTotal = pData.scores?.total_person_score ?? 0;
          const prevClimb = pData.scores?.climbing_score     ?? 0;
          const prevLoit  = pData.scores?.loitering_score    ?? 0;

          if (total >= 100 && prevTotal < 100) {
            pushLog(`ALERT — Total Score reached 100 (max risk)`, 'critical', gid);
          } else if (total - prevTotal >= SCORE_JUMP_THRESHOLD) {
            pushLog(`Total Score escalated ${prevTotal} → ${total}`, total >= 70 ? 'critical' : 'warning', gid);
          }
          if (climb - prevClimb >= SCORE_JUMP_THRESHOLD) {
            pushLog(`Climbing Score escalated ${prevClimb} → ${climb}`, climb >= 50 ? 'warning' : 'info', gid);
          }
          if (loit - prevLoit >= SCORE_JUMP_THRESHOLD) {
            pushLog(`Loitering Score escalated ${prevLoit} → ${loit}`, loit >= 70 ? 'critical' : 'warning', gid);
          }
        }

        prev[gid] = { zone_risk_level: zone, scores };
      });
    });

    return () => socketRef.current.disconnect();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Workflow 3 – Firebase Realtime Database alert subscription ────────
  useEffect(() => {
    const alertsRef = ref(database, '/alerts/CAM_1001');
    const unsubscribe = onChildAdded(alertsRef, (snapshot) => {
      const alert = snapshot.val();
      if (alert && alert.status === 'open') {
        setAlerts(prev => [alert, ...prev].slice(0, 10));
      }
    });
    return () => unsubscribe();
  }, []);

  // ── Bounding-box drawing ─────────────────────────────────────────────
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
      activeDetections.forEach(det => drawSingleBox(ctx, det, canvas.width, canvas.height));
    }

    const timer = setTimeout(() => {}, 1000);
    return () => clearTimeout(timer);
  }, [activeDetections, isDrawingMode]);

  const drawSingleBox = (ctx, detection, width, height) => {
    const { x, y, w, h } = detection.bbox;
    const rectX = x * width;
    const rectY = y * height;
    const rectW = w * width;
    const rectH = h * height;

    ctx.beginPath();
    ctx.lineWidth   = 3;
    ctx.strokeStyle = '#00ff00';
    ctx.rect(rectX, rectY, rectW, rectH);
    ctx.stroke();

    ctx.fillStyle = '#00ff00';
    ctx.fillRect(rectX, rectY - 25, 120, 25);

    ctx.fillStyle = 'black';
    ctx.font      = 'bold 14px Arial';
    ctx.fillText(
      `${detection.label} ${(detection.confidence * 100).toFixed(0)}%`,
      rectX + 5,
      rectY - 7
    );
  };

  // ── Operator decision: emit via socket AND POST to REST API ──────────
  const handleDecision = async (status) => {
    if (activeDetections.length === 0) return;
    const target = activeDetections[0];

    // 1. Real-time channel – keeps the backend ML loop updated immediately
    socketRef.current.emit('feedback', { eventId: target.id, status });

    // 2. REST channel – persists the decision and triggers downstream logic
    try {
      await fetch(`${SERVER_URL}/api/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ eventId: target.id, status }),
      });
    } catch (err) {
      console.error('[LiveRoom] Failed to POST feedback:', err);
    }

    setActiveDetections([]);
  };

  return (
    <>
    <div className="grid grid-cols-12 gap-6 h-[calc(100vh-8rem)]">
      <div className="col-span-8 flex flex-col gap-4">

        {/* Video feed + overlays */}
        <div
          ref={videoContainerRef}
          className="relative bg-black rounded-2xl overflow-hidden aspect-video border-2 border-slate-700"
        >
          <img
            ref={imgRef}
            src={frameSrc}
            className="w-full h-full object-contain"
            alt="Live camera stream"
          />

          {/* Bounding-box canvas (hidden while drawing so it doesn't intercept clicks) */}
          <canvas
            ref={canvasRef}
            className={`absolute top-0 left-0 w-full h-full pointer-events-none z-10 ${isDrawingMode ? 'opacity-0' : ''}`}
          />

          {/* Persistent multi-zone SVG overlay (hidden while actively redrawing) */}
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
        </div>

        {/* Status bar */}
        <div className="bg-slate-800 p-4 rounded text-white flex justify-between items-center">
          <span>
            {activeDetections.length > 0
              ? `⚠️ DETECTED: ${activeDetections.length} Object${activeDetections.length !== 1 ? 's' : ''} Moving`
              : 'Scanning Area (Motion Detection Active)…'}
          </span>
          <div className="flex items-center gap-3">
            {videoMode && (
              <span className={`text-xs px-2 py-0.5 rounded font-mono font-semibold border
                ${videoMode === 'live'
                  ? 'bg-cyan-900/50 text-cyan-400 border-cyan-700/40'
                  : 'bg-slate-700/60 text-slate-400 border-slate-600/40'}`}>
                {videoMode === 'live' ? 'LIVE' : 'DEMO'}
              </span>
            )}
            {activeDetections.length > 0 && (
              <span className="text-xs bg-red-500 px-2 py-1 rounded animate-pulse">MOTION</span>
            )}
            <button
              onClick={() => setIsDrawingMode(v => !v)}
              title="Draw a VCA zone on the feed"
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded transition-colors
                ${isDrawingMode
                  ? 'bg-indigo-600 text-white'
                  : 'bg-slate-700 hover:bg-slate-600 text-slate-300'}`}
            >
              <Pen size={13} />
              {isDrawingMode ? 'Drawing…' : 'Draw Zone'}
            </button>
          </div>
        </div>

        {/* Decision buttons */}
        <div className="grid grid-cols-2 gap-4 h-24">
          <button
            onClick={() => handleDecision('confirmed')}
            disabled={activeDetections.length === 0 || isDrawingMode}
            className={`rounded-xl flex items-center justify-center gap-3 text-xl font-semibold transition-all shadow-lg
              ${activeDetections.length > 0 && !isDrawingMode
                ? 'bg-red-500 hover:bg-red-600 text-white cursor-pointer'
                : 'bg-slate-800 text-slate-600 cursor-not-allowed'}`}
          >
            <AlertTriangle size={28} /> Confirm Alarm
          </button>

          <button
            onClick={() => handleDecision('false_alarm')}
            disabled={activeDetections.length === 0 || isDrawingMode}
            className={`rounded-xl flex items-center justify-center gap-3 text-xl font-semibold transition-all shadow-lg
              ${activeDetections.length > 0 && !isDrawingMode
                ? 'bg-slate-700 hover:bg-emerald-600 text-white cursor-pointer'
                : 'bg-slate-800 text-slate-600 cursor-not-allowed'}`}
          >
            <CheckCircle size={28} /> Mark as False
          </button>
        </div>
      </div>

      {/* Right panel: scoring + compact alerts */}
      <div className="col-span-4 flex flex-col gap-3 min-h-0">

        {/* Alert history button */}
        <button
          onClick={() => setShowHistory(true)}
          className="flex items-center justify-center gap-2 w-full px-4 py-2.5 rounded-xl
            bg-slate-800 hover:bg-indigo-900/40 border border-slate-700 hover:border-indigo-700
            text-slate-300 hover:text-indigo-300 text-xs font-semibold transition-all duration-200 shrink-0"
        >
          <Film size={14} />
          View Alert History
        </button>

        {/* Dynamic scoring panel — takes all remaining height */}
        <div className="flex-1 min-h-0">
          <DynamicScoringPanel
            persons={trackingPersons}
            activityLog={activityLog}
          />
        </div>

        {/* Compact recent alerts strip */}
        <div className="h-44 bg-slate-900 rounded-2xl border border-slate-800 flex flex-col overflow-hidden shrink-0">
          <div className="px-3 py-2 border-b border-slate-800 flex items-center gap-2 shrink-0">
            <Activity size={13} className="text-indigo-400" />
            <h3 className="font-semibold text-slate-100 text-xs">Recent Alerts</h3>
          </div>
          <div className="flex-1 overflow-auto px-2 py-1 space-y-1 custom-scrollbar">
            {alerts.length === 0 ? (
              <p className="text-[10px] text-slate-700 italic px-1 pt-1">No alerts yet…</p>
            ) : alerts.map((alert, idx) => (
              <div key={alert.alert_id ?? idx} className="bg-slate-800/50 px-2 py-1.5 rounded-lg border border-slate-700/40 flex items-center gap-2">
                <span className={`text-[10px] px-1 py-0.5 rounded font-bold shrink-0
                  ${alert.severity === 'high'   ? 'bg-red-900/70 text-red-300'    :
                    alert.severity === 'medium' ? 'bg-amber-900/70 text-amber-300' :
                                                  'bg-slate-700 text-slate-400'}`}>
                  {(alert.severity ?? 'low').toUpperCase()}
                </span>
                <span className="text-[10px] text-red-400 font-semibold capitalize truncate flex-1">
                  {alert.alert_type}
                </span>
                <span className="text-[10px] text-slate-600 font-mono shrink-0">
                  {alert.timestamp_iso ? alert.timestamp_iso.split('T')[1].slice(0, 8) : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>

    {/* Alert history modal */}
    <AlertHistoryModal
      isOpen={showHistory}
      onClose={() => setShowHistory(false)}
    />
    </>
  );
};

export default LiveRoom;
