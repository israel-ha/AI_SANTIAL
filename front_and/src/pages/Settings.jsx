import React, { useState, useEffect } from 'react';
import {
  Save, Bell, Shield, Server, Camera, RefreshCw, Volume2, Mail, Cpu,
  CheckCircle, XCircle, Video, PlayCircle, Eye, EyeOff,
} from 'lucide-react';

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ||
  import.meta.env.VITE_SERVER_URL   ||
  'http://localhost:5000';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const buildRtspUrl = (form, maskPassword = false) => {
  const { host, port, path, username, password } = form;
  const pass  = maskPassword && password ? '***' : password;
  const creds = username ? `${username}:${pass}@` : '';
  const pathClean = path.replace(/^\//, '');
  return `rtsp://${creds}${host}:${port}/${pathClean}`;
};

// ---------------------------------------------------------------------------
// Settings page
// ---------------------------------------------------------------------------

const Settings = () => {
  // ── General settings ────────────────────────────────────────────────
  const [thresholds, setThresholds] = useState({ person: 85, vehicle: 70, animal: 60 });
  const [notifications, setNotifications] = useState({ sound: true, email: false, desktop: true });
  const [isSaving, setIsSaving]     = useState(false);
  const [saveStatus, setSaveStatus] = useState(null); // 'success' | 'error' | null

  // ── Video source ─────────────────────────────────────────────────────
  const [videoSource, setVideoSource]       = useState(null);
  const [demoVideos, setDemoVideos]         = useState([]);
  const [videoSourceBusy, setVideoSourceBusy] = useState(false);
  const [videoSourceError, setVideoSourceError] = useState(null);

  // selectedTab tracks which panel is visible; synced from API on load.
  const [selectedTab, setSelectedTab] = useState('demo'); // 'demo' | 'live'

  // ── RTSP form ─────────────────────────────────────────────────────────
  const [rtspForm, setRtspForm] = useState({
    host:     '192.168.0.64',
    port:     '554',
    path:     'live/ch0',
    username: 'admin',
    password: '',
  });
  const [showPassword, setShowPassword] = useState(false);
  const [liveStatus, setLiveStatus]     = useState(null); // 'success' | 'error' | null
  const [liveError, setLiveError]       = useState('');

  // ── Load video state on mount ────────────────────────────────────────
  useEffect(() => {
    fetch(`${API_BASE_URL}/api/video-source`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) {
          setVideoSource(data);
          if (data.mode === 'live' || data.mode === 'demo') setSelectedTab(data.mode);
        }
      })
      .catch(err => console.error('[Settings] video-source load failed:', err));

    fetch(`${API_BASE_URL}/api/available-videos`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setDemoVideos(data.videos || []); })
      .catch(err => console.error('[Settings] available-videos load failed:', err));

    // Pre-fill RTSP form from saved camera config (password comes back masked as ***)
    fetch(`${API_BASE_URL}/api/cameras`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        const cam = data?.cameras?.find(c => c.camera_id === 'CAM_1001');
        if (cam?.rtsp_url) {
          try {
            const u = new URL(cam.rtsp_url);
            setRtspForm(f => ({
              ...f,
              host:     u.hostname  || f.host,
              port:     u.port      || f.port,
              path:     u.pathname?.replace(/^\//, '') || f.path,
              username: u.username  || f.username,
              // leave password blank — backend masks it as ***
            }));
          } catch { /* malformed URL, keep defaults */ }
        }
      })
      .catch(() => {});
  }, []);

  // ── Internal: raw fetch for video-source switch ──────────────────────
  const _fetchVideoSwitch = async (mode, filename) => {
    const body = { mode };
    // Always send the bare filename string — the backend JSON-parses it,
    // so spaces are transmitted verbatim (no URL-encoding needed here).
    if (mode === 'demo' && filename) body.filename = filename;

    console.log('[Settings] POST /api/video-source', body);

    const res = await fetch(`${API_BASE_URL}/api/video-source`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });

    if (!res.ok) {
      // Try to get the descriptive message from the JSON error body.
      let serverMsg = `Server responded with ${res.status}`;
      try {
        const errData = await res.json();
        if (errData?.error?.message) serverMsg = errData.error.message;
      } catch { /* response wasn't JSON — keep the generic message */ }
      console.error('[Settings] video-source switch error:', serverMsg);
      throw new Error(serverMsg);
    }

    return res.json();
  };

  // ── Demo mode switch ─────────────────────────────────────────────────
  const switchVideoSource = async (mode, filename) => {
    if (videoSourceBusy) return;
    setVideoSourceBusy(true);
    setVideoSourceError(null);
    try {
      const data = await _fetchVideoSwitch(mode, filename);
      setVideoSource(data);
    } catch (err) {
      console.error('[Settings] switchVideoSource failed:', err);
      setVideoSourceError(err.message || 'Could not switch video source – check backend connection');
    } finally {
      setVideoSourceBusy(false);
    }
  };

  // ── RTSP connect ──────────────────────────────────────────────────────
  const handleSaveConnect = async () => {
    if (videoSourceBusy) return;
    setVideoSourceBusy(true);
    setLiveStatus(null);
    setLiveError('');

    try {
      const rtsp_url = buildRtspUrl(rtspForm, false);

      // 1. Register camera config → relay to Edge Node on next /ingest
      const camRes = await fetch(`${API_BASE_URL}/api/cameras`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          camera_id:     'CAM_1001',
          rtsp_url,
          rtsp_username: rtspForm.username || null,
          rtsp_password: rtspForm.password || null,
        }),
      });
      if (!camRes.ok) {
        const errBody = await camRes.json().catch(() => ({}));
        throw new Error(errBody.error?.message || `Camera API: ${camRes.status}`);
      }

      // 2. Switch the central server's video worker to live mode
      const srcData = await _fetchVideoSwitch('live');
      setVideoSource(srcData);
      setLiveStatus('success');
      setTimeout(() => setLiveStatus(null), 4000);
    } catch (err) {
      console.error('[Settings] RTSP connect failed:', err);
      setLiveStatus('error');
      setLiveError(err.message || 'Connection failed – check backend');
    } finally {
      setVideoSourceBusy(false);
    }
  };

  // ── Threshold / notification save (existing) ─────────────────────────
  const handleSave = async () => {
    setIsSaving(true);
    setSaveStatus(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/settings`, {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ thresholds, notifications }),
      });
      if (!res.ok) throw new Error(`Server responded with ${res.status}`);
      setSaveStatus('success');
    } catch (err) {
      console.error('[Settings] Save failed:', err);
      setSaveStatus('error');
    } finally {
      setIsSaving(false);
      setTimeout(() => setSaveStatus(null), 4000);
    }
  };

  // ── RTSP helper ──────────────────────────────────────────────────────
  const updateRtsp = (field) => (e) =>
    setRtspForm(f => ({ ...f, [field]: e.target.value }));

  // ────────────────────────────────────────────────────────────────────
  return (
    <div className="max-w-6xl mx-auto space-y-8 pb-12">

      {/* Header */}
      <div className="flex justify-between items-center border-b border-slate-800 pb-6">
        <div>
          <h2 className="text-3xl font-bold text-white">System Configuration</h2>
          <p className="text-slate-400 mt-1">Manage AI sensitivity, notifications, and device status.</p>
        </div>
        <div className="flex items-center gap-3">
          {saveStatus === 'success' && (
            <span className="flex items-center gap-1.5 text-emerald-400 text-sm font-medium">
              <CheckCircle size={16} /> Saved successfully
            </span>
          )}
          {saveStatus === 'error' && (
            <span className="flex items-center gap-1.5 text-red-400 text-sm font-medium">
              <XCircle size={16} /> Save failed – check backend
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-3 rounded-xl flex items-center gap-2 font-medium transition-all shadow-lg shadow-indigo-500/20 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSaving ? <RefreshCw className="animate-spin" size={20} /> : <Save size={20} />}
            {isSaving ? 'Saving…' : 'Save Changes'}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">

        {/* 1. AI Sensitivity */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-3 bg-indigo-500/10 rounded-lg text-indigo-400"><Shield size={24} /></div>
            <div>
              <h3 className="text-xl font-bold text-white">AI Sensitivity Thresholds</h3>
              <p className="text-sm text-slate-500">Minimum confidence score to trigger an alarm</p>
            </div>
          </div>
          <div className="space-y-8">
            <ThresholdSlider label="Person Detection"  value={thresholds.person}  onChange={v => setThresholds({ ...thresholds, person: v })}  color="text-indigo-400" />
            <ThresholdSlider label="Vehicle Detection" value={thresholds.vehicle} onChange={v => setThresholds({ ...thresholds, vehicle: v })} color="text-blue-400" />
            <ThresholdSlider label="Animal Detection"  value={thresholds.animal}  onChange={v => setThresholds({ ...thresholds, animal: v })}  color="text-emerald-400" />
          </div>
        </div>

        {/* 2. Notifications */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
          <div className="flex items-center gap-3 mb-6">
            <div className="p-3 bg-rose-500/10 rounded-lg text-rose-400"><Bell size={24} /></div>
            <div>
              <h3 className="text-xl font-bold text-white">Alert Preferences</h3>
              <p className="text-sm text-slate-500">Customize how you receive alerts</p>
            </div>
          </div>
          <div className="space-y-6">
            <ToggleOption icon={Volume2} label="Sound Alerts"         desc="Play a siren sound on high-priority threats"          active={notifications.sound}   onToggle={() => setNotifications({ ...notifications, sound:   !notifications.sound })} />
            <ToggleOption icon={Mail}    label="Email Reports"         desc="Send daily summary to admin@company.com"              active={notifications.email}   onToggle={() => setNotifications({ ...notifications, email:   !notifications.email })} />
            <ToggleOption icon={Server}  label="Desktop Notifications" desc="Show popup bubbles when browser is minimized"         active={notifications.desktop} onToggle={() => setNotifications({ ...notifications, desktop: !notifications.desktop })} />
          </div>
        </div>

        {/* 3. Video Source Configuration ─────────────────────────────── */}
        <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl lg:col-span-2">

          {/* Card header */}
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <div className="p-3 bg-cyan-500/10 rounded-lg text-cyan-400"><Camera size={24} /></div>
              <div>
                <h3 className="text-xl font-bold text-white">Video Source Configuration</h3>
                <p className="text-sm text-slate-500">Switch between demo footage and a live RTSP camera</p>
              </div>
            </div>
            {videoSource && (
              <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium border
                ${videoSource.running
                  ? 'bg-green-500/10 text-green-400 border-green-500/30'
                  : 'bg-slate-800 text-slate-500 border-slate-700'}`}>
                <div className={`w-1.5 h-1.5 rounded-full ${videoSource.running ? 'bg-green-400 animate-pulse' : 'bg-slate-500'}`} />
                {videoSource.running ? 'Streaming' : 'Stopped'}
              </div>
            )}
          </div>

          {/* Mode tabs */}
          <div className="flex gap-1.5 mb-6 p-1 bg-slate-800/60 rounded-xl w-fit">
            <ModeTab
              active={selectedTab === 'demo'}
              onClick={() => {
                setSelectedTab('demo');
                if (videoSource?.mode !== 'demo') switchVideoSource('demo', videoSource?.filename);
              }}
              icon={PlayCircle}
              label="Demo Video"
            />
            <ModeTab
              active={selectedTab === 'live'}
              onClick={() => setSelectedTab('live')}
              icon={Video}
              label="Live Camera"
            />
          </div>

          {/* ── Demo panel ─────────────────────────────────────────── */}
          {selectedTab === 'demo' && (
            <div className="space-y-4 max-w-lg">
              <p className="text-sm text-slate-400">
                Plays a looping <code className="text-slate-300 bg-slate-800 px-1 rounded">.mp4</code> file
                from the server's video library. Drop any video into{' '}
                <code className="text-slate-300 bg-slate-800 px-1 rounded">assets/videos/</code> to add it here.
              </p>

              {demoVideos.length > 0 ? (
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wider">
                    Demo Scenario
                  </label>
                  <select
                    value={videoSource?.filename || demoVideos[0] || ''}
                    onChange={e => switchVideoSource('demo', e.target.value)}
                    disabled={videoSourceBusy}
                    className="w-full sm:w-80 bg-slate-800 border border-slate-700 text-white text-sm rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {demoVideos.map(name => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                  </select>
                </div>
              ) : (
                <p className="text-xs text-slate-500 italic">
                  No .mp4 files found in assets/videos/ — add videos to enable this selector.
                </p>
              )}

              {videoSource?.mode === 'demo' && videoSource.source && (
                <p className="text-xs font-mono text-slate-600 truncate">{videoSource.source}</p>
              )}

              {videoSourceError && (
                <p className="text-xs text-red-400 flex items-center gap-1.5 mt-2">
                  <XCircle size={13} /> {videoSourceError}
                </p>
              )}
              {videoSourceBusy && (
                <p className="text-xs text-slate-500 flex items-center gap-1.5 mt-2">
                  <RefreshCw className="animate-spin" size={13} /> Switching…
                </p>
              )}
            </div>
          )}

          {/* ── Live RTSP panel ────────────────────────────────────── */}
          {selectedTab === 'live' && (
            <div className="space-y-5 max-w-2xl">
              <p className="text-sm text-slate-400">
                Configure an IP camera's RTSP stream. The credentials are stored server-side only
                and relayed to the Edge Node within one ingest cycle (≤ 1 s).
              </p>

              {/* Form grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <RtspField
                  label="Camera IP / Host"
                  value={rtspForm.host}
                  onChange={updateRtsp('host')}
                  placeholder="192.168.0.64"
                />
                <RtspField
                  label="Port"
                  value={rtspForm.port}
                  onChange={updateRtsp('port')}
                  placeholder="554"
                  type="number"
                />
                <div className="sm:col-span-2">
                  <RtspField
                    label="Stream Path"
                    value={rtspForm.path}
                    onChange={updateRtsp('path')}
                    placeholder="live/ch0"
                  />
                </div>
                <RtspField
                  label="Username"
                  value={rtspForm.username}
                  onChange={updateRtsp('username')}
                  placeholder="admin"
                  autoComplete="username"
                />
                {/* Password with show/hide toggle */}
                <div>
                  <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wider">
                    Password
                  </label>
                  <div className="relative">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={rtspForm.password}
                      onChange={updateRtsp('password')}
                      placeholder="••••••"
                      autoComplete="current-password"
                      className="w-full bg-slate-800 border border-slate-700 text-white text-sm rounded-lg px-3 py-2 pr-10 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-cyan-500 transition-colors"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(v => !v)}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                    >
                      {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </div>
              </div>

              {/* RTSP URL preview */}
              <div className="bg-slate-800/60 border border-slate-700/60 rounded-xl px-4 py-3">
                <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest mb-1.5">
                  Generated RTSP URL
                </p>
                <code className="text-xs text-cyan-300 break-all leading-relaxed">
                  {buildRtspUrl(rtspForm, true)}
                </code>
              </div>

              {/* Status messages */}
              {liveStatus === 'success' && (
                <div className="flex items-center gap-2 text-emerald-400 text-sm bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-2.5">
                  <CheckCircle size={15} />
                  Camera configured — config queued to Edge Node (delivers on next ingest cycle)
                </div>
              )}
              {liveStatus === 'error' && (
                <div className="flex items-center gap-2 text-red-400 text-sm bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2.5">
                  <XCircle size={15} />
                  {liveError || 'Connection failed – check backend'}
                </div>
              )}

              {/* Save & Connect button */}
              <button
                onClick={handleSaveConnect}
                disabled={videoSourceBusy || !rtspForm.host.trim()}
                className="flex items-center gap-2 bg-cyan-600 hover:bg-cyan-700 disabled:opacity-40 disabled:cursor-not-allowed text-white px-6 py-2.5 rounded-xl font-medium transition-all shadow-lg shadow-cyan-500/20 active:scale-95"
              >
                {videoSourceBusy
                  ? <RefreshCw className="animate-spin" size={16} />
                  : <Camera size={16} />}
                {videoSourceBusy ? 'Connecting…' : 'Save & Connect'}
              </button>

              {videoSource?.mode === 'live' && videoSource.source && (
                <p className="text-xs font-mono text-slate-600 truncate">{videoSource.source}</p>
              )}
            </div>
          )}
        </div>

        {/* 4. System Maintenance */}
        <div className="bg-slate-900 border border-red-900/30 rounded-2xl p-6 shadow-xl lg:col-span-2 relative overflow-hidden">
          <div className="absolute top-0 right-0 p-32 bg-red-600/5 blur-[100px] rounded-full pointer-events-none" />
          <div className="flex items-center gap-3 mb-6 relative z-10">
            <div className="p-3 bg-red-500/10 rounded-lg text-red-500"><Cpu size={24} /></div>
            <div>
              <h3 className="text-xl font-bold text-white">System Maintenance</h3>
              <p className="text-sm text-slate-500">Advanced system operations</p>
            </div>
          </div>
          <div className="flex gap-4 relative z-10">
            <button className="px-5 py-2.5 bg-slate-800 border border-slate-700 hover:bg-slate-700 text-white rounded-lg transition-colors">Reboot System</button>
            <button className="px-5 py-2.5 bg-red-500/10 border border-red-500/20 hover:bg-red-500/20 text-red-500 rounded-lg transition-colors">Clear All Logs</button>
            <button className="px-5 py-2.5 bg-slate-800 border border-slate-700 hover:bg-slate-700 text-white rounded-lg transition-colors ml-auto">Check Updates</button>
          </div>
        </div>

      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

