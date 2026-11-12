/* Classroom kiosk: runs on the interactive flat panel (IFP).
 *
 * 1. Downloads the roster (face data of enrolled students and teachers).
 * 2. Watches the panel camera and recognises faces in the browser.
 * 3. Sends "seen" records to the server, which works out entries and exits.
 *
 * Built for Android panels running unattended for days: queued records
 * survive network outages and page reloads, the roster refreshes every few
 * minutes, the screen is kept awake, and slow panels drop to the fast
 * detector. Android stops the camera of apps in the background, so the kiosk
 * must stay visible (full screen, split screen or a floating window). */
(function () {
  'use strict';
  var K = window.KIOSK;
  var SEND_EVERY_MS = 5000;
  var ROSTER_EVERY_MS = 5 * 60 * 1000;
  var RESEND_PERSON_MS = 30 * 1000;     // one sighting per person per 30 s is plenty
  var CONFIRM_HITS = 2;                  // matches needed within CONFIRM_WINDOW_MS before counting
  var CONFIRM_WINDOW_MS = 10 * 1000;
  var DETECT_GAP_MS = 400;               // pause between detection passes
  var QUEUE_MAX = 20000;
  var RELOAD_AFTER_MS = 12 * 3600 * 1000; // periodic fresh start for long-running panels

  var video = document.getElementById('video');
  var overlay = document.getElementById('overlay');
  var ctx = overlay.getContext('2d');
  var msgEl = document.getElementById('msg');
  var statusEl = document.getElementById('status');
  var cameraSel = document.getElementById('camera');

  var state = {
    roster: null, matcher: null, people: {}, detector: 'ssd', activeDetector: null, threshold: 0.5, absenceMs: 10 * 60000,
    queue: load('queue', []), lastSent: {}, hits: {}, lastSeen: {}, since: {}, inClass: {},
    events: [], lastSync: null, syncError: '', faces: 0, startedAt: Date.now(), lastFaceAt: Date.now(),
    showBoxes: load('boxes', true), busy: false, stream: null
  };

  // ------------------------------------------------------------ storage --
  function load(name, fallback) {
    try { var v = localStorage.getItem(K.key + ':' + name); return v === null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  }
  function save(name, value) {
    try { localStorage.setItem(K.key + ':' + name, JSON.stringify(value)); } catch (e) { /* storage full or blocked */ }
  }

  function message(text) {
    if (!text) { msgEl.hidden = true; return; }
    msgEl.hidden = false;
    msgEl.firstElementChild.textContent = text;
  }
  function hhmm(t) { var d = new Date(t); return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }

  // ------------------------------------------------------------- roster --
  async function fetchRoster() {
    try {
      var resp = await fetch(K.roster, { cache: 'no-store' });
      if (resp.status === 404) {
        state.signedOut = true;
        state.syncError = window.AndroidKiosk
          ? 'This panel is no longer signed in to its classroom (the link was changed or the room removed). Tap ⚙ App settings and sign in again.'
          : 'This kiosk link is no longer valid. Copy the new link from the Classrooms page.';
        message(state.syncError);
        return;
      }
      if (!resp.ok) throw new Error('server returned ' + resp.status);
      if (state.signedOut) { state.signedOut = false; message(''); }
      var data = await resp.json();
      save('roster', data);
      applyRoster(data, true);
      state.lastSync = Date.now();
      state.syncError = '';
    } catch (err) {
      state.syncError = 'Server not reachable (' + err.message + '). Records are kept and sent later.';
      if (!state.roster) {
        var cached = load('roster', null);
        if (cached) applyRoster(cached, false);
      }
    }
  }

  function applyRoster(data, fresh) {
    state.roster = data;
    var detector = data.detector || 'ssd';
    if (detector !== state.detector) {
      state.detector = detector;
      if (FaceKit.ready) FaceKit.load(detector).then(function () { state.activeDetector = detector; }).catch(function () {});
    }
    state.threshold = data.threshold || 0.5;
    state.absenceMs = (data.classroom.absence_minutes || 10) * 60000;
    state.people = {};
    var labelled = [];
    data.persons.forEach(function (p) {
      state.people[p.id] = p;
      var descs = p.descriptors.map(function (d) { return new Float32Array(d); });
      if (descs.length) labelled.push(new faceapi.LabeledFaceDescriptors(String(p.id), descs));
    });
    state.matcher = labelled.length ? new faceapi.FaceMatcher(labelled, state.threshold) : null;
    document.getElementById('total').textContent = data.persons.length + ' enrolled';
    var roomEl = document.querySelector('.room');
    if (roomEl && data.classroom && data.classroom.name) roomEl.textContent = data.classroom.name;
    if (fresh) {
      // who the server already has in class (e.g. after a reload), so they are not shown as new entries
      var now = Date.now();
      (data.present || []).forEach(function (p) {
        var seen = now - p.last_seen_age * 1000;
        if (!state.lastSeen[p.person_id] || state.lastSeen[p.person_id] < seen) state.lastSeen[p.person_id] = seen;
        if (!state.inClass[p.person_id]) { state.inClass[p.person_id] = true; state.since[p.person_id] = p.since; }
      });
    }
  }

  // ------------------------------------------------------------- camera --
  async function listCameras() {
    try {
      var devices = (await navigator.mediaDevices.enumerateDevices()).filter(function (d) { return d.kind === 'videoinput'; });
      var chosen = load('camera', '');
      cameraSel.innerHTML = devices.map(function (d, i) {
        return '<option value="' + esc(d.deviceId) + '"' + (d.deviceId === chosen ? ' selected' : '') + '>' +
          esc(d.label || ('Camera ' + (i + 1))) + '</option>';
      }).join('');
      cameraSel.hidden = devices.length < 2;
    } catch (e) { cameraSel.hidden = true; }
  }

  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw { name: 'Insecure' };
    if (state.stream) state.stream.getTracks().forEach(function (t) { t.stop(); });
    var chosen = load('camera', '');
    var constraints = { audio: false, video: { width: { ideal: 1920 }, height: { ideal: 1080 } } };
    if (chosen) constraints.video.deviceId = { exact: chosen };
    try { state.stream = await navigator.mediaDevices.getUserMedia(constraints); }
    catch (err) {
      if (!chosen) throw err;
      save('camera', '');           // saved camera is gone: fall back to the default one
      delete constraints.video.deviceId;
      state.stream = await navigator.mediaDevices.getUserMedia(constraints);
    }
    video.srcObject = state.stream;
    state.stream.getVideoTracks().forEach(function (t) {
      t.addEventListener('ended', function () { setTimeout(restartCamera, 3000); });
    });
    await new Promise(function (resolve) {
      if (video.readyState >= 2) return resolve();
      video.onloadeddata = function () { resolve(); };
    });
    overlay.width = video.videoWidth;
    overlay.height = video.videoHeight;
    await listCameras();
  }

  async function restartCamera() {
    try { await startCamera(); message(''); }
    catch (err) { message(FaceKit.cameraError(err)); setTimeout(restartCamera, 15000); }
  }

  // ---------------------------------------------------------- detection --
  async function detectOnce() {
    if (state.busy || !state.matcher || !state.activeDetector || video.readyState < 2 || !video.videoWidth) return;
    state.busy = true;
    try {
      var t0 = Date.now();
      var results = await FaceKit.detectAll(video, state.activeDetector);
      var now = Date.now();
      adaptSpeed(now - t0);
      state.faces = results.length;
      if (results.length) state.lastFaceAt = now;
      var labels = results.map(function (r) {
        var best = state.matcher.findBestMatch(r.descriptor);
        var known = best.label !== 'unknown';
        if (known) noteMatch(Number(best.label), best.distance, now);
        return { box: r.detection.box, person: known ? state.people[best.label] : null, distance: best.distance };
      });
      draw(labels);
    } catch (err) {
      state.syncError = 'Recognition error: ' + err.message;
    } finally {
      state.busy = false;
    }
  }

  /* Android panels often have slow processors. If the accurate detector keeps
   * taking more than 1.5 s per frame, switch this panel to the fast one. */
  var slowFrames = 0;
  function adaptSpeed(ms) {
    if (state.activeDetector !== 'ssd') return;
    slowFrames = ms > 1500 ? slowFrames + 1 : Math.max(0, slowFrames - 1);
    if (slowFrames < 10) return;
    slowFrames = 0;
    FaceKit.load('tiny').then(function () {
      state.activeDetector = 'tiny';
      state.detector = 'tiny';
      state.syncError = 'This panel is slow, so it switched to the fast face detector.';
    }).catch(function () {});
  }

  function noteMatch(pid, distance, now) {
    var hits = (state.hits[pid] || []).filter(function (t) { return now - t < CONFIRM_WINDOW_MS; });
    hits.push(now);
    state.hits[pid] = hits.slice(-5);
    if (hits.length < CONFIRM_HITS) return;
    state.lastSeen[pid] = now;
    if (!state.inClass[pid]) {
      state.inClass[pid] = true;
      state.since[pid] = hhmm(now);
      addEvent('in', pid, now);
    }
    if (!state.lastSent[pid] || now - state.lastSent[pid] >= RESEND_PERSON_MS) {
      state.lastSent[pid] = now;
      state.queue.push({ person_id: pid, t: now, distance: Math.round(distance * 1000) / 1000 });
      if (state.queue.length > QUEUE_MAX) state.queue.splice(0, state.queue.length - QUEUE_MAX);
      save('queue', state.queue);
    }
  }

  function draw(labels) {
    ctx.clearRect(0, 0, overlay.width, overlay.height);
    if (!state.showBoxes) return;
    var W = overlay.width;
    ctx.lineWidth = Math.max(2, W / 600);
    ctx.font = 'bold ' + Math.max(14, Math.round(W / 70)) + 'px system-ui, sans-serif';
    labels.forEach(function (l) {
      var b = l.box, x = W - b.x - b.width;  // the video is mirrored on screen
      var color = l.person ? '#2fbf71' : '#f0b429';
      ctx.strokeStyle = color;
      ctx.strokeRect(x, b.y, b.width, b.height);
      var text = l.person ? l.person.name : 'Unknown';
      var tw = ctx.measureText(text).width + 10, th = parseInt(ctx.font.split(' ')[1], 10) + 8;
      ctx.fillStyle = color;
      ctx.fillRect(x, b.y - th, tw, th);
      ctx.fillStyle = '#000';
      ctx.fillText(text, x + 5, b.y - 6);
    });
  }

  // ------------------------------------------------------ presence view --
  function addEvent(kind, pid, t) {
    var p = state.people[pid];
    state.events.unshift({ kind: kind, name: p ? p.name : '#' + pid, role: p ? p.role : '', time: hhmm(t) });
    state.events = state.events.slice(0, 200);
  }

  function expire() {
    var now = Date.now();
    Object.keys(state.inClass).forEach(function (pid) {
      if (state.inClass[pid] && now - (state.lastSeen[pid] || 0) > state.absenceMs) {
        state.inClass[pid] = false;
        addEvent('out', pid, state.lastSeen[pid] || now);
      }
    });
  }

  function render() {
    var ids = Object.keys(state.inClass).filter(function (k) { return state.inClass[k]; });
    var people = ids.map(function (id) { return { id: id, p: state.people[id] }; }).filter(function (x) { return x.p; });
    people.sort(function (a, b) { return (a.p.role === b.p.role ? 0 : a.p.role === 'teacher' ? -1 : 1) || a.p.name.localeCompare(b.p.name); });
    document.getElementById('count').textContent = people.length;
    document.getElementById('present').innerHTML = people.map(function (x) {
      return '<div class="who"><span>' + esc(x.p.name) + (x.p.role === 'teacher' ? '<span class="tag">Teacher</span>' : '') +
        '</span><small>since ' + esc(state.since[x.id] || '') + '</small></div>';
    }).join('') || '<div class="who"><small>Nobody recognised yet</small></div>';
    document.getElementById('events').innerHTML = state.events.slice(0, 60).map(function (e) {
      return '<div class="who"><span class="ev-' + e.kind + '">' + (e.kind === 'in' ? 'Entry' : 'Exit') + '</span><span>' +
        esc(e.name) + '</span><small>' + e.time + '</small></div>';
    }).join('');
    var parts = [];
    parts.push(state.matcher ? 'Recognising' : 'No enrolled faces yet');
    parts.push(state.faces + ' face' + (state.faces === 1 ? '' : 's') + ' in view');
    parts.push(state.lastSync ? 'synced ' + hhmm(state.lastSync) : 'not synced yet');
    if (state.queue.length) parts.push(state.queue.length + ' waiting to send');
    statusEl.innerHTML = esc(parts.join(' · ')) + (state.syncError ? '<div class="err">' + esc(state.syncError) + '</div>' : '');
    var d = new Date();
    document.getElementById('clock').textContent = d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }) + '  ' + hhmm(d);
  }

  // --------------------------------------------------------------- send --
  async function flush() {
    if (!state.queue.length || state.signedOut) return;  // keep records until the panel is signed in again
    var batch = state.queue.slice(0, 1000);
    var now = Date.now();
    var body = { sightings: batch.map(function (s) {
      return { person_id: s.person_id, distance: s.distance, age_seconds: Math.max(0, (now - s.t) / 1000) };
    }) };
    try {
      var resp = await fetch(K.sightings, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      if (resp.status === 404) { state.signedOut = true; fetchRoster(); return; }
      if (!resp.ok) throw new Error('server returned ' + resp.status);
      state.queue.splice(0, batch.length);
      save('queue', state.queue);
      state.lastSync = Date.now();
      state.syncError = '';
    } catch (err) {
      state.syncError = 'Could not send records (' + err.message + '). They are kept and retried.';
    }
  }

  // --------------------------------------------------------------- loop --
  /* Timers in a hidden/background tab are slowed to once a minute; timers in
   * a worker are not, so a worker drives the loop. */
  function ticker(ms, fn) {
    try {
      var src = 'setInterval(function(){postMessage(0)},' + ms + ')';
      var w = new Worker(URL.createObjectURL(new Blob([src], { type: 'text/javascript' })));
      w.onmessage = fn;
      return;
    } catch (e) { /* workers blocked: plain timer */ }
    setInterval(fn, ms);
  }

  var lastDetect = 0, lastSend = 0, lastRoster = Date.now();
  function tick() {
    var now = Date.now();
    if (!state.busy && now - lastDetect >= DETECT_GAP_MS) { lastDetect = now; detectOnce(); }
    if (now - lastSend >= SEND_EVERY_MS) { lastSend = now; expire(); flush(); render(); }
    if (now - lastRoster >= ROSTER_EVERY_MS) { lastRoster = now; fetchRoster(); }
    if (now - state.startedAt > RELOAD_AFTER_MS && now - state.lastFaceAt > 10 * 60000 && !state.queue.length) location.reload();
  }

  // ------------------------------------------------------------ buttons --
  document.getElementById('fs').onclick = function () {
    if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen().catch(function () {});
  };
  document.getElementById('compact').onclick = function () {
    document.body.classList.toggle('compact'); save('compact', document.body.classList.contains('compact'));
  };
  document.getElementById('boxes').onclick = function () {
    state.showBoxes = !state.showBoxes; save('boxes', state.showBoxes);
    this.textContent = state.showBoxes ? 'Hide boxes' : 'Show boxes';
    if (!state.showBoxes) ctx.clearRect(0, 0, overlay.width, overlay.height);
  };
  cameraSel.onchange = function () { save('camera', cameraSel.value); restartCamera(); };
  if (load('compact', false) || /[?&]compact=1/.test(location.search)) document.body.classList.add('compact');
  if (!state.showBoxes) document.getElementById('boxes').textContent = 'Show boxes';
  if (navigator.wakeLock) {
    var lock = function () { navigator.wakeLock.request('screen').catch(function () {}); };
    lock();
    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible') lock(); });
  }
  // Android releases the camera while the browser is in the background; get it back on return.
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible' || !state.stream) return;
    var live = state.stream.getVideoTracks().some(function (t) { return t.readyState === 'live' && !t.muted; });
    if (!live) restartCamera(); else if (video.paused) video.play().catch(function () {});
  });

  // -------------------------------------------------------------- start --
  async function main() {
    try {
      message('Loading face recognition…');
      await fetchRoster();
      if (!state.roster) throw new Error(state.syncError || 'could not load the roster');
      await FaceKit.load(state.detector);
      state.activeDetector = state.detector;
    } catch (err) {
      message('Could not start: ' + err.message + '. Retrying in 30 seconds…');
      setTimeout(main, 30000);
      return;
    }
    message('Starting the camera…');
    try { await startCamera(); }
    catch (err) { message(FaceKit.cameraError(err)); setTimeout(restartCamera, 15000); }
    if (video.srcObject) message(state.matcher ? '' : 'No students or teachers have face photos yet. Add them on the admin PC; this panel picks them up within 5 minutes.');
    render();
    ticker(200, tick);
    // the message clears itself once faces are enrolled
    setInterval(function () { if (state.matcher && video.srcObject && !msgEl.hidden && /No students/.test(msgEl.textContent)) message(''); }, 5000);
  }
  main();
})();
