"""Minimal browser UI for visible-resistance annotation."""

BOUNDARY_LABELER_HTML = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Visible resistance desk</title>
  <script src="/plotly.min.js"></script>
  <style>
    :root {
      --bg:#101217; --panel:#171a20; --panel2:#20242c; --line:#2b3039;
      --text:#e8e5dc; --muted:#999b9a; --faint:#6f747d;
      --blue:#8aa0d8; --blue2:#222b45; --green:#7dbb91; --red:#d18495; --orange:#d5a36a;
    }
    * { box-sizing:border-box; scrollbar-width:thin; scrollbar-color:#555d6b #15171c; }
    body { margin:0; background:var(--bg); color:var(--text); font:13px Inter,Segoe UI,Arial,sans-serif; }
    button,input,textarea {
      border:0; border-radius:7px; background:var(--panel2); color:var(--text);
      box-shadow:inset 0 0 0 1px var(--line); padding:8px 10px; outline:none;
    }
    button { cursor:pointer; font-weight:700; }
    button:hover { background:#292e38; }
    button:disabled { opacity:.38; cursor:default; }
    button.primary { background:var(--blue); color:#101217; box-shadow:none; }
    button.good { color:var(--green); }
    button.warn { color:var(--orange); }
    button.bad { color:var(--red); }
    button.active { color:var(--blue); background:var(--blue2); box-shadow:inset 0 0 0 1px #52658f; }
    #top {
      height:58px; min-width:0; padding:0 14px; display:flex; align-items:center; justify-content:space-between;
      border-bottom:1px solid var(--line); background:rgba(16,18,23,.96);
    }
    .brand { min-width:0; font-weight:800; letter-spacing:-.02em; white-space:nowrap; }
    .brand small { color:var(--muted); font-weight:500; margin-left:10px; }
    .nav { min-width:0; display:flex; gap:6px; }
    #app { height:calc(100vh - 58px); display:grid; grid-template-columns:410px minmax(0,1fr); }
    #side { min-height:0; padding:12px; display:flex; flex-direction:column; gap:10px; border-right:1px solid var(--line); }
    .panel { background:var(--panel); border-radius:10px; padding:12px; }
    .heading { color:var(--blue); text-transform:uppercase; letter-spacing:.07em; font-size:11px; font-weight:800; }
    .help { color:var(--muted); font-size:11px; line-height:1.45; }
    .statusline { display:flex; justify-content:space-between; gap:8px; margin-top:7px; color:var(--muted); }
    .filters { display:grid; grid-template-columns:1fr 120px; gap:6px; }
    #list { min-height:120px; flex:1 1 auto; overflow:auto; display:flex; flex-direction:column; gap:5px; }
    .candidate { background:var(--panel); border-radius:7px; padding:8px 10px; cursor:pointer; color:var(--muted); }
    .candidate:hover { background:var(--panel2); }
    .candidate.active { background:var(--blue2); box-shadow:inset 2px 0 0 var(--blue); }
    .candidate.clear_boundary { box-shadow:inset 2px 0 0 var(--green); }
    .candidate.borderline { box-shadow:inset 2px 0 0 var(--orange); }
    .candidate.no_boundary { opacity:.55; box-shadow:inset 2px 0 0 var(--faint); }
    .candidate-main { display:flex; justify-content:space-between; color:var(--text); font-weight:750; }
    .candidate-meta { margin-top:4px; font-size:10px; color:var(--faint); display:flex; gap:7px; }
    .tfrow,.actions { display:flex; gap:5px; flex-wrap:wrap; }
    .tfrow button { min-width:48px; }
    .section { margin-top:10px; padding-top:10px; border-top:1px solid var(--line); }
    textarea { width:100%; height:58px; resize:vertical; margin-top:6px; }
    .reaction-list { max-height:100px; overflow:auto; margin:7px 0; }
    .reaction { display:flex; justify-content:space-between; gap:8px; padding:5px 7px; border-radius:6px; background:var(--panel2); margin-bottom:4px; font-size:11px; color:var(--muted); }
    .reaction button { width:22px; height:22px; padding:0; color:var(--red); }
    .metrics { display:grid; grid-template-columns:1fr 1fr; gap:5px; margin-top:8px; }
    .metric { padding:6px 7px; border-radius:6px; background:var(--panel2); color:var(--muted); font-size:10px; }
    .metric b { display:block; color:var(--text); margin-top:2px; font-size:12px; }
    #chartwrap { position:relative; min-width:0; min-height:0; }
    #chart { position:absolute; inset:0; }
    #overlay { position:absolute; inset:0; z-index:3; pointer-events:none; touch-action:none; }
    body.drawing #overlay { pointer-events:all; cursor:crosshair; }
    #chartTitle { position:absolute; right:18px; top:10px; z-index:4; color:var(--muted); pointer-events:none; }
    #toast { position:fixed; right:16px; bottom:16px; z-index:9; display:none; padding:10px 14px; border-radius:8px; background:#17241d; color:var(--green); }
    @media(max-width:900px){
      #app{grid-template-columns:minmax(300px,43vw) minmax(0,1fr)}
      .brand small{display:none}
      .nav button:last-child{font-size:0}
      .nav button:last-child::after{content:'Следующий';font-size:13px}
    }
  </style>
</head>
<body>
  <header id="top">
    <div class="brand">Visible resistance desk <small>только граница и реакции · без сделок</small></div>
    <div class="nav">
      <button onclick="previousEvent()">←</button>
      <button onclick="nextEvent()">→</button>
      <button onclick="nextUnlabeled()">Следующий неразмеченный</button>
    </div>
  </header>
  <main id="app">
    <aside id="side">
      <section class="panel">
        <div class="heading">Задача</div>
        <div class="help" style="margin-top:6px">
          Отметь независимые пары: тест верхней границы → завершившийся откат.
          График обрезан строго в момент снимка; будущих свечей нет.
        </div>
        <div class="help" id="coverage" style="margin-top:6px"></div>
        <div class="statusline"><span id="progress">0 / 0</span><span id="snapshot"></span></div>
      </section>
      <div class="filters">
        <input id="search" placeholder="монета" oninput="renderList()">
        <button id="filterBtn" onclick="cycleFilter()">все</button>
      </div>
      <div id="list"></div>
      <section class="panel" id="editor">
        <div class="heading">Разметка границы</div>
        <div class="tfrow" id="tfButtons" style="margin-top:8px"></div>
        <div class="actions" style="margin-top:8px">
          <button id="reactionTool" onclick="toggleReactionTool()">+ Реакция</button>
          <button onclick="discardChanges()">Отменить изменения</button>
          <button class="bad" id="unlabelBtn" onclick="unlabel()" disabled>Снять метку</button>
        </div>
        <div class="help" id="toolHint" style="margin-top:7px">
          Нажми «+ Реакция»: первый клик — high теста, второй — low отката.
        </div>
        <div class="reaction-list" id="reactionList"></div>
        <div class="metrics" id="metrics"></div>
        <div class="section">
          <div class="help">Комментарий: почему граница очевидна или почему это шум</div>
          <textarea id="comment" oninput="markDirty()"></textarea>
          <div class="actions" style="margin-top:8px">
            <button class="good" onclick="saveLabel('clear_boundary')">Хорошая граница</button>
            <button class="warn" onclick="saveLabel('borderline')">Сомнительная</button>
            <button class="bad" onclick="saveLabel('no_boundary')">Нет границы</button>
          </div>
        </div>
      </section>
    </aside>
    <section id="chartwrap">
      <div id="chart"></div>
      <svg id="overlay"></svg>
      <div id="chartTitle"></div>
    </section>
  </main>
  <div id="toast"></div>
<script>
let candidates=[], visible=[], currentIndex=-1, current=null, selectedTf='1h';
let reactions=[], savedLabel=null, pendingTouch=null, reactionTool=false, dirty=false, listFilter='all';
const TF_ORDER=['15m','30m','1h','4h'];

function esc(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
function fmtPrice(value){
  const v=Number(value); if(!Number.isFinite(v)) return '';
  const d=Math.abs(v)>=100?2:Math.abs(v)>=1?4:Math.abs(v)>=.01?5:7;
  return v.toFixed(d).replace(/\.?0+$/,'');
}
function iso(ms){return new Date(Number(ms)).toISOString().slice(0,16).replace('T',' ');}
function toast(message,ms=1800){
  const el=document.getElementById('toast'); el.textContent=message; el.style.display='block';
  clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.style.display='none',ms);
}
function markDirty(){dirty=true;}
function api(path){return path;}

async function loadCandidates(){
  const response=await fetch(api('/api/boundary/candidates')); const data=await response.json();
  if(data.error){toast(data.error,5000); return;}
  candidates=data.candidates||[];
  const manifest=data.manifest||{};
  if(manifest.available_cache_start){
    const full=manifest.full_calendar_year_available;
    document.getElementById('coverage').textContent=`Локальный кэш: ${String(manifest.available_cache_start).slice(0,10)} — ${String(manifest.available_cache_end).slice(0,10)}${full?'':' · январь–май отсутствуют'}`;
  }
  renderTfButtons(); renderList();
  if(candidates.length) await selectEvent(candidates[0].event_id,true);
}
function filteredCandidates(){
  const q=document.getElementById('search').value.trim().toLowerCase();
  return candidates.filter(row=>(!q||String(row.symbol).toLowerCase().includes(q)) &&
    (listFilter==='all'||(listFilter==='unlabeled'?!row.labeled:row.status===listFilter)));
}
function renderList(){
  visible=filteredCandidates(); const root=document.getElementById('list');
  root.innerHTML=visible.map(row=>`
    <div class="candidate ${row.status||''} ${current&&current.event.event_id===row.event_id?'active':''}" onclick="selectEvent('${esc(row.event_id)}')">
      <div class="candidate-main"><span>${esc(row.symbol)}</span><span>${row.status==='clear_boundary'?'граница':row.status==='borderline'?'сомн.':row.status==='no_boundary'?'нет':'—'}</span></div>
      <div class="candidate-meta"><span>${iso(row.snapshot_time_ms).slice(0,10)}</span><span>${esc(row.sampling_stratum)}</span><span>act ×${Number(row.activity_ratio||0).toFixed(1)}</span></div>
    </div>`).join('') || '<div class="help">Нет примеров для фильтра.</div>';
  const labeled=candidates.filter(row=>row.labeled).length;
  document.getElementById('progress').textContent=`${labeled} / ${candidates.length} размечено`;
}
function cycleFilter(){
  const order=['all','unlabeled','clear_boundary','borderline','no_boundary'];
  listFilter=order[(order.indexOf(listFilter)+1)%order.length];
  const names={all:'все',unlabeled:'неразмеченные',clear_boundary:'хорошие',borderline:'сомнительные',no_boundary:'без границы'};
  document.getElementById('filterBtn').textContent=names[listFilter]; renderList();
}
function renderTfButtons(){
  document.getElementById('tfButtons').innerHTML=TF_ORDER.map(tf=>
    `<button class="${selectedTf===tf?'active':''}" onclick="changeTf('${tf}')">${tf}</button>`).join('');
}
async function selectEvent(eventId,force=false){
  if(dirty&&!force){toast('Сначала сохрани или отмени изменения'); return;}
  const row=candidates.find(item=>item.event_id===eventId); if(!row)return;
  selectedTf=row.selected_tf||'1h'; await loadEvent(eventId);
}
async function loadEvent(eventId){
  const response=await fetch(api(`/api/boundary/candles?event_id=${encodeURIComponent(eventId)}&tf=${encodeURIComponent(selectedTf)}`));
  const data=await response.json(); if(data.error){toast(data.error,5000);return;}
  current=data; savedLabel=data.label||null;
  reactions=savedLabel&&Array.isArray(savedLabel.reactions)?savedLabel.reactions.map(item=>({...item})):[];
  document.getElementById('comment').value=savedLabel?(savedLabel.comment||''):'';
  pendingTouch=null; reactionTool=false; dirty=false;
  document.body.classList.remove('drawing');
  document.getElementById('reactionTool').classList.remove('active');
  document.getElementById('unlabelBtn').disabled=!savedLabel;
  document.getElementById('snapshot').textContent=`снимок ${iso(data.event.snapshot_time_ms)} UTC`;
  document.getElementById('chartTitle').textContent=`${data.event.symbol} · ${selectedTf} · только до снимка`;
  renderTfButtons(); renderList(); renderReactions(); draw();
}
async function changeTf(tf){
  if(tf===selectedTf)return;
  if(reactions.length||dirty){toast('Чтобы сменить TF, сначала сохрани или отмени разметку');return;}
  selectedTf=tf; await loadEvent(current.event.event_id);
}
function previousEvent(){navigate(-1);}
function nextEvent(){navigate(1);}
function navigate(delta){
  if(!current||dirty){if(dirty)toast('Сначала сохрани или отмени изменения');return;}
  const at=visible.findIndex(row=>row.event_id===current.event.event_id);
  const next=Math.max(0,Math.min(visible.length-1,at+delta));
  if(visible[next])selectEvent(visible[next].event_id);
}
function nextUnlabeled(){
  if(dirty){toast('Сначала сохрани или отмени изменения');return;}
  const row=candidates.find(item=>!item.labeled); if(row)selectEvent(row.event_id);
}
function toggleReactionTool(){
  if(!current)return;
  reactionTool=!reactionTool; pendingTouch=null;
  document.body.classList.toggle('drawing',reactionTool);
  document.getElementById('reactionTool').classList.toggle('active',reactionTool);
  document.getElementById('toolHint').textContent=reactionTool
    ? 'Кликни по high теста границы.'
    : 'Нажми «+ Реакция»: первый клик — high теста, второй — low отката.';
  clearGhost();
}
function discardChanges(){if(current)loadEvent(current.event.event_id);}
function deleteReaction(index){
  reactions.splice(index,1); dirty=true; renderReactions(); draw();
}
function renderReactions(){
  document.getElementById('reactionList').innerHTML=reactions.map((item,index)=>`
    <div class="reaction"><span>${index+1}. ${iso(item.touch_time_ms)} · ${fmtPrice(item.touch_price)} → ${fmtPrice(item.rejection_price)}</span>
    <button onclick="deleteReaction(${index})">×</button></div>`).join('');
  const metrics=localMetrics();
  document.getElementById('metrics').innerHTML=metrics?[
    ['реакций',metrics.count],['длительность',metrics.duration],
    ['толщина',metrics.thickness],['мин. откат',metrics.minReaction]
  ].map(([label,value])=>`<div class="metric">${label}<b>${value}</b></div>`).join(''):'';
}
function localMetrics(){
  if(!reactions.length)return null;
  const prices=reactions.map(item=>Number(item.touch_price));
  const lo=Math.min(...prices),hi=Math.max(...prices);
  const moves=reactions.map(item=>(item.touch_price-item.rejection_price)/item.touch_price);
  return {
    count:reactions.length,
    duration:((reactions.at(-1).touch_time_ms-reactions[0].touch_time_ms)/3600000).toFixed(1)+' ч',
    thickness:((hi/lo-1)*10000).toFixed(0)+' bps',
    minReaction:(100*Math.min(...moves)).toFixed(2)+'%'
  };
}
async function saveLabel(status){
  if(!current)return;
  const payload={
    event_id:current.event.event_id,tf:selectedTf,status,
    comment:document.getElementById('comment').value,
    reactions:status==='no_boundary'?[]:reactions
  };
  const response=await fetch(api('/api/boundary/label'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json(); if(!data.ok){toast(data.error||'Ошибка сохранения',4000);return;}
  dirty=false; if(status==='no_boundary')reactions=[];
  const row=candidates.find(item=>item.event_id===payload.event_id);
  if(row){row.labeled=true;row.status=status;row.selected_tf=selectedTf;}
  toast('Разметка сохранена'); await loadEvent(payload.event_id);
}
async function unlabel(){
  if(!current)return;
  const response=await fetch(api('/api/boundary/unlabel'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event_id:current.event.event_id})});
  const data=await response.json(); if(!data.ok){toast(data.error||'Ошибка',4000);return;}
  const row=candidates.find(item=>item.event_id===current.event.event_id);
  if(row){row.labeled=false;row.status=null;row.selected_tf=null;}
  toast('Метка снята'); await loadEvent(current.event.event_id);
}

function gd(){return document.getElementById('chart');}
function plotRefs(){const f=gd()._fullLayout;return f&&f.xaxis&&f.yaxis?{xa:f.xaxis,ya:f.yaxis}:null;}
function eventPoint(event){
  const refs=plotRefs();if(!refs)return null;const rect=gd().getBoundingClientRect();
  const px=event.clientX-rect.left,py=event.clientY-rect.top;
  const inside=px>=refs.xa._offset&&px<=refs.xa._offset+refs.xa._length&&py>=refs.ya._offset&&py<=refs.ya._offset+refs.ya._length;
  return {inside,ms:Number(refs.xa.p2l(px-refs.xa._offset)),price:refs.ya.l2c(refs.ya.p2l(py-refs.ya._offset)),px,py};
}
function nearestIndex(ms){
  const ts=current.candles.timestamp;let best=0,distance=Infinity;
  ts.forEach((value,index)=>{const d=Math.abs(value-ms);if(d<distance){best=index;distance=d;}});
  return best;
}
function xPx(ms){const r=plotRefs();return r.xa.l2p(Number(ms))+r.xa._offset;}
function yPx(price){const r=plotRefs();return r.ya.l2p(r.ya.d2c(price))+r.ya._offset;}
function overlayHitArea(){return '<rect width="100%" height="100%" fill="transparent" pointer-events="all"/>';}
function clearGhost(){document.getElementById('overlay').innerHTML=overlayHitArea();}
function ghost(event){
  if(!reactionTool||!current)return clearGhost();
  const point=eventPoint(event);if(!point||!point.inside)return clearGhost();
  const i=nearestIndex(point.ms),c=current.candles;
  const ms=c.timestamp[i],price=pendingTouch?c.low[i]:c.high[i],x=xPx(ms),y=yPx(price);
  const color=pendingTouch?'#d5a36a':'#8aa0d8';
  document.getElementById('overlay').innerHTML=`${overlayHitArea()}<circle cx="${x}" cy="${y}" r="4" fill="${color}"/><text x="${x+7}" y="${y-7}" fill="${color}" font-size="11">${pendingTouch?'low отката':'high теста'} ${fmtPrice(price)}</text>`;
}
function chartClick(event){
  if(!reactionTool||!current)return;
  const point=eventPoint(event);if(!point||!point.inside)return;
  const i=nearestIndex(point.ms),c=current.candles,ms=Number(c.timestamp[i]);
  if(ms>=Number(current.event.snapshot_time_ms)){toast('Свеча ещё не завершена в момент снимка');return;}
  if(!pendingTouch){
    const previous=reactions.at(-1);
    if(previous&&ms<=Number(previous.rejection_time_ms)){toast('Новый тест должен быть после предыдущего отката');return;}
    pendingTouch={touch_time_ms:ms,touch_price:Number(c.high[i])};
    document.getElementById('toolHint').textContent=`Тест ${iso(ms)} · ${fmtPrice(c.high[i])}. Теперь кликни по low завершившегося отката.`;
  }else{
    if(ms<=pendingTouch.touch_time_ms){toast(`Откат должен быть после теста ${iso(pendingTouch.touch_time_ms)}; выбрано ${iso(ms)}`);return;}
    const rejectionPrice=Number(c.low[i]);
    if(rejectionPrice>=pendingTouch.touch_price){toast('Откат должен уйти ниже теста');return;}
    reactions.push({...pendingTouch,rejection_time_ms:ms,rejection_price:rejectionPrice});
    pendingTouch=null;reactionTool=false;dirty=true;
    document.body.classList.remove('drawing');document.getElementById('reactionTool').classList.remove('active');
    document.getElementById('toolHint').textContent='Реакция добавлена. Добавь следующую независимую реакцию.';
    renderReactions();draw();
  }
  clearGhost();
}
function chartShapes(){
  if(!reactions.length)return[];
  const prices=reactions.map(item=>Number(item.touch_price)),lo=Math.min(...prices),hi=Math.max(...prices);
  const mid=(lo+hi)/2,pad=Math.max((hi-lo)*.5,mid*.001);
  const start=reactions[0].touch_time_ms,end=current.event.snapshot_time_ms;
  return[
    {type:'rect',layer:'below',x0:new Date(start),x1:new Date(end),y0:lo-pad,y1:hi+pad,line:{color:'#8aa0d8',width:1},fillcolor:'rgba(138,160,216,.10)'},
    {type:'line',x0:new Date(start),x1:new Date(end),y0:mid,y1:mid,line:{color:'#8aa0d8',width:1.5,dash:'dash'}}
  ];
}
function reactionTrace(){
  const x=[],y=[];
  reactions.forEach(item=>{x.push(new Date(item.touch_time_ms),new Date(item.rejection_time_ms),null);y.push(item.touch_price,item.rejection_price,null);});
  return {type:'scatter',mode:'lines+markers',x,y,yaxis:'y',line:{color:'#d5a36a',width:1.4},marker:{size:7,color:['#8aa0d8','#d5a36a']},hoverinfo:'none'};
}
function chartAnnotations(){
  return reactions.flatMap((item,index)=>[
    {x:new Date(item.touch_time_ms),y:item.touch_price,text:`T${index+1}`,showarrow:true,ax:0,ay:-22,arrowcolor:'#8aa0d8',font:{color:'#b8c5e8',size:10}},
    {x:new Date(item.rejection_time_ms),y:item.rejection_price,text:`R${index+1}`,showarrow:true,ax:0,ay:22,arrowcolor:'#d5a36a',font:{color:'#e7bc8c',size:10}}
  ]);
}
function draw(){
  if(!current)return;const c=current.candles,x=c.timestamp.map(ms=>new Date(ms));
  const candle={type:'candlestick',x,open:c.open,high:c.high,low:c.low,close:c.close,xaxis:'x',yaxis:'y',
    increasing:{line:{color:'#7dbb91'},fillcolor:'rgba(125,187,145,.42)'},decreasing:{line:{color:'#c99a62'},fillcolor:'rgba(201,154,98,.40)'},hoverinfo:'none'};
  const volume={type:'bar',x,y:c.quote_volume,xaxis:'x',yaxis:'y2',marker:{color:'#667087'},opacity:.32,hoverinfo:'none'};
  const traces=[candle,volume];if(reactions.length)traces.push(reactionTrace());
  Plotly.react(gd(),traces,{
    paper_bgcolor:'#101217',plot_bgcolor:'#171a20',font:{color:'#e8e5dc'},showlegend:false,dragmode:'pan',hovermode:false,
    margin:{l:58,r:25,t:38,b:38},xaxis:{rangeslider:{visible:false},gridcolor:'#252a32',linecolor:'#2b3039'},
    yaxis:{domain:[.23,1],gridcolor:'#252a32',linecolor:'#2b3039',title:'price'},
    yaxis2:{domain:[0,.16],gridcolor:'#252a32',linecolor:'#2b3039',title:'volume',fixedrange:true},
    shapes:chartShapes(),annotations:chartAnnotations()
  },{responsive:true,displayModeBar:false,scrollZoom:true});
}
const overlay=document.getElementById('overlay');
overlay.addEventListener('pointermove',ghost);
overlay.addEventListener('pointerleave',clearGhost);
overlay.addEventListener('click',chartClick);
window.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&reactionTool)toggleReactionTool();
  if(event.key==='ArrowLeft')previousEvent();
  if(event.key==='ArrowRight')nextEvent();
});
loadCandidates();
</script>
</body>
</html>
"""