const ModeTab = ({ active, onClick, icon: Icon, label }) => (
  <button
    onClick={onClick}
    className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-all
      ${active
        ? 'bg-slate-700 text-white shadow-md'
        : 'text-slate-400 hover:text-white hover:bg-slate-700/50'}`}
  >
    <Icon size={14} />
    {label}
  </button>
);

const RtspField = ({ label, value, onChange, placeholder, type = 'text', autoComplete }) => (
  <div>
    <label className="block text-xs font-medium text-slate-500 mb-1.5 uppercase tracking-wider">
      {label}
    </label>
    <input
      type={type}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      autoComplete={autoComplete}
      className="w-full bg-slate-800 border border-slate-700 text-white text-sm rounded-lg px-3 py-2 placeholder-slate-600 focus:outline-none focus:ring-2 focus:ring-cyan-500 transition-colors"
    />
  </div>
);

const ThresholdSlider = ({ label, value, onChange, color }) => (
  <div>
    <div className="flex justify-between mb-2">
      <span className="text-slate-300 font-medium">{label}</span>
      <span className={`font-bold ${color}`}>{value}%</span>
    </div>
    <input
      type="range" min="0" max="100" value={value}
      onChange={e => onChange(Number(e.target.value))}
      className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
    />
  </div>
);

const ToggleOption = ({ icon: Icon, label, desc, active, onToggle }) => (
  <div className="flex items-center justify-between group cursor-pointer" onClick={onToggle}>
    <div className="flex items-center gap-4">
      <div className={`p-2 rounded-lg transition-colors ${active ? 'bg-indigo-500/20 text-indigo-400' : 'bg-slate-800 text-slate-500'}`}>
        <Icon size={20} />
      </div>
      <div>
        <p className={`font-medium transition-colors ${active ? 'text-white' : 'text-slate-400'}`}>{label}</p>
        <p className="text-xs text-slate-500">{desc}</p>
      </div>
    </div>
    <div className={`w-12 h-6 rounded-full p-1 transition-colors duration-300 ${active ? 'bg-indigo-600' : 'bg-slate-700'}`}>
      <div className={`w-4 h-4 bg-white rounded-full shadow-md transform transition-transform duration-300 ${active ? 'translate-x-6' : 'translate-x-0'}`} />
    </div>
  </div>
);

export default Settings;
