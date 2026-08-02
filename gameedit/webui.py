"""폰 브라우저에서 열리는 화면 (HTML 한 장).

앱 설치 없이 아이폰·안드로이드 모두에서 쓰려고 순수 HTML/CSS/JS 로 만들었다.
외부 라이브러리를 전혀 쓰지 않아서 인터넷 없이 집 와이파이만으로 동작한다.
"""

PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d1017">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>게임 하이라이트 편집기</title>
<style>
:root{
  --bg:#0d1017; --card:#161b26; --line:#232a38; --text:#e6e9ef; --dim:#8b95a8;
  --accent:#58a6ff; --accent2:#1f6feb; --good:#3fb950; --warn:#d29922; --bad:#f85149;
  color-scheme: dark;
}
*{box-sizing:border-box; -webkit-tap-highlight-color:transparent}
body{
  margin:0; background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Pretendard',
              'Noto Sans KR',system-ui,sans-serif;
  padding:0 0 calc(88px + env(safe-area-inset-bottom));
  -webkit-text-size-adjust:100%;
}
header{
  position:sticky; top:0; z-index:20; background:rgba(13,16,23,.94);
  backdrop-filter:blur(12px); border-bottom:1px solid var(--line);
  padding:calc(14px + env(safe-area-inset-top)) 18px 14px;
  display:flex; align-items:center; gap:10px;
}
header h1{font-size:17px; margin:0; flex:1}
header button{background:none;border:none;color:var(--accent);font-size:15px;padding:6px}
main{padding:18px}
.view{display:none} .view.on{display:block}
h2{font-size:15px; color:var(--dim); margin:26px 0 10px; font-weight:600}
h2:first-child{margin-top:6px}
.card{background:var(--card); border:1px solid var(--line); border-radius:14px;
      padding:16px; margin-bottom:12px}
