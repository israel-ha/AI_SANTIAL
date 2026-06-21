import React, { useRef, useState, useEffect, useCallback } from 'react';
import { Pen, Trash2, Save, X, ShieldAlert } from 'lucide-react';

/**
 * DrawingOverlay – lets the operator draw multiple sensitive zones on the
 * live feed, each tagged with a risk level (Low / Medium / High).
 *
 * Props:
 *   containerRef   – ref to the parent video-container element (used for sizing)
 *   onClose        – callback to exit drawing mode
 *   initialZones   – existing zones array from LiveRoom state
 *   onSubmitZones  – callback(zones) fired whenever the zones list changes;
 *                    LiveRoom uses this to emit via Socket.IO
 */

const RISK_COLORS = {
  Low:    { stroke: '#eab308', fill: 'rgba(234, 179, 8, 0.15)',   dot: '#eab308' },
  Medium: { stroke: '#f97316', fill: 'rgba(249, 115, 22, 0.15)',  dot: '#f97316' },
  High:   { stroke: '#ef4444', fill: 'rgba(239, 68, 68, 0.20)',   dot: '#ef4444' },
};

const RISK_LABELS = {
  Low:    'Low Risk',
  Medium: 'Medium Risk',
  High:   'High — No-Go',
};

const DrawingOverlay = ({ containerRef, onClose, initialZones = [], onSubmitZones }) => {
  const canvasRef = useRef(null);

  // All committed zones
  const [zones, setZones]               = useState(initialZones);
  // Points being placed for the next zone
  const [currentPoints, setCurrentPoints] = useState([]);
  // Risk level chosen for the zone being drawn
  const [riskLevel, setRiskLevel]       = useState('Medium');
  // Track canvas size so the draw effect re-fires on resize
  const [canvasSize, setCanvasSize]     = useState({ w: 0, h: 0 });

  const [saveStatus, setSaveStatus] = useState(null); // 'success' | null

  // ── Sync canvas size to the video container ──────────────────────────
  useEffect(() => {
    const canvas    = canvasRef.current;
    const container = containerRef?.current;
    if (!canvas || !container) return;

    const sync = () => {
      canvas.width  = container.clientWidth;
      canvas.height = container.clientHeight;
      setCanvasSize({ w: container.clientWidth, h: container.clientHeight });
    };
    sync();

    const ro = new ResizeObserver(sync);
    ro.observe(container);
    return () => ro.disconnect();
  }, [containerRef]);

  // ── Redraw all zones + current in-progress polygon ───────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || canvasSize.w === 0) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw committed zones (slightly faded)
    zones.forEach(zone => {
      if (!zone.points || zone.points.length < 3) return;
      const color = RISK_COLORS[zone.riskLevel] || RISK_COLORS.Medium;
      const pts   = zone.points.map(p => [p.x * canvas.width, p.y * canvas.height]);
      drawPolygon(ctx, pts, color, 0.65);
    });

    // Draw the zone currently being placed (full opacity)
    if (currentPoints.length > 0) {
      const color = RISK_COLORS[riskLevel] || RISK_COLORS.Medium;
      const pts   = currentPoints.map(p => [p.px, p.py]);
      drawPolygon(ctx, pts, color, 1.0);

      // Dashed close-hint back to origin
      if (currentPoints.length >= 3) {
        ctx.beginPath();
        ctx.moveTo(currentPoints[currentPoints.length - 1].px, currentPoints[currentPoints.length - 1].py);
        ctx.lineTo(currentPoints[0].px, currentPoints[0].py);
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = color.stroke + '80';
        ctx.lineWidth   = 1.5;
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }
  }, [zones, currentPoints, riskLevel, canvasSize]);

  // ── Click to place a point ────────────────────────────────────────────
  const handleCanvasClick = useCallback((e) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const px   = e.clientX - rect.left;
    const py   = e.clientY - rect.top;
    setCurrentPoints(prev => [...prev, { px, py, nx: px / canvas.width, ny: py / canvas.height }]);
  }, []);

  // ── Commit the current polygon as a new zone ──────────────────────────
  const handleSaveZone = () => {
    if (currentPoints.length < 3) return;
    const newZone = {
      id:        `zone_${Date.now()}`,
      points:    currentPoints.map(p => ({ x: p.nx, y: p.ny })),
      riskLevel,
    };
    const updated = [...zones, newZone];
    setZones(updated);
    setCurrentPoints([]);
    onSubmitZones?.(updated);
    setSaveStatus('success');
    setTimeout(() => setSaveStatus(null), 2000);
  };

  // ── Delete a committed zone ───────────────────────────────────────────
  const handleDeleteZone = (zoneId) => {
    const updated = zones.filter(z => z.id !== zoneId);
    setZones(updated);
    onSubmitZones?.(updated);
  };

  return (
    <div className="absolute inset-0 z-20">

      {/* Clickable drawing canvas */}
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full cursor-crosshair"
        onClick={handleCanvasClick}
      />

      {/* ── Top-right control panel ────────────────────────────────────── */}
      <div className="absolute top-2 right-2 flex flex-col gap-2 z-30 max-w-55">

        {/* Drawing toolbar */}
        <div className="bg-slate-900/90 backdrop-blur-sm rounded-xl p-2 flex flex-col gap-2 border border-slate-700/60 shadow-xl">

          {/* Risk level selector */}
          <div className="flex items-center gap-1.5">
            <ShieldAlert size={13} className="text-slate-400 shrink-0" />
            <select
              value={riskLevel}
              onChange={e => setRiskLevel(e.target.value)}
              className="flex-1 bg-slate-800 text-white text-xs rounded-md px-2 py-1.5 border border-slate-600 focus:outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
            >
              <option value="Low">Low Risk</option>
              <option value="Medium">Medium Risk</option>
              <option value="High">High — No-Go</option>
            </select>
          </div>

          {/* Risk level color indicator */}
          <div
            className="h-1 rounded-full"
            style={{ background: RISK_COLORS[riskLevel]?.stroke ?? '#6366f1' }}
          />

          {/* Action buttons */}
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setCurrentPoints([])}
              title="Clear current drawing"
              className="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-300 px-2 py-1.5 rounded-lg text-xs flex items-center justify-center gap-1"
            >
              <Trash2 size={12} /> Clear
            </button>

            <button
              onClick={handleSaveZone}
              disabled={currentPoints.length < 3}
              className="flex-1 text-white px-2 py-1.5 rounded-lg text-xs flex items-center justify-center gap-1 font-medium disabled:opacity-40 disabled:cursor-not-allowed"
              style={{
                background: currentPoints.length >= 3
                  ? (RISK_COLORS[riskLevel]?.stroke ?? '#6366f1')
                  : undefined,
                backgroundColor: currentPoints.length < 3 ? '#334155' : undefined,
              }}
            >
              <Save size={12} />
              {currentPoints.length >= 3 ? 'Save Zone' : `${currentPoints.length}/3 pts`}
            </button>

            <button
              onClick={onClose}
              title="Exit drawing mode"
              className="bg-slate-800 hover:bg-red-900/60 text-slate-400 hover:text-white px-2 py-1.5 rounded-lg"
            >
              <X size={13} />
            </button>
          </div>

          {saveStatus === 'success' && (
            <p className="text-emerald-400 text-xs text-center font-medium">Zone saved!</p>
          )}
        </div>

        {/* Saved zones list */}
        {zones.length > 0 && (
          <div className="bg-slate-900/90 backdrop-blur-sm rounded-xl border border-slate-700/60 shadow-xl overflow-hidden">
            <p className="text-slate-400 text-[10px] font-semibold uppercase tracking-wider px-3 pt-2 pb-1">
              Zones ({zones.length})
            </p>
            <div className="flex flex-col divide-y divide-slate-700/40">
              {zones.map((zone, idx) => {
                const color = RISK_COLORS[zone.riskLevel] || RISK_COLORS.Medium;
                return (
                  <div
                    key={zone.id}
                    className="flex items-center justify-between px-3 py-1.5 gap-2"
                  >
                    <div className="flex items-center gap-1.5 min-w-0">
                      <span
                        className="w-2 h-2 rounded-full shrink-0"
                        style={{ background: color.stroke }}
                      />
                      <span className="text-slate-300 text-xs truncate">
                        Zone {idx + 1}
                      </span>
                      <span
                        className="text-[10px] font-medium shrink-0"
                        style={{ color: color.stroke }}
                      >
                        {zone.riskLevel}
                      </span>
                    </div>
                    <button
                      onClick={() => handleDeleteZone(zone.id)}
                      title="Delete zone"
                      className="text-slate-500 hover:text-red-400 transition-colors shrink-0"
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ── Bottom-left hint ───────────────────────────────────────────── */}
      <div className="absolute bottom-2 left-2 bg-slate-900/80 text-slate-300 text-xs px-3 py-1.5 rounded-lg backdrop-blur-sm flex items-center gap-1.5 pointer-events-none">
        <Pen size={12} />
        {currentPoints.length === 0
          ? 'Click to place vertices — need 3+ for a zone'
          : `${currentPoints.length} point${currentPoints.length !== 1 ? 's' : ''} placed${currentPoints.length >= 3 ? ' · ready to save' : ''}`}
      </div>
    </div>
  );
};

// ── Canvas drawing helper ──────────────────────────────────────────────────

function drawPolygon(ctx, pts, color, alpha) {
  if (pts.length < 1) return;

  ctx.save();
  ctx.globalAlpha = alpha;

  // Fill
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  pts.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
  if (pts.length >= 3) ctx.closePath();
  ctx.fillStyle = color.fill;
  ctx.fill();

  // Stroke
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  pts.slice(1).forEach(([x, y]) => ctx.lineTo(x, y));
  if (pts.length >= 3) ctx.closePath();
  ctx.strokeStyle = color.stroke;
  ctx.lineWidth   = 2;
  ctx.stroke();

  // Vertex dots
  pts.forEach(([x, y]) => {
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fillStyle   = color.dot;
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth   = 1.5;
    ctx.stroke();
  });

  ctx.restore();
}

export default DrawingOverlay;
