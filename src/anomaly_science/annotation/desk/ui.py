"""Browser HTML assets for the level desk."""

from __future__ import annotations

LAUNCHER_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Annotation Browser</title>
  <style>
    :root {
      --bg:#111318; --panel:#171a20; --panel-soft:#20242c; --text:#e7e3d8;
      --muted:#9b9a93; --line:#2a2e36; --accent:#8aa0d8; --accent-soft:#222b45;
      --good:#7dbb91; --bad:#d18495;
    }
    * { box-sizing:border-box; }
    * { scrollbar-width: thin; scrollbar-color: #555d6b #15171c; }
    *::-webkit-scrollbar { width: 9px; height: 9px; }
    *::-webkit-scrollbar-track { background: #15171c; }
    *::-webkit-scrollbar-thumb { background: #555d6b; border-radius: 0; }
    *::-webkit-scrollbar-thumb:hover { background: #687181; }
    body {
      margin:0; min-height:100vh; font-family:Inter, "IBM Plex Sans", Segoe UI, Arial, sans-serif;
      color:var(--text); background:var(--bg);
    }
    main { max-width:960px; margin:0 auto; padding:64px 24px; }
    .kicker { color:var(--accent); letter-spacing:.10em; text-transform:uppercase; font-weight:760; font-size:12px; }
    h1 { margin:12px 0 10px; font-size:42px; line-height:1.05; letter-spacing:-.035em; font-weight:760; }
    .sub { color:var(--muted); max-width:740px; line-height:1.5; }
    #strategies { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:14px; margin-top:30px; }
    .card { border-radius:10px; padding:22px; background:var(--panel); box-shadow:none; }
    .card h2 { margin:0 0 10px; font-size:19px; letter-spacing:-.015em; }
    .metric { display:inline-block; margin:6px 6px 0 0; padding:5px 9px; border-radius:7px; color:var(--muted); background:var(--panel-soft); font-size:12px; }
    .metric b { color:var(--text); }
    .inputs { margin-top:16px; padding-top:14px; border-top:1px solid var(--line); color:var(--muted); font-size:13px; line-height:1.55; }
    a.button { display:inline-block; margin-top:18px; color:#101217; background:var(--accent); text-decoration:none; font-weight:760; padding:10px 14px; border-radius:8px; }
    .empty { color:var(--bad); border-radius:8px; padding:14px; background:#24171c; }
  </style>
</head>
<body>
  <main>
    <div class="kicker">Anomaly science annotation</div>
    <h1>Choose a supported labeling strategy</h1>
    <p class="sub">This launcher only lists strategies with a real candidate artifact and a declared browser input schema. No placeholder strategies are exposed.</p>
    <section id="strategies"></section>
  </main>
  <script>
    function esc(x) { return String(x ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }
    async function boot() {
      const r = await fetch('/api/strategies');
      const data = await r.json();
      const root = document.getElementById('strategies');
      if (!data.strategies.length) {
        root.innerHTML = '<div class="empty">No annotation-capable strategies are registered.</div>';
        return;
      }
      root.innerHTML = data.strategies.map(s => `
        <article class="card">
          <h2>${esc(s.title)}</h2>
          <span class="metric">events <b>${s.candidate_count}</b></span>
          <span class="metric">rows <b>${s.candidate_rows}</b></span>
          <div class="inputs"><b>Inputs:</b><br>${s.inputs.map(i => esc(i.label)).join('<br>')}</div>
          <a class="button" href="/labeler?strategy=${encodeURIComponent(s.strategy_id)}">Open annotation desk</a>
        </article>
      `).join('');
    }
    boot();
  </script>
</body>
</html>
"""


LABELER_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Market Level Labeler</title>
  <script src="/plotly.min.js"></script>
  <style>
    :root {
      --bg:#111318; --surface:#171a20; --surface-2:#20242c; --surface-3:#262b34;
      --text:#e7e3d8; --muted:#9b9a93; --faint:#73777f; --line:#2a2e36;
      --accent:#8aa0d8; --accent-soft:#222b45; --green:#7dbb91; --orange:#c99a62;
      --red:#d18495; --purple:#a597d6; --shadow:rgba(0,0,0,.22);
    }
    * { box-sizing: border-box; }
    * { scrollbar-width: thin; scrollbar-color: #555d6b #15171c; }
    *::-webkit-scrollbar { width: 9px; height: 9px; }
    *::-webkit-scrollbar-track { background: #15171c; }
    *::-webkit-scrollbar-thumb { background: #555d6b; border-radius: 0; }
    *::-webkit-scrollbar-thumb:hover { background: #687181; }
    body {
      margin: 0; font-family: Inter, "IBM Plex Sans", Segoe UI, Arial, sans-serif;
      background: var(--bg); color: var(--text);
    }
    #top {
      height: 62px; display: grid; grid-template-columns: auto 1fr auto; gap: 16px; align-items: center; padding: 9px 14px;
      background: rgba(17,19,24,.92); backdrop-filter: blur(16px);
      border-bottom: 1px solid rgba(42,46,54,.95);
      position: sticky; top: 0; z-index: 2;
    }
    .brand {
      display: flex; align-items: center; gap: 10px; letter-spacing: -.015em;
      font-size: 15px; color: var(--text); font-weight: 760;
    }
    .actions { display: flex; gap: 6px; align-items: center; justify-content: flex-end; }
    .action-group { display:flex; gap:4px; align-items:center; }
    .action-spacer { width:16px; flex:0 0 16px; }
    .tool-spacer { flex:1 1 10px; min-width:10px; }
    .center { min-width: 0; display: flex; align-items: center; gap: 9px; }
    button, select, input, textarea {
      background: var(--surface); color: var(--text);
      border: 0; border-radius: 7px; padding: 8px 10px; outline: none;
      box-shadow: inset 0 0 0 1px rgba(42,46,54,.95);
    }
    select { padding-inline-end: 28px; }
    button { font-weight: 720; cursor: pointer; }
    button.icon { width:32px; height:32px; padding:0; display:grid; place-items:center; color:var(--text); }
    button.icon svg { width:16px; height:16px; stroke:currentColor; fill:none; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
    button.text-action { height:32px; padding:0 10px; font-size:12px; }
    button.tool.active { background: var(--accent-soft); color: var(--accent); box-shadow: inset 0 0 0 1px rgba(138,160,216,.50); }
    button.tool.pending { background: rgba(201,154,98,.14); color: var(--orange); box-shadow: inset 0 0 0 1px rgba(201,154,98,.40); }
    button:hover { cursor: pointer; background: var(--surface-2); }
    button:disabled { opacity:.38; cursor:default; }
    button:disabled:hover { background: var(--surface); }
    button.ghost { color: var(--muted); }
    button.primary { background: var(--accent); color: #101217; box-shadow: none; }
    button.primary:hover { background: #9aaddf; }
    button.danger { background: #251922; color: var(--red); box-shadow: inset 0 0 0 1px rgba(209,132,149,.20); }
    #wrap { display: grid; grid-template-columns: 370px minmax(0, 1fr); height: calc(100vh - 62px); }
    #side { overflow: auto; background: rgba(17,19,24,.60); padding: 12px; }
    #chartwrap { position: relative; height: calc(100vh - 62px); min-width: 0; overflow:hidden; }
    #chart { position:absolute; inset:0; cursor: grab; }
    #chart:active { cursor: grabbing; }
    #ovl { position:absolute; inset:0; width:100%; height:100%; pointer-events:none; z-index:3; touch-action:none; }
    body.drawing #ovl { pointer-events:all; }
    #yzone { position:absolute; z-index:4; cursor: ns-resize; }
    #xzone { position:absolute; z-index:4; cursor: ew-resize; }
    body.drawing #yzone, body.drawing #xzone { pointer-events:none; }
    #keysFab {
      position:absolute; left:10px; top:8px; z-index:5; width:26px; height:26px;
      display:grid; place-items:center; border-radius:7px;
      background:rgba(32,36,44,.85); color:var(--faint); cursor:default; user-select:none;
    }
    #keysFab > svg { width:15px; height:15px; stroke:currentColor; fill:none; stroke-width:1; stroke-linecap:round; stroke-linejoin:round; }
    #keysFab:hover { color:var(--text); }
    #keysPop {
      display:none; position:absolute; left:0; top:32px; width:460px;
      background:var(--surface); border-radius:10px; padding:12px 14px;
      box-shadow:0 18px 44px rgba(0,0,0,.45), inset 0 0 0 1px rgba(42,46,54,.95);
    }
    #keysFab:hover #keysPop { display:block; }
    .title { font-size: 15px; font-weight: 720; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 120px; letter-spacing:-.01em; }
    .pill { color: var(--muted); border-radius: 7px; padding: 5px 9px; font-size: 12px; background: var(--surface-2); }
    .progress { color: var(--accent); background: var(--accent-soft); }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .field { margin-bottom: 10px; }
    .field label { display:block; font-size: 11px; color: var(--muted); margin-bottom: 5px; text-transform: uppercase; letter-spacing: .045em; }
    .field input, .field select { width: 100%; }
    textarea { width: 100%; height: 62px; resize: vertical; }
    .hint { color: var(--muted); font-size: 12px; line-height: 1.35; margin-bottom: 12px; }
    .panel {
      border-radius: 10px; background: var(--surface);
      padding: 14px; margin-bottom: 10px; box-shadow: none;
    }
    .panel-title { font-size: 11px; color: var(--accent); text-transform: uppercase; letter-spacing: .075em; margin-bottom: 10px; font-weight: 760; }
    .metricbar { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:8px; }
    .metric { border-radius: 6px; padding: 4px 8px; color: var(--muted); font-size: 11px; background:var(--surface-2); }
    .metric b { color: var(--text); }
    .row {
      padding: 10px 11px; margin-bottom: 6px; border-radius: 7px;
      background: var(--surface);
    }
    .row.active { background: var(--accent-soft); box-shadow: inset 2px 0 0 var(--accent); }
    .row.labeled { opacity: .56; }
    .row-head { display:flex; justify-content:space-between; gap:8px; font-weight:760; }
    .row-badges { display:flex; gap:8px; margin-top:5px; flex-wrap:wrap; }
    .badge { font-size:10px; color:var(--faint); }
    .badge.hot { color: var(--orange); }
    .small { font-size: 12px; color: var(--muted); }
    .quiet-help { font-size:10px; color:var(--faint); line-height:1.35; }
    .tool-row { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:10px; align-items:center; }
    .field-group { margin-bottom:10px; }
    .field-group-label { font-size:10px; color:var(--faint); text-transform:uppercase; letter-spacing:.06em; font-weight:760; margin-bottom:6px; }
    .setup-tabs { display:flex; gap:5px; flex-wrap:wrap; margin-bottom:10px; align-items:center; }
    .setup-tab { display:flex; align-items:center; gap:6px; height:28px; padding:0 8px; border-radius:7px; font-size:12px; font-weight:720; color:var(--muted); background:var(--surface-2); box-shadow:inset 0 0 0 1px rgba(42,46,54,.95); cursor:pointer; }
    .setup-tab:hover { color:var(--text); }
    .setup-tab.active { color:var(--accent); background:var(--accent-soft); box-shadow:inset 0 0 0 1px rgba(138,160,216,.50); }
    .setup-tab .kill { display:grid; place-items:center; width:15px; height:15px; border-radius:4px; color:var(--faint); font-size:13px; line-height:1; }
    .setup-tab .kill:hover { color:var(--red); background:rgba(209,132,149,.14); }
    .setup-add { height:28px; width:28px; padding:0; display:grid; place-items:center; border-radius:7px; font-size:17px; color:var(--muted); background:var(--surface-2); box-shadow:inset 0 0 0 1px rgba(42,46,54,.95); cursor:pointer; }
    .setup-add:hover { color:var(--accent); }
    .manual-list { display:flex; flex-direction:column; gap:4px; margin-top:8px; }
    .manual-item { display:flex; align-items:center; justify-content:space-between; gap:8px; color:var(--muted); background:var(--surface-2); border-radius:6px; padding:5px 7px; font-size:11px; }
    .manual-item.active { color:var(--text); background:var(--accent-soft); }
    .manual-item button { width:22px; height:22px; padding:0; box-shadow:none; background:transparent; color:var(--faint); }
    .manual-item .auto-tag { font-size:9px; color:var(--faint); text-transform:uppercase; letter-spacing:.05em; }
    .event-controls { display:flex; gap:8px; align-items:center; margin-bottom:10px; }
    .hover-zone { color:var(--faint); background:var(--surface-2); border-radius:7px; padding:7px 9px; font-size:11px; user-select:none; }
    .hover-zone.active { color:var(--text); background:var(--accent-soft); }
    .native-select-hidden { display:none !important; }
    .select-ui { position:relative; min-width:112px; }
    .select-button { width:100%; height:32px; display:flex; align-items:center; justify-content:space-between; gap:10px; padding:0 9px; background:var(--surface-2); color:var(--text); border-radius:7px; box-shadow:inset 0 0 0 1px rgba(42,46,54,.95); font-size:12px; }
    .select-button::after { content:"⌄"; color:var(--faint); font-size:12px; }
    .select-menu { position:absolute; left:0; right:0; top:36px; z-index:6; display:none; background:var(--surface); border-radius:8px; box-shadow:0 16px 36px rgba(0,0,0,.32), inset 0 0 0 1px rgba(42,46,54,.95); padding:4px; max-height:220px; overflow:auto; }
    .select-ui.open .select-menu { display:block; }
    .select-option { padding:7px 8px; border-radius:6px; font-size:12px; color:var(--muted); }
    .select-option:hover { background:var(--surface-2); color:var(--text); }
    .select-option.selected { color:var(--accent); background:var(--accent-soft); }
    .workbench-panel { display:none; }
    body.results-mode .workbench-panel { display:block; }
    body.results-mode #list, body.results-mode .search-panel { display:none; }
    body.results-mode #setupsHeading, body.results-mode #setupTabs { display:none; }
    .status-box { border-radius:8px; background:var(--surface-2); padding:9px; color:var(--muted); font-size:12px; line-height:1.45; margin-bottom:10px; }
    .status-box b { color:var(--text); }
    .slice-grid { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin:8px 0 10px; }
    .slice-card { background:var(--surface-2); border-radius:7px; padding:7px; font-size:11px; color:var(--muted); }
    .slice-card b { display:block; color:var(--text); margin-bottom:3px; }
    .trade-filters { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin-bottom:8px; }
    .trade-list { display:flex; flex-direction:column; gap:5px; max-height:320px; overflow:auto; }
    .trade-row { border-radius:7px; padding:8px; background:var(--surface-2); color:var(--muted); font-size:11px; }
    .trade-row.active { background:var(--accent-soft); color:var(--text); box-shadow: inset 2px 0 0 var(--accent); }
    .trade-row.win { box-shadow: inset 2px 0 0 var(--green); }
    .trade-row.loss { box-shadow: inset 2px 0 0 var(--red); }
    .trade-main { display:flex; justify-content:space-between; gap:8px; font-weight:760; color:var(--text); }
    .trade-tags { margin-top:4px; display:flex; gap:6px; flex-wrap:wrap; }
    .trade-tag { color:var(--faint); }
    .keys-panel { display:grid; grid-template-columns:1fr 1fr; gap:4px 14px; margin-top:2px; }
    .keys-panel .k-row { display:flex; align-items:baseline; gap:8px; font-size:11px; color:var(--muted); }
    .keys-panel .k { display:inline-block; min-width:22px; padding:1px 6px; border-radius:4px; background:var(--surface-2); color:var(--text); font-family: "IBM Plex Mono","JetBrains Mono",Consolas,monospace; font-size:10px; text-align:center; box-shadow: inset 0 0 0 1px rgba(42,46,54,.95); }
    .keys-heading { font-size:10px; color:var(--faint); text-transform:uppercase; letter-spacing:.075em; margin:8px 0 4px; }
    #toast { position: fixed; right: 16px; bottom: 16px; background: #17241d; color: var(--green); padding: 10px 14px; border-radius: 8px; display:none; box-shadow: 0 12px 34px var(--shadow); z-index:9; }
    @media (max-width: 900px) {
      #top { grid-template-columns: 1fr auto; height: auto; }
      .center { grid-column: 1 / -1; order: 3; }
      #wrap { grid-template-columns: 360px minmax(360px, 1fr); }
    }
  </style>
</head>
<body>
  <div id="top">
    <div class="brand">Level desk</div>
    <div class="center">
      <div id="title" class="title"></div>
      <span id="progress" class="pill progress"></span>
    </div>
    <div class="actions">
      <div class="action-group">
        <button class="ghost icon" onclick="prevEvent()" title="Previous candidate  Left">
          <svg viewBox="0 0 20 20"><path d="M12.5 4L6.5 10l6 6"/></svg>
        </button>
        <button class="ghost icon" onclick="nextEvent()" title="Next candidate  Right">
          <svg viewBox="0 0 20 20"><path d="M7.5 4l6 6-6 6"/></svg>
        </button>
        <button class="ghost icon" onclick="nextUnlabeled()" title="Next unlabeled  U">
          <svg viewBox="0 0 20 20"><path d="M10 3l7 7-7 7-7-7z"/></svg>
        </button>
        <span class="action-spacer"></span>
        <button class="ghost text-action" id="unlabelBtn" onclick="unlabelEvent()" disabled title="Make this event unlabeled again">Unlabel</button>
        <button class="primary text-action" onclick="saveLabel()" title="Save this event annotation  S">Save</button>
        <span class="action-spacer"></span>
        <button class="ghost text-action" onclick="showLabeling()">Labeling</button>
        <button class="ghost text-action" onclick="showResults()">Results</button>
        <button class="primary text-action" id="runIterationBtn" onclick="startIteration()">Run</button>
      </div>
    </div>
  </div>
  <div id="wrap">
    <div id="side">
      <div class="panel">
        <div class="panel-title">Event tape</div>
        <div class="event-controls">
          <input id="jump" type="number" min="1" style="display:none" onkeydown="jumpKey(event)">
          <select id="tfSelect" onchange="changeTf()"></select>
          <div id="defaultLinesHover" class="hover-zone">auto lines</div>
        </div>
        <div id="metricbar" class="metricbar"></div>
        <div class="quiet-help">
          Wheel = zoom X at cursor · Shift = Y · Ctrl = both · LMB-drag an axis to scale it · double-click axis to auto-fit.
        </div>
      </div>
      <div class="panel">
        <div class="panel-title">Annotation</div>
        <div id="setupsHeading" class="field-group-label">setups <span class="quiet-help" style="text-transform:none;letter-spacing:0">— one event can carry several trades</span></div>
        <div id="setupTabs" class="setup-tabs"></div>
        <div class="tool-row">
          <button class="ghost icon tool" id="toolLevel" onclick="toggleTool('level')" title="Level: one click snaps start to candle high; end is the first later candle that closes above the level, or chart end.  L">
            <svg viewBox="0 0 20 20"><circle cx="4" cy="10" r="1.4" fill="currentColor" stroke="none"/><path d="M6 10h11"/></svg>
          </button>
          <button class="ghost icon tool" id="toolPump" onclick="toggleTool('pump')" title="Pump: click start candle (snaps to low), click culmination candle (snaps to high).  P">
            <svg viewBox="0 0 20 20"><path d="M4 16L16 4"/><path d="M4 16v-5"/><path d="M16 4h-5"/><rect x="4" y="4" width="12" height="12" stroke-dasharray="2 2" opacity=".45"/></svg>
          </button>
          <button class="ghost icon tool" id="toolZigzag" onclick="toggleTool('zigzag')" title="Swing zigzag: left-click to drop points (snap to nearest high/low), right-click to finish.  G">
            <svg viewBox="0 0 20 20"><path d="M2 14l4-8 4 6 4-8 4 6"/></svg>
          </button>
          <button class="ghost icon tool" id="toolExit" onclick="toggleTool('exit')" disabled title="Draw a level first, then mark the possible exit point.  E">
            <svg viewBox="0 0 20 20"><path d="M5 5l10 10"/><path d="M15 5L5 15"/></svg>
          </button>
          <button class="text-action" id="slBtn" onclick="setOptimalSl()" disabled title="Auto stop-loss: low of the entry candle, ray cut at first touch  X">Auto SL</button>
          <span class="tool-spacer"></span>
          <button class="danger icon" id="clearDrawingsBtn" onclick="resetAnnotations()" disabled title="Clear level, pump, stop, exit and zigzag for this setup.  Z">
            <svg viewBox="0 0 20 20"><path d="M5 6h10"/><path d="M8 6V4h4v2"/><path d="M7 8l.5 8h5L13 8"/><path d="M9.2 10v4M10.8 10v4"/></svg>
          </button>
        </div>
        <div class="quiet-help" style="margin-bottom:10px">
          Level is a one-click high-wick anchor; its segment ends at the first later candle that closes above it, or at chart end if none does.
        </div>
        <div class="field-group">
          <div class="field-group-label">event assessment</div>
          <div class="grid">
            <div class="field"><label>family</label><select id="family" onchange="onSetupFieldChange()"><option value="cap">cap</option><option value="breakout">breakout</option><option value="structure_break">structure break</option><option value="unknown">unknown</option></select></div>
            <div class="field"><label>quality</label><select id="quality" onchange="onSetupFieldChange()"><option value="good">good</option><option value="ok">ok</option><option value="bad">bad</option></select></div>
          </div>
        </div>
        <div class="field-group">
          <div class="field-group-label">chart</div>
          <div class="grid">
            <div class="field"><label>price snap</label><select id="snapMode"><option value="wick">wick</option><option value="ohlc">OHLC</option><option value="off">off</option></select></div>
            <div class="field"><label>show only</label><select id="filter" onchange="renderList()"><option value="all">all</option><option value="unlabeled">unlabeled</option><option value="labeled">labeled</option></select></div>
          </div>
        </div>
        <div class="field"><label>notes</label><textarea id="notes" placeholder="why valid / what you see; missing pump/level is inferred from drawings" oninput="onSetupFieldChange()"></textarea></div>
        <div class="field"><label>latest saved comment</label><textarea id="savedNotes" readonly></textarea></div>
        <div class="hint" id="objectsText"></div>
      </div>
      <div class="panel workbench-panel">
        <div class="panel-title">Run / Results</div>
        <div class="status-box" id="iterationStatus">No run loaded.</div>
        <div class="slice-grid" id="resultSlices"></div>
        <div class="trade-filters">
          <select id="filterTf" onchange="renderTrades()"><option value="">TF</option></select>
          <select id="filterSetup" onchange="renderTrades()"><option value="">setup</option></select>
          <select id="filterOutcome" onchange="renderTrades()"><option value="">outcome</option></select>
          <select id="filterSession" onchange="renderTrades()"><option value="">session</option></select>
          <select id="filterMonth" onchange="renderTrades()"><option value="">month</option></select>
          <select id="filterWeek" onchange="renderTrades()"><option value="">week</option></select>
        </div>
        <div class="trade-list" id="tradeList"></div>
        <div class="field" style="margin-top:10px"><label>trade comment</label><textarea id="resultComment" placeholder="comment on selected result trade"></textarea></div>
        <button class="primary text-action" onclick="saveResultAnnotation()" id="saveResultAnnotationBtn" disabled>Save trade note/drawings</button>
      </div>
      <div class="panel search-panel">
        <div class="panel-title">Find candidate</div>
        <div class="field" style="margin-bottom:0"><input id="search" placeholder="symbol / TF, e.g. AVNT 15m" oninput="renderList()"></div>
      </div>
      <div id="list"></div>
    </div>
    <div id="chartwrap">
      <div id="chart"></div>
      <svg id="ovl"></svg>
      <div id="keysFab">
        <svg viewBox="0 0 20 20"><rect x="2" y="6" width="16" height="9" rx="1.5"/><path d="M5 9h1M8.5 9h1M12 9h1M15 9h0.01"/><path d="M6 12h8"/></svg>
        <div id="keysPop">
          <div class="keys-heading">Drawing</div>
          <div class="keys-panel">
            <div class="k-row"><span class="k">L</span><span>level segment (1 click)</span></div>
            <div class="k-row"><span class="k">P</span><span>pump rectangle (2 clicks)</span></div>
            <div class="k-row"><span class="k">G</span><span>swing zigzag (RMB ends)</span></div>
            <div class="k-row"><span class="k">E</span><span>exit point</span></div>
            <div class="k-row"><span class="k">X</span><span>auto stop-loss</span></div>
            <div class="k-row"><span class="k">Del</span><span>remove selected</span></div>
            <div class="k-row"><span class="k">Esc</span><span>cancel tool</span></div>
            <div class="k-row"><span class="k">Z</span><span>reset annotations</span></div>
          </div>
          <div class="keys-heading">Chart</div>
          <div class="keys-panel">
            <div class="k-row"><span class="k">Drag</span><span>pan chart</span></div>
            <div class="k-row"><span class="k">Wheel</span><span>zoom X at cursor</span></div>
            <div class="k-row"><span class="k">Shift+Wh</span><span>zoom Y at cursor</span></div>
            <div class="k-row"><span class="k">^+Wh</span><span>zoom both</span></div>
            <div class="k-row"><span class="k">Axis</span><span>LMB-drag to scale</span></div>
            <div class="k-row"><span class="k">2x ax</span><span>auto-fit axis</span></div>
          </div>
          <div class="keys-heading">Navigation</div>
          <div class="keys-panel">
            <div class="k-row"><span class="k">Left</span><span>previous event</span></div>
            <div class="k-row"><span class="k">Right</span><span>next event</span></div>
            <div class="k-row"><span class="k">U</span><span>next unlabeled</span></div>
            <div class="k-row"><span class="k">[ ]</span><span>cycle timeframe</span></div>
            <div class="k-row"><span class="k">S</span><span>save event annotation</span></div>
          </div>
        </div>
      </div>
      <div id="yzone" title="Drag to scale price · double-click to auto-fit"></div>
      <div id="xzone" title="Drag to scale time · double-click to fit all"></div>
    </div>
  </div>
  <div id="toast"></div>
<script>
let candidates = [], visible = [], idx = 0, current = null, selectedTf = null;
let xRange = null, yRange = null, yAuto = true, barMs = 60000;
let tool = null, drawStep = 0, pending = null;
let level = null;       // {price, start_ms, end_ms(auto body-cross or chart end), broken}
let pump = null;        // {start:{idx,ms,price}, high:{idx,ms,price}}
let entry = null;       // auto: {idx,ms,price}
let sl = null;          // {price, hit_ms|null, end_ms}
let exitPoint = null;   // {ms, price}
let zigzag = null;      // {points:[{ms,price}]} - swing zigzag of the active setup
let zzDraft = null;     // in-progress zigzag being drawn (before RMB finishes)
let setups = [];        // per-event list of independent setups (see emptySetup)
let activeSetup = 0;    // index of the setup currently shown in the workspace
let selectedObj = null;
let showDefaultLines = false;
let relayoutGuard = false;
let chartHandlersAttached = false;
let resultMode = false;
let resultTrade = null;
let resultTrades = [];
let selectedTradeId = null;
let iterationPoll = null;
let loadToken = 0;
let navigationSerial = 0;
let autosaveTimer = null;
let saveQueue = Promise.resolve();
let savedSignatures = new Map();
const AUTOSAVE_DELAY_MS = 350;

function gd() { return document.getElementById('chart'); }
function iso(ms) { return new Date(ms).toISOString().slice(0,16).replace('T',' '); }
function fmtPct(x) { return Number.isFinite(x) ? (100*x).toFixed(1)+'%' : 'n/a'; }
function fmtPrice(x) {
  const v = Number(x);
  if (!Number.isFinite(v)) return '';
  const a = Math.abs(v);
  const digits = a >= 100 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : a >= 0.0001 ? 6 : 8;
  return v.toFixed(digits).replace(/\.?0+$/, '');
}
function priceTickFormat(values) {
  const nums = values.map(Number).filter(Number.isFinite);
  if (!nums.length) return '.4f';
  const mid = nums[Math.floor(nums.length / 2)] || nums[0];
  const a = Math.abs(mid);
  const digits = a >= 100 ? 2 : a >= 1 ? 3 : a >= 0.01 ? 4 : a >= 0.0001 ? 5 : 6;
  return '.' + digits + 'f';
}
function displaySymbol(symbol) {
  return String(symbol || '').replace(/USDT$/, '').replace(/USDC$/, '');
}
let toastTimer = null;
function toast(msg, ms=1600) {
  const t = document.getElementById('toast');
  t.innerText = msg;
  t.style.display = 'block';
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.style.display = 'none'; toastTimer = null; }, ms);
}
function esc(x) {
  return String(x ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function pms(v) {
  if (v == null) return NaN;
  if (typeof v === 'number') return v;
  if (v instanceof Date) return v.getTime();
  const s = String(v).replace(' ', 'T');
  return Date.parse(s);
}

/* ---------- custom selects ---------- */
function enhanceSelect(select) {
  if (!select || select.closest('[style*="display:none"]')) return;
  let ui = select.nextElementSibling && select.nextElementSibling.classList.contains('select-ui') ? select.nextElementSibling : null;
  if (!ui) {
    ui = document.createElement('div');
    ui.className = 'select-ui';
    ui.innerHTML = '<button type="button" class="select-button"></button><div class="select-menu"></div>';
    select.insertAdjacentElement('afterend', ui);
    select.classList.add('native-select-hidden');
    ui.querySelector('.select-button').addEventListener('click', ev => {
      ev.stopPropagation();
      document.querySelectorAll('.select-ui.open').forEach(x => { if (x !== ui) x.classList.remove('open'); });
      ui.classList.toggle('open');
    });
  }
  const btn = ui.querySelector('.select-button');
  const menu = ui.querySelector('.select-menu');
  btn.textContent = select.options[select.selectedIndex] ? select.options[select.selectedIndex].textContent : '';
  menu.innerHTML = '';
  Array.from(select.options).forEach((opt, i) => {
    const item = document.createElement('div');
    item.className = 'select-option' + (i === select.selectedIndex ? ' selected' : '');
    item.textContent = opt.textContent;
    item.onclick = ev => {
      ev.stopPropagation();
      select.selectedIndex = i;
      select.dispatchEvent(new Event('change'));
      ui.classList.remove('open');
      refreshSelect(select);
    };
    menu.appendChild(item);
  });
}
function refreshSelect(select) { if (select) enhanceSelect(select); }
function enhanceVisibleSelects() { document.querySelectorAll('select').forEach(enhanceSelect); }
document.addEventListener('click', () => document.querySelectorAll('.select-ui.open').forEach(x => x.classList.remove('open')));

/* ---------- data helpers ---------- */
function searchBlob(c) {
  const variants = c.variants || [c];
  return [c.symbol, c.tf, c.event_id, ...variants.flatMap(v => [v.tf, v.event_id])].join(' ').toLowerCase();
}
function computeBarMs() {
  const ts = current.candles.timestamp;
  if (ts.length < 2) { barMs = 60000; return; }
  const diffs = [];
  for (let i=1; i<Math.min(ts.length, 60); i++) diffs.push(ts[i]-ts[i-1]);
  diffs.sort((a,b)=>a-b);
  barMs = diffs[Math.floor(diffs.length/2)] || 60000;
}
function nearestCandle(ms) {
  const ts = current.candles.timestamp;
  let best = 0, bd = Infinity;
  for (let i=0; i<ts.length; i++) {
    const d = Math.abs(ts[i] - ms);
    if (d <= bd) { bd = d; best = i; }
  }
  return best;
}
function snapPrice(ms, y) {
  const mode = document.getElementById('snapMode').value;
  if (mode === 'off') return y;
  const c = current.candles, i = nearestCandle(ms);
  const vals = mode === 'wick' ? [c.high[i], c.low[i]] : [c.open[i], c.high[i], c.low[i], c.close[i]];
  let price = vals[0], bd = Math.abs(vals[0] - y);
  for (const v of vals) {
    const d = Math.abs(v - y);
    if (d < bd) { bd = d; price = v; }
  }
  return price;
}
function levelHighAnchor(ms) {
  const c = current.candles, i = nearestCandle(ms);
  return {idx: i, ms: c.timestamp[i], price: c.high[i]};
}
function pumpHighBetweenForward(aIdx, bIdx) {
  const c = current.candles;
  const a = Math.max(0, aIdx);
  const b = Math.min(c.timestamp.length - 1, bIdx);
  let best = a;
  for (let i=a; i<=b; i++) if (c.high[i] >= c.high[best]) best = i;
  return {idx:best, ms:c.timestamp[best], price:c.high[best]};
}
function levelShapeStartMs(lvl) {
  return lvl.start_ms;
}
function chartDataEndMs() {
  const c = current && current.candles;
  return c && c.timestamp.length ? c.timestamp[c.timestamp.length-1] + barMs : Date.now();
}
function levelShapeEndMs(lvl) {
  if (!lvl) return chartDataEndMs();
  return Number.isFinite(Number(lvl.end_ms)) ? Number(lvl.end_ms) : levelAutoEndMs(lvl.price, lvl.start_ms);
}
function levelAutoEndMs(price, startMs) {
  if (!current) return startMs + barMs;
  const c = current.candles;
  const startIdx = nearestCandle(startMs);
  // The level (resistance) is valid until a later candle closes above it: that
  // breakout candle is where the segment is cut, matching the entry definition.
  for (let i=startIdx + 1; i<c.timestamp.length; i++) {
    if (c.close[i] > price) return c.timestamp[i];
  }
  return chartDataEndMs();
}
function makeLevelFromAnchor(anchor) {
  return {
    price: anchor.price,
    start_ms: anchor.ms,
    end_ms: levelAutoEndMs(anchor.price, anchor.ms),
    broken: false
  };
}
function pumpPreviewState(toIdx) {
  if (!pending) return null;
  if (toIdx <= pending.idx) {
    return {valid:false, reason:'right-to-left', start:pending, high:null, endIdx:toIdx};
  }
  const hi = pumpHighBetweenForward(pending.idx, toIdx);
  if (hi.price <= pending.price) {
    return {valid:false, reason:'top-down', start:pending, high:hi, endIdx:toIdx};
  }
  return {valid:true, reason:null, start:pending, high:hi, endIdx:toIdx};
}

/* ---------- derived objects: level end + entry + stop ---------- */
function levelBreakIdx(price, startMs, afterMs=null) {
  const c = current.candles;
  const fromMs = Number.isFinite(Number(afterMs)) ? Number(afterMs) : startMs;
  for (let i=0; i<c.timestamp.length; i++) {
    if (c.timestamp[i] < fromMs) continue;
    if (c.close[i] > price) return i;
  }
  return null;
}
function entryForLevel(lvl) {
  if (!lvl || !current) return null;
  const c = current.candles;
  const i = levelBreakIdx(lvl.price, lvl.start_ms, levelShapeEndMs(lvl));
  return i != null ? {idx:i, ms:c.timestamp[i], price:c.close[i]} : null;
}
function slRayFor(entryObj, price) {
  if (entryObj == null || price == null || !current) return null;
  const c = current.candles;
  const out = {price:Number(price), hit_ms:null, end_ms:c.timestamp[c.timestamp.length-1] + barMs};
  for (let j=entryObj.idx+1; j<c.timestamp.length; j++) {
    if (c.low[j] <= out.price) { out.hit_ms = c.timestamp[j]; out.end_ms = c.timestamp[j] + barMs; break; }
  }
  return out;
}
function computeEntry() {
  const sourceLevel = activeEntryLevel();
  entry = entryForLevel(sourceLevel);
  if (level) level.broken = !!entry;
  if (!entry) sl = null;
  else if (sl) recomputeSlRay();
  updateUiButtons();
  syncToolButtons();
}
function recomputeSlRay() {
  if (!sl || !entry) { return; }
  const ray = slRayFor(entry, sl.price);
  if (ray) { sl.hit_ms = ray.hit_ms; sl.end_ms = ray.end_ms; }
}
// Last local swing low / high of a zigzag (protective stop / break level).
function zigzagExtremeFrom(z, kind) {
  const pts = z && Array.isArray(z.points) ? [...z.points].sort((a,b) => a.ms - b.ms) : [];
  if (pts.length < 2) return null;
  let last = null;
  for (let i=0; i<pts.length; i++) {
    const prev = i > 0 ? pts[i-1] : null;
    const next = i < pts.length - 1 ? pts[i+1] : null;
    // Manual zigzag points are the swing points themselves, so endpoints count.
    // In a downtrend sequence H1-L1-H2-L2, the protected stop must be L2, not L1.
    const isLow = !!(prev || next) && (!prev || pts[i].price < prev.price) && (!next || pts[i].price < next.price);
    const isHigh = !!(prev || next) && (!prev || pts[i].price > prev.price) && (!next || pts[i].price > next.price);
    if (kind === 'low' ? isLow : isHigh) last = pts[i];
  }
  if (last) return last;
  return pts.reduce((best, p) =>
    (best == null || (kind === 'low' ? p.price < best.price : p.price > best.price)) ? p : best, null);
}
function zigzagExtreme(kind) { return zigzagExtremeFrom(zigzag, kind); }
function zigzagLastSwingLow() { return zigzagExtreme('low'); }
function zigzagLastSwingHigh() { return zigzagExtreme('high'); }
function setupHasPump(s) { return !!(s && s.pump && s.pump.start && s.pump.high); }
function structureBreakAnchorFrom(z) {
  const high = zigzagExtremeFrom(z, 'high');
  if (!high) return null;
  return {price:high.price, start_ms:high.ms, end_ms:levelAutoEndMs(high.price, high.ms), broken:false, virtual:true};
}
function activeEntryLevel() {
  const family = document.getElementById('family').value;
  if (level) return level;
  if (family === 'structure_break') return structureBreakAnchorFrom(zigzag);
  return null;
}
function setOptimalSl() {
  if (!current) return;
  captureVisibleRanges();
  const family = document.getElementById('family').value;
  let msg;
  if (family === 'structure_break') {
    const low = zigzagLastSwingLow();
    const high = zigzagLastSwingHigh();
    if (!low || !high) { toast('draw the swing zigzag first (G) for the structure break'); return; }
    computeEntry();
    if (!entry) { toast('price has not broken above the last swing high yet'); return; }
    sl = {price: low.price};
    msg = 'stop behind last swing low; entry = break of last swing high';
  } else {
    if (!entry) { toast('draw a level first - entry appears on close above it'); return; }
    sl = {price: current.candles.low[entry.idx]};
    msg = 'stop-loss at entry-candle low';
  }
  recomputeSlRay();
  selectedObj = 'sl';
  commitActiveSetup();
  renderObjects();
  redrawStable(false);
  scheduleAutosave();
  toast(msg);
}
function updateSlButton() {
  const family = document.getElementById('family').value;
  const high = family === 'structure_break' ? zigzagLastSwingHigh() : null;
  const low = family === 'structure_break' ? zigzagLastSwingLow() : null;
  const zzOk = !!(high && low);
  document.getElementById('slBtn').disabled = !(entry || zzOk);
}
function hasDrawings() {
  return !!(level || pump || sl || exitPoint || (zigzag && zigzag.points && zigzag.points.length));
}
function updateDrawingButton() {
  const btn = document.getElementById('clearDrawingsBtn');
  if (btn) btn.disabled = !hasDrawings();
}
function updateEventButton() {
  const btn = document.getElementById('unlabelBtn');
  if (!btn) return;
  const group = visible.length ? visible[idx] : null;
  btn.disabled = !(group && group.labeled);
}
function updateUiButtons() {
  updateSlButton();
  updateDrawingButton();
  updateEventButton();
}

/* ---------- tools ---------- */
function toggleTool(name) {
  if (name === 'exit' && !level) { toast('draw a level first - exit is tied to an existing level'); return; }
  const turningOn = tool !== name;
  tool = turningOn ? name : null;
  drawStep = 0; pending = null;
  zzDraft = (name === 'zigzag' && turningOn) ? {points: []} : null;
  clearGhost();
  syncToolButtons();
  if (tool === 'level') toast('level: one click on the start high');
  if (tool === 'pump') toast('pump: click the start candle, then the culmination');
  if (tool === 'zigzag') toast('zigzag: left-click swing points, right-click to finish');
  if (tool === 'exit') toast('exit: click the exit point');
}
function cancelTool() {
  captureVisibleRanges();
  tool = null; drawStep = 0; pending = null; zzDraft = null;
  selectedObj = null;
  clearGhost();
  syncToolButtons();
  renderObjects();
  redrawStable(true);
}
function syncToolButtons() {
  const map = {level:'toolLevel', pump:'toolPump', zigzag:'toolZigzag', exit:'toolExit'};
  if (!level && tool === 'exit') { tool = null; drawStep = 0; pending = null; clearGhost(); }
  for (const [name, id] of Object.entries(map)) {
    const btn = document.getElementById(id);
    const disabled = name === 'exit' && !level;
    btn.disabled = disabled;
    btn.classList.toggle('active', !disabled && tool === name);
    const pending = drawStep === 1 || (name === 'zigzag' && zzDraft && zzDraft.points.length > 0);
    btn.classList.toggle('pending', !disabled && tool === name && pending);
  }
  document.body.classList.toggle('drawing', tool !== null);
}

/* ---------- coordinate transforms ---------- */
function plotRefs() {
  const fl = gd()._fullLayout;
  return fl && fl.xaxis && fl.yaxis ? {xa: fl.xaxis, ya: fl.yaxis, fl} : null;
}
/* Plotly maps our epoch-ms timestamps into an internal "linear" axis space via
   d2c(); that map is affine but NOT the identity, so the overlay must go through
   Plotly's own axis functions rather than parse range strings itself. This keeps
   the overlay pixel-locked to the candles no matter how the visible range is
   stored (local string / ISO / number). */
function xMsToLinear(xa, ms) { return xa.d2c(new Date(ms)); }
function xLinearToMs(xa, lin) {
  const c = current && current.candles ? current.candles : null;
  if (!c || c.timestamp.length < 2) return lin - xa.d2c(new Date(0));
  const t0 = c.timestamp[0], t1 = c.timestamp[c.timestamp.length - 1];
  const l0 = xa.d2c(new Date(t0)), l1 = xa.d2c(new Date(t1));
  return l1 === l0 ? t0 : t0 + (lin - l0) * (t1 - t0) / (l1 - l0);
}
function xToPx(ms) {
  const r = plotRefs(); if (!r) return 0;
  return r.xa.l2p(xMsToLinear(r.xa, ms)) + r.xa._offset;
}
function yToPx(p) {
  const r = plotRefs(); if (!r) return 0;
  return r.ya.l2p(r.ya.d2c(p)) + r.ya._offset;
}
function captureVisibleRanges() {
  const r = plotRefs();
  if (!r || !r.xa || !r.ya || !r.xa.range || !r.ya.range) return;
  // Store Plotly's range values verbatim. Re-serialising them (e.g. to ISO/UTC)
  // shifts the view by the timezone offset when re-applied, so we never do that.
  xRange = [r.xa.range[0], r.xa.range[1]];
  const ya0 = Number(r.ya.range[0]), ya1 = Number(r.ya.range[1]);
  if (Number.isFinite(ya0) && Number.isFinite(ya1) && ya1 > ya0) {
    yRange = [ya0, ya1];
  }
}
function relayoutDrawingsOnly() {
  const update = {shapes: shapes(), annotations: annotations()};
  if (xRange) update['xaxis.range'] = xRange;
  if (yRange) update['yaxis.range'] = yRange;
  guardedRelayout(update);
  positionZones();
}
function redrawStable(full=false) {
  captureVisibleRanges();
  if (full) draw();
  else relayoutDrawingsOnly();
}
function eventDataPoint(ev) {
  const r = plotRefs(); if (!r) return null;
  const bb = gd().getBoundingClientRect();
  const px = ev.clientX - bb.left, py = ev.clientY - bb.top;
  const inX = px >= r.xa._offset && px <= r.xa._offset + r.xa._length;
  const inY = py >= r.ya._offset && py <= r.ya._offset + r.ya._length;
  return {
    ms: xLinearToMs(r.xa, r.xa.p2l(px - r.xa._offset)),
    price: r.ya.l2c(r.ya.p2l(py - r.ya._offset)),
    inPrice: inX && inY, px, py
  };
}
function hitObject(ev) {
  if (!current || tool) return null;
  const pt = eventDataPoint(ev);
  if (!pt || !pt.inPrice) return null;
  const threshold = 9;
  function near(x,y) { return Math.hypot(pt.px - x, pt.py - y) <= threshold; }
  // Level and pump are intentionally not grabbable: once placed they can only be
  // deleted (object list / reset), never dragged. A press on them pans the chart.
  if (exitPoint && near(xToPx(exitPoint.ms), yToPx(exitPoint.price))) return {id:'exit', part:'exit'};
  if (sl && entry && Math.abs(pt.py - yToPx(sl.price)) <= threshold && pt.px >= xToPx(entry.ms)-threshold && pt.px <= xToPx(sl.end_ms)+threshold) return {id:'sl', part:'sl'};
  return null;
}
let dragObj = null;
function applyDrag(hit, ev) {
  const pt = eventDataPoint(ev);
  if (!pt || !pt.inPrice) return;
  const c = current.candles;
  selectedObj = hit.id;
  let needsFullDraw = false;
  if (hit.part === 'exit' && exitPoint) {
    const i = nearestCandle(pt.ms);
    exitPoint = {ms:c.timestamp[i], price:snapPrice(pt.ms, pt.price)};
  } else if (hit.part === 'sl' && sl) {
    sl.price = Math.max(0, pt.price);
    recomputeSlRay();
  }
  commitActiveSetup();
  renderObjects();
  redrawStable(needsFullDraw);
  scheduleAutosave();
}

/* ---------- ghost preview overlay ---------- */
function sizeOverlay() {
  const svg = document.getElementById('ovl');
  const w = gd().clientWidth, h = gd().clientHeight;
  if (svg.getAttribute('width') != w) svg.setAttribute('width', w);
  if (svg.getAttribute('height') != h) svg.setAttribute('height', h);
}
function clearGhost() { document.getElementById('ovl').innerHTML = ''; }
function ghostDot(x, y, color) {
  return `<circle cx="${x}" cy="${y}" r="3.5" fill="${color}" stroke="rgba(17,19,24,.9)" stroke-width="1"/>`;
}
function renderGhost(pt) {
  if (!tool || !current || !pt || !pt.inPrice) { clearGhost(); return; }
  sizeOverlay();
  const r = plotRefs(); if (!r) return;
  const x0 = r.xa._offset, x1 = r.xa._offset + r.xa._length;
  const y0 = r.ya._offset, y1 = r.ya._offset + r.ya._length;
  const c = current.candles;
  let html = '';
  if (tool === 'level') {
    const anchor = levelHighAnchor(pt.ms);
    const endMs = levelAutoEndMs(anchor.price, anchor.ms);
    const ax = xToPx(anchor.ms), bx = xToPx(endMs), gy = yToPx(anchor.price);
    html += `<line x1="${ax}" y1="${gy}" x2="${bx}" y2="${gy}" stroke="#8aa0d8" stroke-width="1"/>`;
    html += `<line x1="${ax}" y1="${y0}" x2="${ax}" y2="${y1}" stroke="rgba(138,160,216,.30)" stroke-width="1"/>`;
    html += ghostDot(ax, gy, '#8aa0d8');
    html += `<text x="${ax+6}" y="${gy-6}" fill="#8aa0d8" font-size="11">level ${fmtPrice(anchor.price)}</text>`;
  }
  if (tool === 'zigzag') {
    const i = nearestCandle(pt.ms);
    const snap = {ms:c.timestamp[i], price:snapPrice(pt.ms, pt.price)};
    const pts = (zzDraft && zzDraft.points) ? zzDraft.points : [];
    const color = '#d9c27a';
    // committed segments of the in-progress zigzag
    for (let k=1; k<pts.length; k++) {
      html += `<line x1="${xToPx(pts[k-1].ms)}" y1="${yToPx(pts[k-1].price)}" x2="${xToPx(pts[k].ms)}" y2="${yToPx(pts[k].price)}" stroke="${color}" stroke-width="1.4"/>`;
    }
    for (const p of pts) html += ghostDot(xToPx(p.ms), yToPx(p.price), color);
    // preview segment from the last point to the cursor
    const gx = xToPx(snap.ms), gy = yToPx(snap.price);
    if (pts.length) {
      const last = pts[pts.length-1];
      html += `<line x1="${xToPx(last.ms)}" y1="${yToPx(last.price)}" x2="${gx}" y2="${gy}" stroke="${color}" stroke-width="1" stroke-dasharray="4 3" opacity=".8"/>`;
    }
    html += `<line x1="${gx}" y1="${y0}" x2="${gx}" y2="${y1}" stroke="rgba(217,194,122,.22)" stroke-width="1"/>`;
    html += ghostDot(gx, gy, color);
    html += `<text x="${gx+8}" y="${gy-8}" fill="${color}" font-size="11">${pts.length ? 'RMB to finish' : 'swing '+fmtPrice(snap.price)}</text>`;
  }
  if (tool === 'pump') {
    const i = nearestCandle(pt.ms);
    if (drawStep === 0) {
      const gx = xToPx(c.timestamp[i]), gy = yToPx(c.low[i]);
      html += `<line x1="${gx}" y1="${y0}" x2="${gx}" y2="${y1}" stroke="rgba(125,187,145,.28)" stroke-width="1"/>`;
      html += ghostDot(gx, gy, '#7dbb91');
      html += `<text x="${gx+8}" y="${gy+12}" fill="#7dbb91" font-size="11">low ${fmtPrice(c.low[i])}</text>`;
    } else {
      const ax = xToPx(pending.ms), ay = yToPx(pending.price);
      const state = pumpPreviewState(i);
      const endMs = c.timestamp[i];
      const endPx = xToPx(endMs);
      const hi = state && state.high ? state.high : {ms:endMs, price:c.high[i]};
      const bx = state && state.valid ? xToPx(hi.ms) : endPx;
      const by = state && state.valid ? yToPx(hi.price) : yToPx(Math.max(pending.price, c.high[i]));
      if (!state || !state.valid) {
        html += `<rect x="${Math.min(ax,endPx)}" y="${Math.min(ay,by)}" width="${Math.abs(endPx-ax)}" height="${Math.abs(by-ay)}" fill="rgba(155,154,147,.12)" stroke="rgba(155,154,147,.65)" stroke-width="1"/>`;
        html += ghostDot(ax, ay, '#9b9a93');
        html += `<text x="${Math.max(ax,endPx)+8}" y="${Math.min(ay,by)+14}" fill="#9b9a93" font-size="11">${state ? state.reason : 'invalid'}</text>`;
      } else {
        html += `<rect x="${Math.min(ax,bx)}" y="${Math.min(ay,by)}" width="${Math.abs(bx-ax)}" height="${Math.abs(by-ay)}" fill="rgba(125,187,145,.10)" stroke="#7dbb91" stroke-width="1"/>`;
        html += ghostDot(ax, ay, '#7dbb91') + ghostDot(bx, by, '#7dbb91');
        const movePct = pending.price > 0 ? (hi.price/pending.price - 1) * 100 : 0;
        html += `<text x="${bx+8}" y="${by-6}" fill="#7dbb91" font-size="11">high ${fmtPrice(hi.price)} (+${movePct.toFixed(1)}%)</text>`;
      }
    }
  }
  if (tool === 'exit') {
    const i = nearestCandle(pt.ms);
    const price = snapPrice(pt.ms, pt.price);
    const gx = xToPx(c.timestamp[i]), gy = yToPx(price);
    html += ghostDot(gx, gy, '#a597d6');
    html += `<text x="${gx+8}" y="${gy+4}" fill="#a597d6" font-size="11">${fmtPrice(price)}</text>`;
  }
  document.getElementById('ovl').innerHTML = html;
}

/* ---------- tool clicks ---------- */
function handleToolClick(pt) {
  const c = current.candles;
  let needsFullDraw = false;
  captureVisibleRanges();
  if (tool === 'level') {
    level = makeLevelFromAnchor(levelHighAnchor(pt.ms));
    selectedObj = 'level';
    finishTool();
    computeEntry();
  } else if (tool === 'zigzag') {
    const i = nearestCandle(pt.ms);
    if (!zzDraft) zzDraft = {points: []};
    const pt2 = {ms: c.timestamp[i], price: snapPrice(pt.ms, pt.price)};
    const last = zzDraft.points[zzDraft.points.length-1];
    if (!last || last.ms !== pt2.ms) zzDraft.points.push(pt2);   // keep drawing until RMB
    syncToolButtons();
    renderGhost(pt);
    return;
  } else if (tool === 'pump') {
    const i = nearestCandle(pt.ms);
    if (drawStep === 0) {
      pending = {idx: i, ms: c.timestamp[i], price: c.low[i]};
      drawStep = 1;
      syncToolButtons();
      toast('pump start fixed - click the culmination candle');
      renderGhost(pt);
      return;
    }
    const state = pumpPreviewState(i);
    if (!state || !state.valid) {
      toast(state ? `invalid pump: ${state.reason}` : 'invalid pump');
      renderGhost(pt);
      return;
    }
    pump = {
      start: {idx: pending.idx, ms: pending.ms, price: pending.price},
      high:  state.high
    };
    selectedObj = 'pump';
    finishTool();
  } else if (tool === 'exit') {
    const i = nearestCandle(pt.ms);
    exitPoint = {ms: c.timestamp[i], price: snapPrice(pt.ms, pt.price)};
    selectedObj = 'exit';
    finishTool();
  }
  commitActiveSetup();
  renderObjects();
  redrawStable(needsFullDraw);
  scheduleAutosave();
}
function finishTool() {
  tool = null; drawStep = 0; pending = null;
  clearGhost();
  syncToolButtons();
}
function finishZigzag() {
  if (!zzDraft) return;
  captureVisibleRanges();
  if (zzDraft.points.length >= 2) {
    zigzag = {points: zzDraft.points.slice().sort((a,b) => a.ms - b.ms)};
    selectedObj = 'zigzag';
    toast('zigzag saved (' + zigzag.points.length + ' swings)');
  } else {
    toast('zigzag needs at least 2 points');
  }
  zzDraft = null;
  finishTool();
  computeEntry();
  commitActiveSetup();
  renderObjects();
  redrawStable(true);
  scheduleAutosave();
}

/* ---------- object list / selection / delete ---------- */
function renderObjects() {
  const rows = [];
  if (level) rows.push({id:'level', del:true, label:`level ${fmtPrice(level.price)} · ${iso(level.start_ms)} -> ${iso(levelShapeEndMs(level))}`});
  if (pump) {
    const move = pump.start.price > 0 ? pump.high.price/pump.start.price - 1 : NaN;
    rows.push({id:'pump', del:true, label:`pump +${fmtPct(move)} · ${fmtPrice(pump.start.price)} -> ${fmtPrice(pump.high.price)}`});
  }
  if (entry) rows.push({id:'entry', del:false, auto:true, label:`entry ${iso(entry.ms)} @ ${fmtPrice(entry.price)}`});
  if (sl && entry) {
    const risk = (entry.price - sl.price) / entry.price;
    const hit = sl.hit_ms ? `hit ${iso(sl.hit_ms)}` : 'not hit';
    rows.push({id:'sl', del:true, label:`stop ${fmtPrice(sl.price)} · risk ${fmtPct(risk)} · ${hit}`});
  }
  if (exitPoint) rows.push({id:'exit', del:true, label:`exit ${iso(exitPoint.ms)} @ ${fmtPrice(exitPoint.price)}`});
  if (zigzag && zigzag.points && zigzag.points.length) {
    rows.push({id:'zigzag', del:true, label:`zigzag · ${zigzag.points.length} swings`});
  }
  const el = document.getElementById('objectsText');
  updateDrawingButton();
  if (!rows.length) { el.innerText = 'No drawings for this setup yet.'; return; }
  el.innerHTML = `<div class="manual-list">${rows.map(row => `
    <div class="manual-item ${selectedObj === row.id ? 'active' : ''}" onclick="selectObj('${row.id}')">
      <span>${esc(row.label)}</span>
      ${row.auto ? '<span class="auto-tag">auto</span>' : ''}
      ${row.del ? `<button type="button" onclick="event.stopPropagation(); deleteObj('${row.id}')" title="Delete">×</button>` : ''}
    </div>
  `).join('')}</div>`;
}
function selectObj(id) {
  captureVisibleRanges();
  selectedObj = id;
  renderObjects();
  redrawStable(id === 'zigzag');
}
function deleteObj(id) {
  captureVisibleRanges();
  if (id === 'level') { level = null; exitPoint = null; computeEntry(); }
  if (id === 'pump') pump = null;
  if (id === 'sl') sl = null;
  if (id === 'exit') exitPoint = null;
  if (id === 'zigzag') { zigzag = null; computeEntry(); }
  if (selectedObj === id) selectedObj = null;
  commitActiveSetup();
  renderObjects();
  redrawStable(id === 'zigzag');
  scheduleAutosave();
}
function deleteSelected() { if (selectedObj && selectedObj !== 'entry') deleteObj(selectedObj); }
function resetAnnotations() {
  captureVisibleRanges();
  level = null;
  pump = null;
  entry = null;
  sl = null;
  exitPoint = null;
  zigzag = null;
  zzDraft = null;
  selectedObj = null;
  tool = null; drawStep = 0; pending = null;
  clearGhost();
  syncToolButtons();
  commitActiveSetup();
  updateUiButtons();
  renderObjects();
  redrawStable(false);
  scheduleAutosave();
  toast('setup drawings cleared');
}

/* ---------- setups: multiple independent trade ideas per event ---------- */
function emptySetup() {
  return {family:'cap', quality:'good', notes:'', level:null, pump:null, exitPoint:null, slPrice:null, zigzag:null};
}
function commitActiveSetup() {
  if (resultMode) return;   // result review shares the drawing globals but has no setups
  const s = setups[activeSetup];
  if (!s) return;
  s.family = document.getElementById('family').value;
  s.quality = document.getElementById('quality').value;
  s.notes = document.getElementById('notes').value;
  s.level = level;
  s.pump = pump;
  s.exitPoint = exitPoint;
  s.zigzag = zigzag;
  s.slPrice = sl ? sl.price : null;
}
function applySetup(i) {
  const s = setups[i] || emptySetup();
  activeSetup = i;
  level = s.level; pump = s.pump; exitPoint = s.exitPoint; zigzag = s.zigzag;
  sl = null; entry = null; zzDraft = null; tool = null; drawStep = 0; pending = null; selectedObj = null;
  const fam = document.getElementById('family'); fam.value = s.family || 'cap'; refreshSelect(fam);
  const qual = document.getElementById('quality'); qual.value = s.quality || 'good'; refreshSelect(qual);
  document.getElementById('notes').value = s.notes || '';
  document.getElementById('savedNotes').value = s.notes || '';
  computeEntry();
  if (s.slPrice != null && entry) { sl = {price:+s.slPrice}; recomputeSlRay(); }
}
function onSetupFieldChange() {
  commitActiveSetup();
  computeEntry();
  commitActiveSetup();
  renderSetupTabs();
  updateUiButtons();
  renderObjects();
  relayoutDrawingsOnly();
  scheduleAutosave();
}
function setupLabel(s, i) {
  const fam = {cap:'cap', breakout:'brk', structure_break:'SB', unknown:'?'}[s.family] || s.family;
  return (i + 1) + ' · ' + fam;
}
function renderSetupTabs() {
  const el = document.getElementById('setupTabs');
  if (!el) return;
  const tabs = setups.map((s, i) => `
    <div class="setup-tab ${i === activeSetup ? 'active' : ''}" onclick="switchSetup(${i})">
      <span>${esc(setupLabel(s, i))}</span>
      ${setups.length > 1 ? `<span class="kill" title="Delete setup" onclick="event.stopPropagation(); deleteSetup(${i})">×</span>` : ''}
    </div>`).join('');
  el.innerHTML = tabs + `<div class="setup-add" title="Add another setup for this event" onclick="addSetup()">+</div>`;
}
function switchSetup(i) {
  if (i === activeSetup) return;
  if (zzDraft) finishZigzag();
  captureVisibleRanges();
  commitActiveSetup();
  applySetup(i);
  renderSetupTabs();
  updateUiButtons();
  renderObjects();
  updateMetrics();
  draw();
  scheduleAutosave();
}
function addSetup() {
  if (zzDraft) finishZigzag();
  commitActiveSetup();
  setups.push(emptySetup());
  applySetup(setups.length - 1);
  renderSetupTabs();
  updateUiButtons();
  renderObjects();
  draw();
  scheduleAutosave();
  toast('new setup ' + setups.length);
}
function deleteSetup(i) {
  if (setups.length <= 1) { toast('an event keeps at least one setup'); return; }
  captureVisibleRanges();
  if (i !== activeSetup) commitActiveSetup();
  setups.splice(i, 1);
  const next = Math.max(0, Math.min(activeSetup >= i ? activeSetup - 1 : activeSetup, setups.length - 1));
  applySetup(next);
  renderSetupTabs();
  updateUiButtons();
  renderObjects();
  draw();
  scheduleAutosave();
}
function setupFromLabel(s) {
  const out = emptySetup();
  out.family = s.family || 'cap';
  out.quality = s.quality || 'good';
  out.notes = s.notes || '';
  out.level = (s.level_price != null && s.level_start_ms != null)
    ? {price:+s.level_price, start_ms:+s.level_start_ms,
       end_ms: s.level_end_ms != null ? +s.level_end_ms : levelAutoEndMs(+s.level_price, +s.level_start_ms), broken:false}
    : null;
  out.pump = (s.pump_start_ms != null && s.pump_start_price != null && s.culmination_ms != null && s.culmination_price != null)
    ? {start:{idx:nearestCandle(+s.pump_start_ms), ms:+s.pump_start_ms, price:+s.pump_start_price},
       high:{idx:nearestCandle(+s.culmination_ms), ms:+s.culmination_ms, price:+s.culmination_price}}
    : null;
  out.exitPoint = (s.exit_ms != null && s.exit_price != null) ? {ms:+s.exit_ms, price:+s.exit_price} : null;
  out.slPrice = s.sl_price != null ? +s.sl_price : null;
  const zpts = Array.isArray(s.zigzag_points)
    ? s.zigzag_points.filter(p => p && Number.isFinite(Number(p.ms)) && Number.isFinite(Number(p.price)))
                     .map(p => ({ms:Number(p.ms), price:Number(p.price)}))
    : [];
  out.zigzag = zpts.length ? {points: zpts.sort((a,b) => a.ms - b.ms)} : null;
  return out;
}
function setupsFromSavedLabel(saved) {
  if (!saved) return [emptySetup()];
  if (Array.isArray(saved.setups) && saved.setups.length) return saved.setups.map(setupFromLabel);
  // legacy flat label -> one setup (ignore old swing_points; zigzag replaces them)
  if (saved.level_price != null || saved.family || saved.has_level != null) return [setupFromLabel(saved)];
  return [emptySetup()];
}

/* ---------- workbench: run + result review ---------- */
function showLabeling() {
  document.body.classList.remove('results-mode');
  resultMode = false;
  resultTrade = null;
  selectedTradeId = null;
  if (visible.length) loadEvent(idx);
}
async function showResults() {
  const ok = await flushAutosave({finishDraft:true, quiet:false});
  if (!ok) return;
  document.body.classList.add('results-mode');
  resultMode = true;
  await loadLatestIteration();
  await loadTrades();
}
async function startIteration() {
  const ok = await flushAutosave({finishDraft:true, quiet:false});
  if (!ok) return;
  document.body.classList.add('results-mode');
  resultMode = true;
  const btn = document.getElementById('runIterationBtn');
  btn.disabled = true;
  await fetch('/api/iteration/start', {method:'POST'});
  pollIterationStatus(true);
}
async function pollIterationStatus(keep=false) {
  const r = await fetch('/api/iteration/status');
  const status = await r.json();
  renderIterationStatus(status);
  const running = status.state === 'running';
  document.getElementById('runIterationBtn').disabled = running;
  if (running || keep) {
    if (iterationPoll) clearTimeout(iterationPoll);
    iterationPoll = setTimeout(() => pollIterationStatus(false), running ? 1200 : 400);
  } else {
    await loadLatestIteration();
    await loadTrades();
  }
}
function renderIterationStatus(status) {
  const el = document.getElementById('iterationStatus');
  const steps = (status.steps || []).slice(-4).map(s => `${esc(s.at)} · ${esc(s.message)}`).join('<br>');
  el.innerHTML = `<b>${esc(status.state || 'idle')}</b> · ${esc(status.message || '')}` +
    (status.run_dir ? `<br><span>${esc(status.run_dir)}</span>` : '') +
    (status.error ? `<br><span style="color:var(--red)">${esc(status.error)}</span>` : '') +
    (steps ? `<br>${steps}` : '');
}
async function loadLatestIteration() {
  const r = await fetch('/api/iteration/latest');
  const data = await r.json();
  if (data.error) {
    document.getElementById('iterationStatus').innerHTML = `<b>error</b><br><span style="color:var(--red)">${esc(data.error)}</span>`;
    document.getElementById('resultSlices').innerHTML = '';
    toast(data.error, 4200);
    return;
  }
  if (!data.exists) {
    document.getElementById('iterationStatus').innerHTML = '<b>no run</b> · press Run';
    document.getElementById('resultSlices').innerHTML = '';
    return;
  }
  const lp = data.label_progress || {};
  const cp = data.candidate_summary || {};
  const base = data.trade_report && data.trade_report.combined_portfolio ? data.trade_report.combined_portfolio : {};
  document.getElementById('iterationStatus').innerHTML =
    `<b>${esc(data.latest.run_id)}</b><br>` +
    `labels ${lp.labeled_groups || 0}/${cp.candidate_groups || 0} · trades ${base.trades || 0} · WR ${Number(base.win_rate_pct || 0).toFixed(1)}% · return ${Number(base.return_pct || 0).toFixed(1)}%`;
  renderDashboardSlices(data.dashboard || {});
}
function renderDashboardSlices(dashboard) {
  const root = document.getElementById('resultSlices');
  const slices = dashboard.slices || {};
  const cards = [];
  for (const key of ['tf','setup_family','session','outcome','month','week']) {
    const rows = (slices[key] || []).slice(0, 6);
    if (!rows.length) continue;
    cards.push(`<div class="slice-card"><b>${esc(key)}</b>${rows.map(r => {
      const name = r[key];
      return `${esc(name)}: ${r.trades} / ${Number(r.win_rate_pct || 0).toFixed(0)}% / ${Number(r.return_pct || 0).toFixed(1)}%`;
    }).join('<br>')}</div>`);
  }
  root.innerHTML = cards.join('');
}
async function loadTrades() {
  const r = await fetch('/api/iteration/trades');
  const data = await r.json();
  if (data.error) {
    resultTrades = [];
    populateTradeFilters();
    renderTrades();
    toast(data.error, 4200);
    return;
  }
  resultTrades = data.trades || [];
  populateTradeFilters();
  renderTrades();
}
function populateTradeFilters() {
  const mapping = {
    filterTf:'tf',
    filterSetup:'setup_family',
    filterOutcome:'outcome',
    filterSession:'session',
    filterMonth:'month',
    filterWeek:'week',
  };
  for (const [id, key] of Object.entries(mapping)) {
    const el = document.getElementById(id);
    const old = el.value;
    const values = [...new Set(resultTrades.map(t => String(t[key] ?? '')).filter(Boolean))].sort();
    el.innerHTML = `<option value="">${key}</option>` + values.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
    if (values.includes(old)) el.value = old;
    refreshSelect(el);
  }
}
function filteredTrades() {
  const checks = [
    ['filterTf','tf'],
    ['filterSetup','setup_family'],
    ['filterOutcome','outcome'],
    ['filterSession','session'],
    ['filterMonth','month'],
    ['filterWeek','week'],
  ];
  return resultTrades.filter(t => checks.every(([id,key]) => {
    const v = document.getElementById(id).value;
    return !v || String(t[key] ?? '') === v;
  }));
}
function renderTrades() {
  const root = document.getElementById('tradeList');
  const rows = filteredTrades();
  if (!rows.length) { root.innerHTML = '<div class="hint">No trades for current filters.</div>'; return; }
  root.innerHTML = rows.map(t => `
    <div class="trade-row ${t.outcome || ''} ${selectedTradeId === t.trade_id ? 'active' : ''}" data-trade-id="${esc(t.trade_id)}" onclick="selectTrade('${esc(t.trade_id)}')">
      <div class="trade-main"><span>${esc(displaySymbol(t.symbol))} ${esc(t.tf)} ${Number(t.net_r || 0).toFixed(2)}R</span><span>${esc(t.outcome)}</span></div>
      <div class="trade-tags">
        <span class="trade-tag">${esc(t.setup_family)}</span>
        <span class="trade-tag">${esc(t.session)}</span>
        <span class="trade-tag">${esc(t.day)}</span>
        ${t.has_result_annotation ? '<span class="trade-tag">note</span>' : ''}
      </div>
    </div>
  `).join('');
}
function drawingsPayload() {
  return {
    level, pump, exitPoint, zigzag,
    sl: sl && entry ? sl : null,
  };
}
function loadResultDrawings(annotation) {
  const d = annotation && annotation.drawings ? annotation.drawings : {};
  level = d.level || null;
  pump = d.pump || null;
  exitPoint = d.exitPoint || null;
  zigzag = (d.zigzag && Array.isArray(d.zigzag.points)) ? d.zigzag : null;
  const savedSl = d.sl && Number.isFinite(Number(d.sl.price)) ? d.sl : null;
  zzDraft = null;
  sl = savedSl ? {price:Number(savedSl.price), hit_ms:savedSl.hit_ms ?? null, end_ms:savedSl.end_ms ?? null} : null;
  entry = null;
  if (level) computeEntry();
  if (sl && entry) recomputeSlRay();
  selectedObj = null;
}
async function selectTrade(tradeId) {
  selectedTradeId = tradeId;
  renderTrades();
  const r = await fetch('/api/iteration/trade_candles?trade_id=' + encodeURIComponent(tradeId));
  const payload = await r.json();
  if (payload.error) { toast(payload.error, 3000); return; }
  resultMode = true;
  resultTrade = payload.trade;
  current = payload;
  current.group = {event_id: tradeId};
  computeBarMs();
  xRange = null; yRange = null; yAuto = true;
  tool = null; drawStep = 0; pending = null;
  loadResultDrawings(payload.result_annotation);
  document.getElementById('resultComment').value = payload.result_annotation ? (payload.result_annotation.comment || '') : '';
  document.getElementById('saveResultAnnotationBtn').disabled = false;
  clearGhost();
  syncToolButtons();
  renderObjects();
  updateMetrics();
  draw();
}
async function saveResultAnnotation() {
  if (!selectedTradeId) { toast('select a trade first'); return; }
  const payload = {
    trade_id: selectedTradeId,
    comment: document.getElementById('resultComment').value,
    drawings: drawingsPayload(),
  };
  const r = await fetch('/api/result_annotation', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
  const j = await r.json();
  if (!j.ok) { toast(j.error || 'save failed', 3000); return; }
  toast('trade annotation saved');
  await loadTrades();
}

/* ---------- candidates list ---------- */
async function loadCandidates() {
  const r = await fetch('/api/candidates');
  const data = await r.json();
  if (data.error) {
    candidates = [];
    visible = [];
    document.getElementById('list').innerHTML = `<div class="hint">${esc(data.error)}</div>`;
    document.getElementById('progress').innerText = '0/0 labeled';
    clearChart();
    toast(data.error, 4200);
    return;
  }
  candidates = data.candidates || [];
  enhanceVisibleSelects();
  initDefaultLineHover();
  renderList();
  if (visible.length) loadEvent(0);
  pollIterationStatus(false);
  loadLatestIteration();
}
function labelNoteText(label) {
  if (!label) return '';
  if (Array.isArray(label.setups)) {
    const parts = label.setups.map(s => s && s.notes).filter(Boolean);
    if (parts.length) return parts.join(' | ');
    const n = label.setups.length;
    return n > 1 ? (n + ' setups') : '';
  }
  return label.notes || '';
}
function filtered() {
  const f = document.getElementById('filter').value;
  const q = document.getElementById('search').value.trim().toLowerCase();
  return candidates.filter(c => (f==='all' || (f==='labeled' ? c.labeled : !c.labeled)) && (!q || searchBlob(c).includes(q)));
}
function renderList() {
  visible = filtered();
  const el = document.getElementById('list');
  el.innerHTML = '';
  visible.forEach((c, i) => {
    const d = document.createElement('div');
    d.className = 'row' + (i===idx ? ' active' : '') + (c.labeled ? ' labeled' : '');
    d.onclick = () => navigateToEventId(c.event_id);
    const variants = c.variants || [c];
    const pumpBadge = c.pump_pct == null ? '' : `<span class="badge hot">${fmtPct(c.pump_pct)}</span>`;
    const tfs = variants.map(v => esc(v.tf)).join('/');
    const noteText = labelNoteText(c.label);
    const note = noteText ? `<div class="small">note: ${esc(noteText.slice(0,90))}</div>` : '';
    const multi = variants.length > 1 ? tfs : variants[0].tf;
    d.innerHTML = `<div class="row-head"><span>${i+1}. ${esc(displaySymbol(c.symbol))}</span><span class="small">${esc(multi)}</span></div>
      <div class="row-badges">${pumpBadge}<span class="badge">${iso(c.review_start_ms)}</span></div>${note}`;
    el.appendChild(d);
  });
  document.getElementById('progress').innerText = `${candidates.filter(c=>c.labeled).length}/${candidates.length} labeled`;
  document.getElementById('jump').max = String(Math.max(1, visible.length));
  updateEventButton();
}
async function loadEvent(i) {
  const token = ++loadToken;
  resultMode = false;
  resultTrade = null;
  selectedTradeId = null;
  document.body.classList.remove('results-mode');
  idx = Math.max(0, Math.min(i, visible.length - 1));
  renderList();
  document.getElementById('side').scrollTop = 0;
  const group = visible[idx];
  if (!group) return;
  selectedTf = group.default_tf || (group.variants && group.variants[0] && group.variants[0].tf) || group.tf;
  populateTfSelect(group);
  xRange = null; yRange = null; yAuto = true;
  tool = null; drawStep = 0; pending = null; selectedObj = null;
  clearGhost();
  const r = await fetch('/api/candles?event_id=' + encodeURIComponent(group.event_id) + '&tf=' + encodeURIComponent(selectedTf));
  const payload = await r.json();
  if (token !== loadToken) return;
  if (payload.error) {
    current = null;
    level = null; pump = null; entry = null; sl = null; exitPoint = null; zigzag = null; zzDraft = null; setups = [];
    renderObjects();
    clearChart();
    toast(payload.error, 4200);
    return;
  }
  current = payload;
  current.group = group;
  computeBarMs();
  // Build the per-event setups from the saved label (or one empty setup) and show
  // the first one. Each setup carries its own family/quality/level/pump/exit/SL/zigzag.
  setups = setupsFromSavedLabel(group.label);
  applySetup(0);
  renderSetupTabs();
  document.getElementById('jump').value = String(idx + 1);
  syncToolButtons();
  updateUiButtons();
  renderObjects();
  updateMetrics();
  draw();
  const baseline = currentLabelPayload({finishDraft:false});
  if (baseline) savedSignatures.set(baseline.event_id, labelSignature(baseline));
}
function populateTfSelect(group) {
  const el = document.getElementById('tfSelect');
  el.innerHTML = '';
  for (const v of (group.variants || [group])) {
    const opt = document.createElement('option');
    opt.value = String(v.tf);
    opt.textContent = `${v.tf}  ${fmtPct(v.pump_pct)}`;
    el.appendChild(opt);
  }
  el.value = selectedTf;
  refreshSelect(el);
}
async function changeTf() {
  if (!visible.length) return;
  const token = ++loadToken;
  selectedTf = document.getElementById('tfSelect').value;
  const group = visible[idx];
  commitActiveSetup();
  const r = await fetch('/api/candles?event_id=' + encodeURIComponent(group.event_id) + '&tf=' + encodeURIComponent(selectedTf));
  const payload = await r.json();
  if (token !== loadToken) return;
  if (payload.error) {
    toast(payload.error, 4200);
    refreshSelect(document.getElementById('tfSelect'));
    return;
  }
  current = payload;
  current.group = group;
  computeBarMs();
  const keepSl = sl ? sl.price : null;
  computeEntry();
  if (keepSl != null && entry) { sl = {price: keepSl}; recomputeSlRay(); }
  commitActiveSetup();
  updateMetrics();
  refreshSelect(document.getElementById('tfSelect'));
  if (yAuto) { draw(); fitY(); } else draw();
  renderObjects();
  scheduleAutosave();
}
function changeTfBy(delta) {
  const el = document.getElementById('tfSelect');
  if (!el.options.length) return;
  const next = Math.max(0, Math.min(el.options.length - 1, el.selectedIndex + delta));
  if (next === el.selectedIndex) return;
  el.selectedIndex = next;
  changeTf();
}
function initDefaultLineHover() {
  const el = document.getElementById('defaultLinesHover');
  if (!el || el.dataset.ready) return;
  el.dataset.ready = '1';
  el.addEventListener('mouseenter', () => { showDefaultLines = true; el.classList.add('active'); draw(); });
  el.addEventListener('mouseleave', () => { showDefaultLines = false; el.classList.remove('active'); draw(); });
}
function updateMetrics() {
  if (!current) return;
  const g = current.group || visible[idx], e = current.event;
  const items = [
    ['sym', displaySymbol(e.symbol)],
    ['tf', e.tf],
    ['TFs', (g.variants || [g]).map(v => v.tf).join('/')],
    ['pump', fmtPct(e.pump_pct)],
    ['vol', 'x' + Number(e.pump_over_sleep_vol || 0).toFixed(1)],
    ['trd', 'x' + Number(e.pump_over_sleep_trades || 0).toFixed(1)]
  ];
  document.getElementById('metricbar').innerHTML = items.map(([k,v]) => `<span class="metric">${esc(k)} <b>${esc(v)}</b></span>`).join('');
}

/* ---------- chart shapes & annotations ---------- */
function shapes() {
  if (!current) return [];
  const e = current.event, out = [];
  function add(shape) { out.push(shape); }
  if (showDefaultLines) {
    for (const [k,v] of Object.entries(e.marker_columns || {})) {
      if (k.endsWith('_ms') && Number.isFinite(Number(v))) {
        add({type:'line', layer:'below', editable:false, x0:new Date(v), x1:new Date(v), yref:'paper', y0:0, y1:1, line:{color:'#5f6673', width:1}});
      }
    }
    if (Number.isFinite(Number(e.suggested_level)) && Number.isFinite(Number(e.suggested_level_start_ms)) && Number.isFinite(Number(e.suggested_level_end_ms))) {
      add({type:'line', layer:'below', editable:false, x0:new Date(e.suggested_level_start_ms), x1:new Date(e.suggested_level_end_ms), y0:Number(e.suggested_level), y1:Number(e.suggested_level), line:{color:'#5f6673', width:1}});
    }
  }
  if (resultTrade) {
    const start = Number(resultTrade.fill_time_ms);
    const end = Number(resultTrade.exit_time_ms);
    const left = Number.isFinite(start) ? start : chartDataEndMs() - barMs;
    const right = Number.isFinite(end) ? end : chartDataEndMs();
    function addTradeLine(price, color, label) {
      const p = Number(price);
      if (!Number.isFinite(p) || p <= 0) return;
      add({type:'line', layer:'above', editable:false,
        x0:new Date(left), x1:new Date(right), y0:p, y1:p,
        line:{color, width:1}});
    }
    addTradeLine(resultTrade.level, '#8aa0d8', 'level');
    addTradeLine(resultTrade.entry_price, '#7dbb91', 'entry');
    addTradeLine(resultTrade.stop, '#d18495', 'stop');
    addTradeLine(resultTrade.take, '#7dbb91', 'take');
    if (Number.isFinite(Number(resultTrade.exit_time_ms))) {
      add({type:'line', layer:'above', editable:false,
        x0:new Date(Number(resultTrade.exit_time_ms)), x1:new Date(Number(resultTrade.exit_time_ms)),
        yref:'paper', y0:0.24, y1:1,
        line:{color:'#a597d6', width:1}});
    }
  }
  if (pump) {
    const sel = selectedObj === 'pump';
      add({type:'rect', layer:'below', editable:false,
      x0:new Date(Math.min(pump.start.ms, pump.high.ms)), x1:new Date(Math.max(pump.start.ms, pump.high.ms)),
      y0:Math.min(pump.start.price, pump.high.price), y1:Math.max(pump.start.price, pump.high.price),
      line:{color: sel ? '#a9d8b9' : '#7dbb91', width: 1},
      fillcolor:'rgba(125,187,145,.08)'});
  }
  const sbAnchor = (!level && document.getElementById('family').value === 'structure_break') ? structureBreakAnchorFrom(zigzag) : null;
  if (sbAnchor) {
    add({type:'line', layer:'above', editable:false,
      x0:new Date(sbAnchor.start_ms), x1:new Date(levelShapeEndMs(sbAnchor)),
      y0:sbAnchor.price, y1:sbAnchor.price,
      line:{color:'#d9c27a', width:1, dash:'dot'}});
  }
  if (level) {
    const sel = selectedObj === 'level';
    add({type:'line', layer:'above', editable:false,
      x0:new Date(levelShapeStartMs(level)), x1:new Date(levelShapeEndMs(level)),
      y0:level.price, y1:level.price,
      line:{color: sel ? '#c8d4ff' : '#8aa0d8', width: 1}});
  }
  if (sl && entry) {
    const sel = selectedObj === 'sl';
    add({type:'line', layer:'above', editable:false,
      x0:new Date(entry.ms), x1:new Date(sl.end_ms),
      y0:sl.price, y1:sl.price,
      line:{color: sel ? '#e7a9b6' : '#d18495', width: 1, dash:'solid'}});
  }
  if (exitPoint) {
    const sel = selectedObj === 'exit';
    const c = current.candles;
    const yr = Math.max(...c.high) - Math.min(...c.low);
    const dy = Math.max(Math.abs(exitPoint.price) * 0.0015, yr * 0.006);
    const dx = Math.max(barMs * 0.5, 2*60000);
    add({type:'circle', layer:'above', editable:false,
      x0:new Date(exitPoint.ms - dx), x1:new Date(exitPoint.ms + dx),
      y0:exitPoint.price - dy, y1:exitPoint.price + dy,
      line:{color: sel ? '#c9bdf0' : '#a597d6', width: 1}, fillcolor:'rgba(165,151,214,.10)'});
  }
  return out;
}
function zigzagTrace() {
  const pts = (zigzag && Array.isArray(zigzag.points)) ? [...zigzag.points].sort((a,b) => a.ms - b.ms) : [];
  const sel = selectedObj === 'zigzag';
  const color = sel ? '#f0dc96' : '#d9c27a';
  return {
    type:'scatter',
    mode:'lines+markers',
    x: pts.map(p => new Date(p.ms)),
    y: pts.map(p => p.price),
    xaxis:'x',
    yaxis:'y',
    name:'zigzag',
    hoverinfo:'none',
    hovertemplate:'<extra></extra>',
    line:{color, width: sel ? 2 : 1.4},
    marker:{size: sel ? 8 : 6, symbol:'circle', color, line:{width:1, color:'#171a20'}, opacity:0.95}
  };
}
function annotations() {
  if (!current) return [];
  const out = [];
  if (resultTrade) {
    const x = new Date(Number(resultTrade.fill_time_ms));
    const fields = [
      ['ENTRY', resultTrade.entry_price, '#7dbb91', -18],
      ['STOP', resultTrade.stop, '#d18495', 16],
      ['TAKE', resultTrade.take, '#7dbb91', -18],
      ['LEVEL', resultTrade.level, '#8aa0d8', 16],
    ];
    for (const [label, price, color, shift] of fields) {
      const p = Number(price);
      if (!Number.isFinite(p) || p <= 0) continue;
      out.push({x, y:p, text:`${label} ${fmtPrice(p)}`,
        showarrow:false, xanchor:'right', yanchor:'middle', xshift:-6, yshift:shift,
        font:{size:10, color}, bgcolor:'rgba(17,19,24,.78)', borderpad:2});
    }
  }
  const sbAnchor = (!level && document.getElementById('family').value === 'structure_break') ? structureBreakAnchorFrom(zigzag) : null;
  if (sbAnchor) {
    out.push({x:new Date(levelShapeEndMs(sbAnchor)), y:sbAnchor.price, text:`SB ${fmtPrice(sbAnchor.price)}`,
      showarrow:false, xanchor:'left', yanchor:'middle', xshift:6,
      font:{size:11, color:'#d9c27a'}, bgcolor:'rgba(17,19,24,.75)', borderpad:3});
  }
  if (level) {
    out.push({x:new Date(levelShapeEndMs(level)), y:level.price, text:fmtPrice(level.price),
      showarrow:false, xanchor:'left', yanchor:'middle', xshift:6,
      font:{size:11, color:'#8aa0d8'}, bgcolor:'rgba(34,43,69,.80)', borderpad:3});
  }
  if (pump && pump.start.price > 0) {
    const move = pump.high.price / pump.start.price - 1;
    out.push({x:new Date(pump.high.ms + barMs), y:pump.high.price, text:`+${(move*100).toFixed(1)}%`,
      showarrow:false, xanchor:'left', yanchor:'bottom',
      font:{size:11, color:'#7dbb91'}, bgcolor:'rgba(23,36,29,.80)', borderpad:3});
  }
  if (entry) {
    out.push({x:new Date(entry.ms), y:entry.price, text:'entry',
      showarrow:true, ax:0, ay:26, arrowcolor:'#7dbb91', arrowwidth:1, arrowhead:2,
      font:{size:11, color:'#7dbb91'}, bgcolor:'rgba(23,36,29,.80)', borderpad:3});
  }
  if (sl && entry && entry.price > 0) {
    const risk = (entry.price - sl.price) / entry.price;
    out.push({x:new Date(entry.ms), y:sl.price, text:`SL -${(risk*100).toFixed(1)}%`,
      showarrow:false, xanchor:'right', yanchor:'middle', xshift:-6,
      font:{size:11, color:'#d18495'}, bgcolor:'rgba(37,25,34,.80)', borderpad:3});
  }
  return out;
}
function draw() {
  if (!current) return;
  const c = current.candles, x = c.timestamp.map(t => new Date(t)), e = current.event;
  document.getElementById('title').innerText = `${displaySymbol(e.symbol)} ${e.tf}`;
  const candle = {type:'candlestick', x, open:c.open, high:c.high, low:c.low, close:c.close, name:'price', xaxis:'x', yaxis:'y',
    increasing:{line:{color:'#7dbb91', width:1}, fillcolor:'rgba(125,187,145,.42)'},
    decreasing:{line:{color:'#c99a62', width:1}, fillcolor:'rgba(201,154,98,.40)'},
    hoverinfo:'none', hovertemplate:'<extra></extra>'};
  const vol = {type:'bar', x, y:c.quote_volume, name:'volume', marker:{color:'#6f7890'}, xaxis:'x', yaxis:'y2', opacity:0.32, hoverinfo:'none', hovertemplate:'<extra></extra>'};
  const traces = [candle, vol];
  if (zigzag && zigzag.points && zigzag.points.length) traces.push(zigzagTrace());
  const title = resultTrade
    ? `${displaySymbol(e.symbol)} ${e.tf} / ${resultTrade.outcome} / ${Number(resultTrade.net_r || 0).toFixed(2)}R`
    : `pump ${fmtPct(e.pump_pct)} / vol x${Number(e.pump_over_sleep_vol||0).toFixed(1)} / trades x${Number(e.pump_over_sleep_trades||0).toFixed(1)}`;
  const spike = {showspikes:true, spikemode:'across', spikesnap:'cursor', spikedash:'dot', spikethickness:1, spikecolor:'#4a5160'};
  const layout = {
    paper_bgcolor:'#111318', plot_bgcolor:'#171a20', font:{color:'#e7e3d8'},
    margin:{l:56,r:28,t:42,b:38}, title:{text:title, x:0.99, xanchor:'right', font:{size:13}},
    dragmode:'pan', hovermode:false, hoverdistance:-1, spikedistance:-1,
    xaxis:Object.assign({rangeslider:{visible:false}, gridcolor:'#252a32', linecolor:'#2a2e36', zeroline:false}, spike),
    yaxis:Object.assign({domain:[0.24,1], gridcolor:'#252a32', linecolor:'#2a2e36', zeroline:false, title:'price', hoverformat:'.6g', tickformat:priceTickFormat(c.close)}, spike),
    yaxis2:{domain:[0,0.17], gridcolor:'#252a32', linecolor:'#2a2e36', zeroline:false, title:'volume', hoverformat:'.4g', rangemode:'tozero', fixedrange:true},
    shapes: shapes(), annotations: annotations(), showlegend:false, bargap:0.42
  };
  if (xRange) layout.xaxis.range = xRange;
  if (yRange) layout.yaxis.range = yRange;
  Plotly.react('chart', traces, layout, {responsive:true, displayModeBar:false, displaylogo:false, scrollZoom:false, editable:false, edits:{shapePosition:false}});
  attachChartHandlers();
  positionZones();
}
function clearChart() {
  document.getElementById('title').innerText = '';
  clearGhost();
  if (typeof Plotly !== 'undefined') {
    Plotly.react('chart', [], {
      paper_bgcolor:'#111318',
      plot_bgcolor:'#111318',
      margin:{l:56,r:28,t:42,b:38},
      xaxis:{visible:false},
      yaxis:{visible:false},
      showlegend:false,
    }, {responsive:true, displayModeBar:false, displaylogo:false});
  }
}

/* ---------- axis zones: LMB-drag to scale, dblclick to fit ---------- */
function positionZones() {
  const r = plotRefs(); if (!r) return;
  const yz = document.getElementById('yzone'), xz = document.getElementById('xzone');
  yz.style.left = '0px';
  yz.style.width = Math.max(0, r.xa._offset - 1) + 'px';
  yz.style.top = r.ya._offset + 'px';
  yz.style.height = r.ya._length + 'px';
  const mb = r.fl.margin ? r.fl.margin.b : 38;
  xz.style.left = r.xa._offset + 'px';
  xz.style.width = r.xa._length + 'px';
  xz.style.top = (r.fl.height - mb) + 'px';
  xz.style.height = mb + 'px';
}
function clampYRange(r) {
  let a = Number(r[0]), b = Number(r[1]);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return r;
  if (a < 0) a = 0;
  if (b <= a) b = a + Math.max(Math.abs(b), 1e-9) * 0.01;
  return [a, b];
}
function guardedRelayout(update) {
  relayoutGuard = true;
  const p = Plotly.relayout('chart', update);
  const done = () => { relayoutGuard = false; };
  if (p && p.finally) p.finally(done); else setTimeout(done, 0);
}
function currentAxisRanges() {
  const r = plotRefs();
  return {
    x: xRange || (r ? r.xa.range : null),
    y: yRange || (r ? r.ya.range : null)
  };
}
function fitY() {
  if (!current) return;
  const c = current.candles;
  if (!c.timestamp.length) return;
  const r = plotRefs();
  let a, b;
  if (xRange && r) { a = xLinearToMs(r.xa, r.xa.r2l(xRange[0])); b = xLinearToMs(r.xa, r.xa.r2l(xRange[1])); }
  else if (xRange) { a = pms(xRange[0]); b = pms(xRange[1]); }
  else { a = c.timestamp[0]; b = c.timestamp[c.timestamp.length-1] + barMs; }
  let lo = Infinity, hi = -Infinity;
  for (let i=0; i<c.timestamp.length; i++) {
    const t = c.timestamp[i];
    if (t + barMs < a || t > b) continue;
    if (c.low[i] < lo) lo = c.low[i];
    if (c.high[i] > hi) hi = c.high[i];
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return;
  const pad = Math.max((hi - lo) * 0.08, hi * 0.0005);
  yRange = clampYRange([lo - pad, hi + pad]);
  guardedRelayout({'yaxis.range': yRange});
}
function bindAxisZone(el, axis) {
  el.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const r = plotRefs();
    const startPx = axis === 'y' ? e.clientY : e.clientX;
    const ranges = currentAxisRanges();
    if (axis === 'y' && !ranges.y) return;
    if (axis === 'x' && (!ranges.x || !r)) return;
    const startRange = axis === 'y'
      ? [Number(ranges.y[0]), Number(ranges.y[1])]
      : [r.xa.r2l(ranges.x[0]), r.xa.r2l(ranges.x[1])];
    function move(me) {
      const d = (axis === 'y' ? me.clientY : me.clientX) - startPx;
      const factor = Math.exp((axis === 'y' ? d : -d) * 0.005);
      if (axis === 'y') {
        yAuto = false;
        const mid = (startRange[0] + startRange[1]) / 2;
        const half = (startRange[1] - startRange[0]) / 2 * factor;
        yRange = clampYRange([mid - half, mid + half]);
        guardedRelayout({'yaxis.range': yRange});
      } else {
        const b = startRange[1];
        const span = (startRange[1] - startRange[0]) * factor;
        xRange = [r.xa.l2r(b - span), r.xa.l2r(b)];
        guardedRelayout({'xaxis.range': xRange});
        if (yAuto) fitY();
      }
    }
    function up() {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    }
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  });
  el.addEventListener('dblclick', e => {
    e.preventDefault(); e.stopPropagation();
    if (axis === 'y') { yAuto = true; fitY(); }
    else { xRange = null; yAuto = true; guardedRelayout({'xaxis.autorange': true}); setTimeout(fitY, 60); }
  });
}

/* ---------- wheel zoom (TV: X at cursor; Shift=Y; Ctrl=both) ---------- */
function zoomAroundAnchor(a, b, anchor, factor) {
  return [anchor - (anchor - a) * factor, anchor + (b - anchor) * factor];
}
function chartWheelZoom(ev) {
  if (!current) return;
  ev.preventDefault();
  const factor = ev.deltaY < 0 ? 0.85 : 1.18;
  const ranges = currentAxisRanges();
  const r = plotRefs();
  const bb = gd().getBoundingClientRect();
  let fracX = 0.5, fracY = 0.5;
  if (r) {
    fracX = Math.min(1, Math.max(0, (ev.clientX - bb.left - r.xa._offset) / (r.xa._length || 1)));
    fracY = Math.min(1, Math.max(0, 1 - (ev.clientY - bb.top - r.ya._offset) / (r.ya._length || 1)));
  }
  const zoomBoth = ev.ctrlKey || ev.metaKey;
  const zoomY = ev.shiftKey || zoomBoth;
  const zoomX = zoomBoth || !ev.shiftKey;
  const update = {};
  if (zoomX && ranges.x && r) {
    const a = r.xa.r2l(ranges.x[0]), b = r.xa.r2l(ranges.x[1]);
    const anchor = a + (b - a) * fracX;
    const [na, nb] = zoomAroundAnchor(a, b, anchor, factor);
    xRange = [r.xa.l2r(na), r.xa.l2r(nb)];
    update['xaxis.range'] = xRange;
  }
  if (zoomY && ranges.y) {
    yAuto = false;
    const a = Number(ranges.y[0]), b = Number(ranges.y[1]);
    const anchor = a + (b - a) * fracY;
    yRange = clampYRange(zoomAroundAnchor(a, b, anchor, factor));
    update['yaxis.range'] = yRange;
  }
  guardedRelayout(update);
  if (zoomX && !zoomY && yAuto) fitY();
}
/* ---------- chart event wiring ---------- */
let downPos = null;
function attachChartHandlers() {
  if (chartHandlersAttached) return;
  chartHandlersAttached = true;
  const el = gd();
  el.on('plotly_relayout', ev => {
    if (relayoutGuard) return;
    if (dragObj || tool || downPos) return;
    let xChanged = false;
    if (ev['xaxis.range[0]'] && ev['xaxis.range[1]']) { xRange = [ev['xaxis.range[0]'], ev['xaxis.range[1]']]; xChanged = true; }
    if (ev['yaxis.range[0]'] != null && ev['yaxis.range[1]'] != null && !yAuto) {
      yRange = [Number(ev['yaxis.range[0]']), Number(ev['yaxis.range[1]'])];
      const cl = clampYRange(yRange);
      if (cl[0] !== yRange[0] || cl[1] !== yRange[1]) {
        const span = yRange[1] - yRange[0];
        yRange = [0, span]; // slide the window up to the zero floor instead of squashing it
        guardedRelayout({'yaxis.range': yRange});
      }
    }
    if (ev['xaxis.autorange']) { xRange = null; yAuto = true; }
    if (ev['yaxis.autorange']) { yRange = null; yAuto = true; }
    if (xChanged && yAuto) fitY();
    positionZones();
  });
  const wrap = document.getElementById('chartwrap');
  const surface = document.getElementById('ovl');
  wrap.addEventListener('wheel', chartWheelZoom, {passive:false});
  function beginObjectDrag(e) {
    if (dragObj) return false;
    if (tool || !current || e.button !== 0) return;
    const hit = hitObject(e);
    if (!hit) return;
    e.preventDefault();
    e.stopPropagation();
    dragObj = { hit, start: eventDataPoint(e), pointerId: e.pointerId };
    selectedObj = hit.id;
    renderObjects();
    return true;
  }
  wrap.addEventListener('pointerdown', e => {
    if (!beginObjectDrag(e)) return;
    if (wrap.setPointerCapture) wrap.setPointerCapture(e.pointerId);
  }, true);
  wrap.addEventListener('mousedown', e => {
    beginObjectDrag(e);
  }, true);
  wrap.addEventListener('pointermove', e => {
    if (!dragObj) return;
    e.preventDefault();
    e.stopPropagation();
    applyDrag(dragObj.hit, e);
  }, true);
  window.addEventListener('mousemove', e => {
    if (!dragObj) return;
    e.preventDefault();
    e.stopPropagation();
    applyDrag(dragObj.hit, e);
  }, true);
  wrap.addEventListener('pointerup', e => {
    if (!dragObj) return;
    e.preventDefault();
    e.stopPropagation();
    const pointerId = dragObj.pointerId;
    dragObj = null;
    if (wrap.releasePointerCapture && pointerId != null) {
      try { wrap.releasePointerCapture(pointerId); } catch (_) {}
    }
  }, true);
  window.addEventListener('mouseup', e => {
    if (!dragObj) return;
    e.preventDefault();
    e.stopPropagation();
    dragObj = null;
  }, true);
  surface.addEventListener('pointerdown', e => {
    if (!tool || !current || e.button !== 0) return;
    const pt = eventDataPoint(e);
    if (!pt || !pt.inPrice) return;
    e.preventDefault();
    e.stopPropagation();
    downPos = {x:e.clientX, y:e.clientY, pointerId:e.pointerId};
    if (surface.setPointerCapture) surface.setPointerCapture(e.pointerId);
    renderGhost(pt);
  }, true);
  surface.addEventListener('pointerup', e => {
    if (!tool || !current || !downPos) return;
    e.preventDefault();
    e.stopPropagation();
    const wasClick = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y) < 8;
    const pointerId = downPos.pointerId;
    downPos = null;
    if (surface.releasePointerCapture && pointerId != null) {
      try { surface.releasePointerCapture(pointerId); } catch (_) {}
    }
    if (!wasClick) return;
    const pt = eventDataPoint(e);
    if (!pt || !pt.inPrice) { toast('click inside the price panel'); return; }
    handleToolClick(pt);
  }, true);
  surface.addEventListener('pointermove', e => {
    if (!tool || !current) return;
    renderGhost(eventDataPoint(e));
  }, true);
  surface.addEventListener('pointerleave', () => { if (tool && !downPos) clearGhost(); }, true);
  // right-click finishes the zigzag (and never opens the browser context menu while drawing)
  surface.addEventListener('contextmenu', e => {
    if (tool === 'zigzag') { e.preventDefault(); e.stopPropagation(); finishZigzag(); }
  }, true);
  bindAxisZone(document.getElementById('yzone'), 'y');
  bindAxisZone(document.getElementById('xzone'), 'x');
  new ResizeObserver(() => positionZones()).observe(document.getElementById('chartwrap'));
}
/* ---------- save ---------- */
function setupStructureParts(s) {
  if (!s || s.family !== 'structure_break') return null;
  const high = zigzagExtremeFrom(s.zigzag, 'high');
  const low = zigzagExtremeFrom(s.zigzag, 'low');
  if (!high || !low) return null;
  const anchor = {price:high.price, start_ms:high.ms, end_ms:levelAutoEndMs(high.price, high.ms), broken:false, virtual:true};
  return {high, low, anchor, entry: entryForLevel(anchor)};
}
function serializeSetup(s) {
  const lvl = s.level;
  const withLevel = !!lvl;
  const structure = setupStructureParts(s);
  const entryObj = withLevel ? entryForLevel(lvl) : (structure ? structure.entry : null);
  const stopPrice = s.slPrice != null ? s.slPrice : (structure ? structure.low.price : null);
  const slRay = (stopPrice != null && entryObj) ? slRayFor(entryObj, stopPrice) : null;
  const zpts = (s.zigzag && Array.isArray(s.zigzag.points)) ? s.zigzag.points : [];
  const hasPump = setupHasPump(s);
  return {
    family: s.family, quality: s.quality, notes: s.notes || '',
    has_level: withLevel,
    has_pump_transition: hasPump,
    has_structure_break: !!structure,
    level_price: withLevel ? lvl.price : null,
    level_start_ms: withLevel ? lvl.start_ms : null,
    level_end_ms: withLevel ? levelShapeEndMs(lvl) : null,
    level_broken: withLevel ? !!entryObj : null,
    pump_start_ms: hasPump ? s.pump.start.ms : null,
    pump_start_price: hasPump ? s.pump.start.price : null,
    culmination_ms: hasPump ? s.pump.high.ms : null,
    culmination_price: hasPump ? s.pump.high.price : null,
    structure_break_ms: structure ? structure.high.ms : null,
    structure_break_price: structure ? structure.high.price : null,
    structure_swing_low_ms: structure ? structure.low.ms : null,
    structure_swing_low_price: structure ? structure.low.price : null,
    entry_ms: entryObj ? entryObj.ms : null,
    entry_price: entryObj ? entryObj.price : null,
    entry_auto: entryObj ? true : null,
    entry_source: entryObj ? (withLevel ? 'level_break' : 'structure_break') : null,
    exit_ms: s.exitPoint ? s.exitPoint.ms : null,
    exit_price: s.exitPoint ? s.exitPoint.price : null,
    sl_price: slRay ? slRay.price : null,
    sl_ms: slRay ? entryObj.ms : null,
    sl_hit_ms: slRay ? slRay.hit_ms : null,
    sl_auto: slRay ? true : null,
    sl_source: slRay ? (structure && s.slPrice == null ? 'last_swing_low' : 'annotated') : null,
    zigzag_points: zpts.map(p => ({ms:Math.round(p.ms), price:Number(p.price)})),
  };
}
function baseLabel(serializedSetups) {
  const e = current.event;
  const group = current.group || visible[idx];
  return {
    event_id: group.event_id, symbol: e.symbol, tf: e.tf,
    source_event_id: e.event_id,
    source_event_ids: group.source_event_ids || [e.event_id],
    selected_tf: e.tf,
    has_level: serializedSetups.some(s => s.has_level),
    has_pump_transition: serializedSetups.some(s => s.has_pump_transition),
    setups: serializedSetups,
    source: 'browser_level_labeler',
  };
}
function labelSignature(payload) {
  return JSON.stringify(payload);
}
function updateCandidateLabel(eventId, label) {
  const match = candidates.find(x => x.event_id === eventId || (x.source_event_ids || []).includes(eventId));
  if (match) { match.labeled = true; match.label = label; }
  const group = visible.find(x => x.event_id === eventId || (x.source_event_ids || []).includes(eventId));
  if (group) { group.labeled = true; group.label = label; }
}
function currentLabelPayload(options={}) {
  if (resultMode || !current) return null;
  if (options.finishDraft && zzDraft) finishZigzag();
  commitActiveSetup();
  computeEntry();
  commitActiveSetup();
  const serialized = setups.map(serializeSetup);
  // Empty setup is meaningful: no pump drawn => no transition; no level drawn => no level.
  return baseLabel(serialized.length ? serialized : [serializeSetup(emptySetup())]);
}
async function sendLabelPayload(payload, {quiet=false}={}) {
  let j;
  try {
    const r = await fetch('/api/label', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
    j = await r.json();
  } catch (err) {
    if (!quiet) toast('save failed: request error', 3200);
    return {ok:false, error:'request error'};
  }
  if (!j.ok) {
    if (!quiet) toast('save failed: ' + (j.error || 'validation error'), 4200);
    return {ok:false, error:j.error || 'validation error'};
  }
  return {ok:true, label:j.label};
}
function queueLabelSave(payload, {advance=false, force=false, quiet=false, advanceSerial=null}={}) {
  const eventId = payload.event_id;
  const sig = labelSignature(payload);
  if (!force && savedSignatures.get(eventId) === sig) {
    if (advance) navigateAfterManualSave(eventId, advanceSerial);
    return Promise.resolve(true);
  }
  const task = async () => {
    const res = await sendLabelPayload(payload, {quiet});
    if (!res.ok) return false;
    savedSignatures.set(eventId, sig);
    updateCandidateLabel(eventId, res.label);
    renderList();
    updateEventButton();
    if (!quiet) toast('saved');
    if (advance) navigateAfterManualSave(eventId, advanceSerial);
    return true;
  };
  saveQueue = saveQueue.then(task, task);
  return saveQueue;
}
function scheduleAutosave() {
  if (resultMode || !current) return;
  if (autosaveTimer) clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(() => {
    autosaveTimer = null;
    flushAutosave({finishDraft:false});
  }, AUTOSAVE_DELAY_MS);
}
async function flushAutosave({finishDraft=false, quiet=true}={}) {
  if (autosaveTimer) { clearTimeout(autosaveTimer); autosaveTimer = null; }
  const payload = currentLabelPayload({finishDraft});
  if (autosaveTimer) { clearTimeout(autosaveTimer); autosaveTimer = null; }
  if (!payload) return true;
  return queueLabelSave(payload, {quiet, force:false});
}
async function unlabelEvent() {
  if (resultMode) { toast('result review mode'); return; }
  if (!visible.length) return;
  const group = visible[idx];
  if (!group || !group.labeled) { toast('event is already unlabeled'); return; }
  let j;
  try {
    const r = await fetch('/api/unlabel', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({event_id: group.event_id})});
    j = await r.json();
  } catch (err) {
    toast('unlabel failed: request error', 3200);
    return;
  }
  if (!j.ok) {
    toast('unlabel failed: ' + (j.error || 'validation error'), 4200);
    return;
  }
  const c = candidates.find(x => x.event_id === group.event_id);
  if (c) { c.labeled = false; c.label = null; }
  group.labeled = false;
  group.label = null;
  savedSignatures.delete(group.event_id);
  toast('event is unlabeled');
  renderList();
  const pos = findVisibleIndexByEventId(group.event_id);
  if (pos >= 0) loadEvent(pos);
}
function saveLabel() {
  const payload = currentLabelPayload({finishDraft:true});
  if (!payload) { toast('nothing to save'); return; }
  if (autosaveTimer) { clearTimeout(autosaveTimer); autosaveTimer = null; }
  queueLabelSave(payload, {advance:true, force:true, quiet:false, advanceSerial:navigationSerial});
}

/* ---------- navigation ---------- */
function findVisibleIndexByEventId(eventId) {
  return visible.findIndex(c => c.event_id === eventId || (c.source_event_ids || []).includes(eventId));
}
async function navigateToEventId(eventId) {
  if (!eventId) return;
  navigationSerial += 1;
  const ok = await flushAutosave({finishDraft:true, quiet:false});
  if (!ok) return;
  const pos = findVisibleIndexByEventId(eventId);
  if (pos >= 0) loadEvent(pos);
}
async function navigateByDelta(delta) {
  if (!visible.length) return;
  const target = visible[idx + delta];
  if (!target) return;
  await navigateToEventId(target.event_id);
}
function navigateAfterManualSave(savedEventId, advanceSerial) {
  if (advanceSerial !== navigationSerial) return;
  const pos = findVisibleIndexByEventId(savedEventId);
  if (pos >= 0 && pos < visible.length - 1) { loadEvent(pos + 1); return; }
  if (pos < 0) {
    const fallback = Math.min(idx, visible.length - 1);
    if (fallback >= 0) { loadEvent(fallback); return; }
  }
  toast('saved');
}
function nextEvent() { navigateByDelta(1); }
function prevEvent() { navigateByDelta(-1); }
async function nextUnlabeled() {
  const target = visible.find((c, i) => i > idx && !c.labeled) || visible.find(c => !c.labeled);
  if (target) await navigateToEventId(target.event_id);
  else toast('all visible candidates labeled');
}
function jumpKey(e) {
  if (e.key === 'Enter') {
    const n = Number(document.getElementById('jump').value);
    if (Number.isFinite(n) && visible[n - 1]) navigateToEventId(visible[n - 1].event_id);
  }
}
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'Delete' || e.key === 'Backspace') { deleteSelected(); return; }
  if (e.key === 'Escape') { cancelTool(); return; }
  const k = e.key.toLowerCase();
  if (k === 'l') toggleTool('level');
  if (k === 'p') toggleTool('pump');
  if (k === 'g') { if (tool === 'zigzag' && zzDraft && zzDraft.points.length) finishZigzag(); else toggleTool('zigzag'); }
  if (k === 'e') toggleTool('exit');
  if (k === 'x') setOptimalSl();
  if (k === 'z') resetAnnotations();
  if (e.key === '[') changeTfBy(-1);
  if (e.key === ']') changeTfBy(1);
  if (e.key === 'ArrowRight') nextEvent();
  if (e.key === 'ArrowLeft') prevEvent();
  if (k === 's') saveLabel();
  if (k === 'u') nextUnlabeled();
});
loadCandidates();
</script>
</body>
</html>
"""