.btn{
  display:flex; align-items:center; justify-content:center; gap:8px; width:100%;
  padding:17px; border-radius:14px; border:none; background:var(--accent2); color:#fff;
  font-size:17px; font-weight:600; font-family:inherit; cursor:pointer;
}
.btn:disabled{opacity:.45}
.btn.ghost{background:var(--card); border:1px solid var(--line); color:var(--text)}
.btn.danger{background:#3a1d1d; color:#ff9b95; border:1px solid #5a2a2a}
.btn+.btn{margin-top:10px}
.chips{display:flex; gap:8px; flex-wrap:wrap}
.chip{
  flex:1; min-width:72px; text-align:center; padding:14px 10px; border-radius:12px;
  background:var(--card); border:1px solid var(--line); color:var(--dim); font-size:15px;
}
.chip.on{background:rgba(88,166,255,.16); border-color:var(--accent); color:var(--accent)}
.trash{flex:none; padding:10px 12px; font-size:19px; opacity:.55; border-radius:10px}
.trash:active{background:#3a1d1d; opacity:1}
.row{display:flex; align-items:center; justify-content:space-between; gap:12px;
     padding:14px 0; border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:none}
.row .label{font-size:15px}
.row .sub{font-size:12px; color:var(--dim); margin-top:3px}
.switch{width:52px; height:31px; border-radius:99px; background:#2b3242; position:relative;
        flex:none; transition:background .15s}
.switch.on{background:var(--good)}
.switch i{position:absolute; top:3px; left:3px; width:25px; height:25px; border-radius:50%;
          background:#fff; transition:transform .15s}
.switch.on i{transform:translateX(21px)}
.bar{height:8px; border-radius:99px; background:#232a38; overflow:hidden; margin:12px 0 8px}
.bar i{display:block; height:100%; background:linear-gradient(90deg,var(--accent2),var(--accent));
       width:0; transition:width .4s}
.muted{color:var(--dim); font-size:13px; line-height:1.6}
.job{display:flex; gap:12px; align-items:center; padding:14px 0; border-bottom:1px solid var(--line)}
.job:last-child{border-bottom:none}
.job .dot{width:10px; height:10px; border-radius:50%; flex:none; background:var(--dim)}
.dot.running{background:var(--warn); animation:pulse 1.2s infinite}
.dot.done{background:var(--good)} .dot.error{background:var(--bad)}
@keyframes pulse{50%{opacity:.3}}
.job .grow{flex:1; min-width:0}
.job .name{font-size:15px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
.clip{display:flex; gap:12px; align-items:center; padding:10px 0;
      border-bottom:1px solid var(--line)}
.clip img{width:104px; height:59px; border-radius:8px; object-fit:cover; background:#0a0d13; flex:none}
.clip .grow{flex:1; min-width:0}
.clip .t{font-size:14px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis}
.clip .m{font-size:12px; color:var(--dim); margin-top:4px}
.clip.off{opacity:.35}
.check{width:30px; height:30px; border-radius:9px; border:2px solid var(--line); flex:none;
       display:flex; align-items:center; justify-content:center; font-size:16px; color:transparent}
.check.on{background:var(--accent2); border-color:var(--accent2); color:#fff}
textarea,input[type=text]{
  width:100%; background:#0f131b; color:var(--text); border:1px solid var(--line);
  border-radius:10px; padding:12px; font-size:16px; font-family:inherit; resize:vertical;
}
video{width:100%; border-radius:12px; background:#000; margin-top:4px}
.log{background:#0a0d13; border-radius:10px; padding:12px; font-size:11px; color:var(--dim);
     max-height:190px; overflow:auto; white-space:pre-wrap; line-height:1.55;
     font-family:ui-monospace,Menlo,monospace}
.bottom{position:fixed; left:0; right:0; bottom:0; z-index:30; padding:12px 18px
        calc(12px + env(safe-area-inset-bottom)); background:rgba(13,16,23,.96);
        backdrop-filter:blur(12px); border-top:1px solid var(--line)}
.toast{position:fixed; left:50%; bottom:96px; transform:translateX(-50%); z-index:50;
       background:#1f2633; border:1px solid var(--line); border-radius:12px; padding:12px 18px;
       font-size:14px; opacity:0; pointer-events:none; transition:opacity .25s; max-width:88%}
.toast.on{opacity:1}
.empty{text-align:center; color:var(--dim); padding:34px 10px; font-size:14px; line-height:1.7}
</style>
</head>
<body>

<header>
  <button id="back" style="display:none" onclick="go('home')">‹ 뒤로</button>
  <h1 id="title">🎮 하이라이트 편집기</h1>
  <button onclick="refresh()">새로고침</button>
</header>

<main>
  <!-- ============ 홈 ============ -->
  <section id="v-home" class="view on">
    <h2>새로 편집하기</h2>
    <div class="card">
      <input type="file" id="file" accept="video/*" style="display:none" onchange="pick(this)">
      <button class="btn" onclick="document.getElementById('file').click()">
        📤 폰에서 영상 올리기
      </button>
      <div id="up" style="display:none">
        <div class="bar"><i id="upbar"></i></div>
        <div class="muted" id="uptext">올리는 중…</div>
      </div>
      <div class="muted" style="margin-top:12px">
        {{DEVICE}}에 이미 있는 영상은 아래 목록에서 고르세요. 올릴 필요가 없어 훨씬 빠릅니다.
      </div>
    </div>

    <h2>{{DEVICE}}에 있는 영상</h2>
    <div class="card" id="files"><div class="empty">불러오는 중…</div></div>

    <h2>작업 목록</h2>
    <div class="card" id="jobs"><div class="empty">아직 편집한 영상이 없습니다</div></div>
  </section>

  <!-- ============ 옵션 ============ -->
  <section id="v-opt" class="view">
    <div class="card">
      <div class="label" id="opt-name" style="font-size:15px"></div>
      <div class="sub muted" id="opt-size" style="margin-top:4px"></div>
    </div>

    <h2>완성본 길이</h2>
    <div class="chips" id="lens"></div>

    <h2>편집 스타일</h2>
    <div class="chips" id="styles"></div>
    <div class="muted" id="style-hint" style="margin:6px 2px 0"></div>

    <h2>편집 강도</h2>
    <div class="chips" id="paces"></div>
    <div class="muted" id="pace-hint" style="margin:6px 2px 0"></div>

    <h2>요구사항 (직접 적기)</h2>
    <div class="card">
      <textarea id="wishes" rows="3" oninput="checkWishes()"
        placeholder="예) 3분으로, 죽는 장면 위주로, 자막 크게, 포켓몬 타입 알려줘"></textarea>
      <div class="muted" id="wish-out" style="margin-top:10px">
        적은 대로 알아들었는지 여기에 바로 보여 드립니다.
      </div>
    </div>

    <h2>편집 옵션</h2>
    <div class="card">
      <div class="row">
        <div><div class="label">밈 넣기</div>
             <div class="sub">대사·상황에 맞는 자막 밈</div></div>
        <div class="switch on" id="sw-meme" onclick="toggle(this)"><i></i></div>
      </div>
      <div class="row">
        <div style="flex:1">
          <div class="label">만들어 둔 자막 넣기</div>
          <div class="sub" id="subs-name">유튜브 자동자막·클로바노트 등에서 받은 .srt</div>
        </div>
        <input type="file" id="subsfile" accept=".srt,.vtt" style="display:none"
               onchange="pickSubs(this)">
        <div class="chip" style="flex:none;min-width:0;padding:10px 14px"
             onclick="document.getElementById('subsfile').click()">파일</div>
      </div>
      <div class="row">
        <div><div class="label">대사 자막</div>
             <div class="sub">음성 인식이 설치돼 있을 때만</div></div>
        <div class="switch on" id="sw-sub" onclick="toggle(this)"><i></i></div>
      </div>
      <div class="row">
        <div><div class="label">쇼츠(세로) 로 만들기</div>
             <div class="sub">1080x1920 세로 영상</div></div>
        <div class="switch" id="sw-shorts" onclick="toggleShorts(this)"><i></i></div>
      </div>
      <div id="shorts-extra" style="display:none">
        <div class="sub muted" style="margin:4px 2px 10px">
          가로 영상을 세로로 만들면 위아래가 빕니다. 그 자리에 넣을 글입니다.
          비워 두면 흐린 배경만 깔립니다.
        </div>
        <input id="shorts-title" placeholder="위에 넣을 제목  예) 로토무 한 마리로 끝냄"
               maxlength="40" oninput="state.shortsTitle=this.value">
        <input id="shorts-channel" placeholder="아래에 넣을 채널명  예) @내채널"
               maxlength="24" style="margin-top:8px"
               oninput="state.channel=this.value">
      </div>
    </div>

    <h2>얼마나 걸릴까</h2>
    <div class="card">
      <div class="row">
        <div style="flex:1">
          <div class="label">자막 만드는 시간 재보기</div>
          <div class="sub" id="speed-sub">짧은 조각을 실제로 돌려서 이 기기 속도를 잽니다 (30초쯤)</div>
        </div>
        <div class="chip" id="speed-btn" style="flex:none;min-width:0;padding:10px 14px"
             onclick="runSpeedtest()">재보기</div>
      </div>
      <div class="muted" id="speed-out" style="margin-top:10px;display:none"></div>
    </div>

    <div class="muted">
      편집이 시작되면 <b>이 화면(크롬)을 닫아도 계속 만듭니다.</b>
      Termux 만 켜 두면 됩니다.<br>
      기기가 자꾸 죽는다면 시작한 뒤 크롬을 닫아 두세요 — 이 화면이
      메모리를 수백 MB 씁니다.
    </div>
  </section>

  <!-- ============ 작업 상세 ============ -->
  <section id="v-job" class="view">
    <div class="card">
      <div id="job-step" style="font-size:16px">준비 중…</div>
      <div class="bar"><i id="job-bar"></i></div>
      <div class="muted" id="job-sum"></div>
    </div>
    <div id="job-retry" style="display:none">
      <div class="card">
        <div class="label">중간에 멈췄습니다</div>
        <div class="sub">이미 만들어 둔 조각은 그대로 두고 <b>멈춘 데부터 이어서</b>
             만듭니다. 처음부터 다시 하지 않습니다.</div>
      </div>
      <button class="btn" onclick="resume()">▶️ 이어서 만들기</button>
    </div>
    <div id="job-result" style="display:none">
      <h2>미리보기</h2>
      <video id="job-video" controls playsinline preload="metadata"></video>
      <div style="margin-top:12px">
        <button class="btn" onclick="download()">⬇️ 폰에 저장하기</button>
        <button class="btn ghost" onclick="openEdit()">✂️ 클립·자막 고치기</button>
      </div>
    </div>
    <h2>진행 기록</h2>
    <div class="log" id="job-log"></div>
  </section>

  <!-- ============ 편집 ============ -->
  <section id="v-edit" class="view">
    <div class="card">
      <div class="muted">빼고 싶은 장면을 체크 해제하세요. 자막은 눌러서 고칠 수 있습니다.</div>
    </div>
    <h2>하이라이트 <span id="clip-count" class="muted"></span></h2>
    <div class="card" id="clips"></div>
    <h2>자막</h2>
    <div class="card" id="subs"></div>
  </section>
</main>

<div class="bottom" id="bottom" style="display:none">
  <button class="btn" id="action" onclick="action()">시작</button>
</div>
<div class="toast" id="toast"></div>

<script>
const KEY = (new URLSearchParams(location.search).get('k')) ||
            localStorage.getItem('gameedit_key') || '';
if (KEY) localStorage.setItem('gameedit_key', KEY);

let state = {view:'home', file:null, job:null, plan:null,
             removed:new Set(), edits:{}, target:10, timer:null};

const $ = (id) => document.getElementById(id);

function toast(msg){
  const t = $('toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(t._h); t._h = setTimeout(()=>t.classList.remove('on'), 2600);
}

async function api(path, opts={}){
  opts.headers = Object.assign({'X-Key':KEY}, opts.headers||{});
  const res = await fetch(path, opts);
  if (res.status === 401){ toast('접속 번호가 필요합니다. 컴퓨터 화면의 주소로 다시 접속하세요'); throw 0; }
  const data = await res.json().catch(()=>({}));
  if (!res.ok) throw new Error(data.error || '오류가 발생했습니다');
  return data;
}

function go(view){
  state.view = view;
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('on'));
  $('v-'+view).classList.add('on');
  $('back').style.display = view === 'home' ? 'none' : 'block';
  const titles = {home:'🎮 하이라이트 편집기', opt:'편집 설정', job:'편집 진행', edit:'다시 고르기'};
  $('title').textContent = titles[view];
  const bottom = {opt:'✨ 편집 시작', edit:'🔄 이대로 다시 만들기'};
  if (bottom[view]){ $('bottom').style.display='block'; $('action').textContent = bottom[view]; }
  else $('bottom').style.display='none';
  window.scrollTo(0,0);
  if (view === 'home') refresh();
}
$('back').onclick = () => go(state.view === 'edit' ? 'job' : 'home');

function toggle(el){ el.classList.toggle('on'); }
function toggleShorts(el){
  toggle(el);
  $('shorts-extra').style.display = el.classList.contains('on') ? 'block' : 'none';
}

/* ---------------- 업로드 ---------------- */
function pick(input){
  const file = input.files[0];
  if (!file) return;
  $('up').style.display = 'block';
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.setRequestHeader('X-Key', KEY);
  xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = e.loaded / e.total * 100;
    $('upbar').style.width = pct + '%';
    $('uptext').textContent = `올리는 중… ${pct.toFixed(0)}% ` +
      `(${(e.loaded/1048576).toFixed(0)} / ${(e.total/1048576).toFixed(0)} MB)`;
  };
  xhr.onload = () => {
    $('up').style.display = 'none'; $('upbar').style.width = '0';
    input.value = '';
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch(e){}
    if (xhr.status >= 400){ toast(data.error || '업로드 실패'); return; }
    openOptions({path:data.path, name:data.name, size_mb:(file.size/1048576).toFixed(0)});
  };
  xhr.onerror = () => { $('up').style.display='none'; toast('업로드 중 연결이 끊겼습니다'); };
  xhr.send(file);
}

/* ---------------- 목록 ---------------- */
async function refresh(){
  try {
    const {files} = await api('/api/files');
    state.fileList = files;
    const total = files.reduce((s,f) => s + (f.size_mb||0), 0);
    $('files').innerHTML = files.length ? files.map((f,i) => `
      <div class="row">
        <div class="grow" onclick="openOptions(state.fileList[${i}])">
          <div class="label">🎬 ${esc(f.name)}</div>
          <div class="sub">${f.duration ? fmtDuration(f.duration)+' · ' : ''}${f.size_mb} MB</div>
        </div>
        <div class="trash" onclick="delFile(${i})">🗑</div>
      </div>`).join('') +
      `<div class="sub muted" style="padding-top:12px">전부 ${(total/1024).toFixed(1)} GB 차지하고 있습니다</div>` :
      '<div class="empty">{{DEVICE}}에 저장된 영상이 여기에 나타납니다</div>';

    const {jobs} = await api('/api/jobs');
    $('jobs').innerHTML = jobs.length ? jobs.map(j => `
      <div class="job" onclick="openJob('${j.id}')">
        <div class="dot ${j.status}"></div>
        <div class="grow"><div class="name">${esc(j.title)}</div>
        <div class="sub muted">${statusText(j)}</div></div>
        <div class="muted">›</div>
      </div>`).join('') : '<div class="empty">아직 편집한 영상이 없습니다</div>';
  } catch(e){}
}

function statusText(j){
  if (j.status === 'done') return `완료 · ${j.summary.duration_text||''} · 하이라이트 ${j.summary.clips||0}개`;
  if (j.status === 'error') return '실패: ' + (j.error||'');
  return `${j.step} · ${Math.round(j.progress*100)}%`;
}
const esc = (s) => String(s||'').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

/* ---------------- 옵션 ---------------- */
const ALL_LENGTHS = [3,5,10,15,20];
/* 5분짜리 원본으로 10분 완성본은 못 만든다. 원본보다 긴 선택지는 숨긴다. */
function lengthsFor(file){
  const mins = (file && file.duration) ? file.duration / 60 : 0;
  if (!mins) return ALL_LENGTHS;
  const fits = ALL_LENGTHS.filter(n => n <= mins * 0.9);
  return fits.length ? fits : [ALL_LENGTHS[0]];
}
function fmtDuration(sec){
  if (!sec) return '';
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}분 ${String(s).padStart(2,'0')}초`;
}
/* 실제 편집본 캡처로 확인한 스타일만 둔다. 근거 없는 건 넣지 않는다.
   여기 목록과 서버의 styles.py 가 어긋나면 골라도 아무 일이 안 일어난다. */
const STYLES = [
  ['','없음','기본 설정으로 편집합니다'],
  ['anmori','안모리','자막 크게·굵게, 강조는 노랑/빨강, 2단 자막'],
];
const PACES = [
  ['loose','여유', '말 사이 호흡을 남깁니다. 토크가 많은 영상용'],
  ['normal','기본', '죽은 시간만 잘라냅니다'],
  ['fast','빠르게', '숨 쉴 틈 없이 촘촘하게. 밈도 많이 들어갑니다'],
];
function openOptions(file){
  state.file = file;
  state.pace = state.pace || 'normal';
  $('opt-name').textContent = '🎬 ' + file.name;
  const bits = [];
  if (file.duration) bits.push('원본 ' + fmtDuration(file.duration));
  if (file.size_mb) bits.push(file.size_mb + ' MB');
  $('opt-size').textContent = bits.join(' · ');

  state.lengths = lengthsFor(file);
  if (!state.lengths.includes(state.target)) state.target = state.lengths[0];
  $('lens').innerHTML = state.lengths.map(n =>
    `<div class="chip ${n===state.target?'on':''}" onclick="setLen(${n})">${n}분</div>`).join('');
  state.subs = '';
  state.wishes = '';
  if ($('wishes')) $('wishes').value = '';
  resetSpeed();          // 다른 영상을 골랐는데 지난 결과가 남아 있으면 오해한다
  state.shortsTitle = state.shortsTitle || '';
  state.channel = state.channel || '';
  if ($('shorts-title')) $('shorts-title').value = state.shortsTitle;
  if ($('shorts-channel')) $('shorts-channel').value = state.channel;
  $('shorts-extra').style.display =
    $('sw-shorts').classList.contains('on') ? 'block' : 'none';
  $('subs-name').textContent = '유튜브 자동자막·클로바노트 등에서 받은 .srt';
  state.style = state.style || '';
  $('styles').innerHTML = STYLES.map(([id,name]) =>
    `<div class="chip ${id===state.style?'on':''}" onclick="setStyle('${id}')">${name}</div>`).join('');
  setStyle(state.style);
  $('paces').innerHTML = PACES.map(([id,name]) =>
    `<div class="chip ${id===state.pace?'on':''}" onclick="setPace('${id}')">${name}</div>`).join('');
  setPace(state.pace);
  go('opt');
}
function setLen(n){
  state.target = n;
  const list = state.lengths || ALL_LENGTHS;
  document.querySelectorAll('#lens .chip').forEach((c,i) =>
    c.classList.toggle('on', list[i]===n));
}
function setStyle(id){
  state.style = id;
  document.querySelectorAll('#styles .chip').forEach((c,i) =>
    c.classList.toggle('on', STYLES[i][0]===id));
  const hit = STYLES.find(s => s[0]===id);
  $('style-hint').textContent = hit ? hit[2] : '';
}
function setPace(id){
  state.pace = id;
  document.querySelectorAll('#paces .chip').forEach((c,i) =>
    c.classList.toggle('on', PACES[i][0]===id));
  const hit = PACES.find(p => p[0]===id);
  $('pace-hint').textContent = hit ? hit[2] : '';
}

async function action(){
  if (state.view === 'opt') return startJob();
  if (state.view === 'edit') return replan();
}

let wishTimer = null;
function checkWishes(){
  clearTimeout(wishTimer);
  wishTimer = setTimeout(async () => {
    const text = $('wishes').value.trim();
    state.wishes = text;
    if (!text){ $('wish-out').textContent = '적은 대로 알아들었는지 여기에 바로 보여 드립니다.'; return; }
    try{
      const r = await api('/api/wishes/check', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({wishes: text})
      });
      const ok = (r.matched||[]).map(m => `✅ ${esc(m)}`).join('<br>');
      const no = (r.ignored||[]).map(m => `❓ <b>${esc(m)}</b> — 이 말은 못 알아들어서 그냥 넘어갑니다`).join('<br>');
      $('wish-out').innerHTML = [ok, no].filter(Boolean).join('<br>')
        || '❓ 알아들은 게 없습니다. 아래 예시처럼 적어 보세요.';
    } catch(e){ /* 서버가 잠깐 안 받아도 입력은 계속 가능해야 한다 */ }
  }, 400);
}

// ---------------------------------------------------------- 속도 재보기
// 기기마다 열 배씩 차이나서 추정값은 의미가 없다. 짧은 조각을 실제로 돌린다.
let speedTimer = null;

function resetSpeed(){
  clearInterval(speedTimer); speedTimer = null;
  $('speed-btn').textContent = '재보기';
  $('speed-out').style.display = 'none';
  $('speed-out').innerHTML = '';
}

function fmtWait(sec){
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + '초';
  const m = Math.round(sec / 60);
  if (m < 60) return m + '분';
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h}시간 ${r}분` : `${h}시간`;
}

function showSpeed(r){
  const out = $('speed-out');
  out.style.display = 'block';
  if (!r.ok){
    out.innerHTML = `❌ ${esc(r.error || '재지 못했습니다')}`;
    return;
  }
  const rows = [];
  if (r.predicted_seconds > 0)
    rows.push(`<b>이 영상: 약 ${fmtWait(r.predicted_seconds)}</b>`);
  rows.push(`1시간짜리면 약 ${fmtWait(r.predicted_hour_seconds)}`);
  rows.push(`오디오 1분당 ${Math.round(r.seconds_per_minute)}초 · ${esc(r.model || r.backend)}`);
  if (r.memory_verdict && r.memory_verdict !== 'ok')
    rows.push(`⚠ ${esc(r.memory_note)}`);
  if (r.no_speech)
    rows.push('<br>⚠ 잰 구간에 말이 없어서 인식기가 아무 말이나 지어냈습니다.<br>' +
              '위 속도도 실제보다 빠르게 나온 값입니다. 말이 있는 영상으로 다시 재보세요.');
  else if (r.sample_text)
    rows.push(`<br>인식된 대사 — 맞는지 직접 보세요<br><i>${esc(r.sample_text)}</i>`);
  out.innerHTML = rows.join('<br>');
}

async function runSpeedtest(){
  if (!state.file) return;
  if (speedTimer) return;                 // 이미 재는 중
  $('speed-btn').textContent = '재는 중…';
  $('speed-out').style.display = 'block';
  $('speed-out').textContent = '짧은 조각 두 개를 돌려 보는 중… (30초쯤)';
  try{
    await api('/api/speedtest', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({path: state.file.path})
    });
  } catch(e){
    resetSpeed(); toast('재보기를 시작하지 못했습니다'); return;
  }
  speedTimer = setInterval(async () => {
    let s;
    try{ s = await api('/api/speedtest'); } catch(e){ return; }
    if (s.running) return;
    clearInterval(speedTimer); speedTimer = null;
    $('speed-btn').textContent = '다시 재기';
    if (s.report) showSpeed(s.report);
    else { $('speed-out').textContent = '결과를 받지 못했습니다.'; }
  }, 2000);
}

async function pickSubs(input){
  const f = input.files[0];
  if (!f) return;
  try{
    const r = await fetch('/api/upload-subs?k=' + encodeURIComponent(state.key||''), {
      method:'POST', headers:{'X-Filename': encodeURIComponent(f.name)}, body: f
    }).then(r => r.json());
    if (r.error) throw new Error(r.error);
    state.subs = r.path;
    $('subs-name').textContent = '✅ ' + r.name + ' — 음성 인식을 건너뜁니다';
  } catch(e){ toast('자막 올리기 실패: ' + e.message); }
}

async function delFile(i){
  const f = (state.fileList||[])[i];
  if (!f) return;
  const size = f.size_mb >= 1024 ? (f.size_mb/1024).toFixed(1)+' GB' : f.size_mb+' MB';
  if (!confirm(`${f.name}\n${size}\n\n이 영상을 폰에서 지웁니다.\n되돌릴 수 없습니다. 지울까요?`)) return;
  try{
    const r = await api('/api/files/delete', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({path: f.path})
    });
    toast(`${r.name} 지웠습니다 (${r.freed_mb} MB 확보)`);
    refresh();
  } catch(e){ if (e) toast(e.message); }
}

async function startJob(){
  $('action').disabled = true;
  try{
    const job = await api('/api/jobs', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        path: state.file.path,
        target_duration: state.target * 60,
        no_memes: !$('sw-meme').classList.contains('on'),
        no_subtitles: !$('sw-sub').classList.contains('on'),
        shorts: $('sw-shorts').classList.contains('on'),
        shorts_title: (state.shortsTitle||'').slice(0,40),
        channel: (state.channel||'').slice(0,24),
        pace: state.pace || 'normal',
        style: state.style || '',
        subs: state.subs || '',
        wishes: state.wishes || '',
      })
    });
    openJob(job.id);
  } catch(e){ if (e) toast(e.message); }
  $('action').disabled = false;
}

/* ---------------- 작업 상세 ---------------- */
function openJob(id){
  state.job = id;
  $('job-result').style.display = 'none';
  go('job');
  poll();
}

async function poll(){
  clearTimeout(state.timer);
  if (state.view !== 'job' || !state.job) return;
  try{
    const j = await api('/api/jobs/' + state.job);
    $('job-step').textContent =
      j.status==='done' ? '✅ 완성됐습니다' :
      j.status==='error' ? '❌ ' + (j.error||'실패') : j.step;
    $('job-bar').style.width = (j.progress*100) + '%';
    $('job-retry').style.display = j.status==='error' ? 'block' : 'none';
    if (j.status === 'done' && j.summary.fallback)
      $('job-step').textContent = '✅ 완성 (신호가 약해 균등 간격으로 잘랐습니다)';
    $('job-sum').textContent = j.status==='done' && j.summary.clips ?
      `${j.summary.source_duration_text} → ${j.summary.duration_text} · ` +
      `하이라이트 ${j.summary.clips}개 · 밈 ${j.summary.memes}개 · 자막 ${j.summary.subtitles}줄` : '';
    $('job-log').textContent = (j.log||[]).join('\n');
    $('job-log').scrollTop = 1e6;

    if (j.status === 'done' && j.has_output){
      const v = $('job-video');
      const src = `/api/jobs/${j.id}/video?k=${encodeURIComponent(KEY)}`;
      if (v.getAttribute('src') !== src) v.setAttribute('src', src);
      $('job-result').style.display = 'block';
    }
    if (j.status === 'running' || j.status === 'queued')
      state.timer = setTimeout(poll, 2000);
  } catch(e){ state.timer = setTimeout(poll, 5000); }
}

function download(){
  location.href = `/api/jobs/${state.job}/video?download=1&k=${encodeURIComponent(KEY)}`;
}

/* ---------------- 편집 ---------------- */
async function openEdit(){
  try{
    state.plan = await api(`/api/jobs/${state.job}/plan`);
    state.removed = new Set(); state.edits = {};
    renderEdit();
    go('edit');
  } catch(e){ if (e) toast(e.message); }
}

function renderEdit(){
  const p = state.plan;
  $('clip-count').textContent = `(${p.clips.length - state.removed.size}/${p.clips.length}개 사용)`;
  $('clips').innerHTML = p.clips.map(c => `
    <div class="clip ${state.removed.has(c.index)?'off':''}" onclick="toggleClip(${c.index})">
      <div class="check ${state.removed.has(c.index)?'':'on'}">✓</div>
      <img loading="lazy" src="/api/jobs/${state.job}/thumb/${c.index}?k=${encodeURIComponent(KEY)}"
           onerror="this.style.visibility='hidden'">
      <div class="grow">
        <div class="t">${esc(c.label)}</div>
        <div class="m">원본 ${c.start_text} · ${c.duration}초</div>
      </div>
    </div>`).join('') || '<div class="empty">하이라이트가 없습니다</div>';

  $('subs').innerHTML = p.subtitles.length ? p.subtitles.map(s => `
    <div class="row" style="display:block">
      <div class="m muted">${s.start_text}</div>
      <textarea rows="1" oninput="editSub(${s.index}, this.value)">${esc(s.text)}</textarea>
    </div>`).join('') :
    '<div class="empty">자막이 없습니다<br>(음성 인식을 설치하면 대사 자막이 생깁니다)</div>';
}

function toggleClip(i){
  if (state.removed.has(i)) state.removed.delete(i); else state.removed.add(i);
  renderEdit();
}
function editSub(i, text){ state.edits[i] = text; }

// 앱이 죽어서 멈춘 편집을 이어서. 만들어 둔 조각은 그대로 쓴다.
async function resume(){
  if (!state.job) return;
  try{
    await api(`/api/jobs/${state.job}/replan`, {
      method:'POST', headers:{'Content-Type':'application/json'}, body: '{}'
    });
    toast('멈춘 데부터 이어서 만듭니다');
    $('job-retry').style.display = 'none';
    poll();
  } catch(e){ if (e) toast(e.message); }
}

async function replan(){
  if (state.removed.size >= state.plan.clips.length){ toast('클립을 최소 하나는 남겨 주세요'); return; }
  $('action').disabled = true;
  try{
    await api(`/api/jobs/${state.job}/replan`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        removed_clips:[...state.removed],
        subtitle_edits: state.edits,
      })
    });
    toast('다시 만들고 있습니다');
    go('job'); poll();
  } catch(e){ if (e) toast(e.message); }
  $('action').disabled = false;
}

refresh();
setInterval(() => { if (state.view === 'home') refresh(); }, 5000);
</script>
</body>
</html>
"""

__all__ = ["PAGE"]
