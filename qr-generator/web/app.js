/* QR Generator web client. Served by the QR Generator server (qr-generator/server) and
   shown inside the Android app. Every QR code encodes a short link on the server, so
   codes keep working only while the owner's account (7-day free trial) is active. */
(function () {
  'use strict';

  qrcode.stringToBytes = qrcode.stringToBytesFuncs['UTF-8'];

  var $ = function (id) { return document.getElementById(id); };
  var form = $('form');
  var design = $('designForm');
  var canvas = $('qr');
  var statusEl = $('status');
  var tabs = $('tabs');
  var logoInput = $('logoInput');
  var removeLogoBtn = $('removeLogo');
  var codeList = $('codeList');
  var actionButtons = ['downloadPng', 'downloadSvg', 'copyImg', 'shareImg'].map($);

  // Present only inside the Android app (see MainActivity.java).
  var android = window.AndroidBridge || null;

  var TOKEN_KEY = 'qrgen.token';
  var state = {
    token: null, account: null, codes: [],
    current: null,            // the saved code being shown/edited, or null for a new one
    type: 'url', logo: null, logoDataUrl: null, model: null
  };

  // ---------------------------------------------------------------- server API

  function api(method, path, body) {
    var headers = { 'Accept': 'application/json' };
    if (body) headers['Content-Type'] = 'application/json';
    if (state.token) headers.Authorization = 'Bearer ' + state.token;
    return fetch(path, { method: method, headers: headers, body: body ? JSON.stringify(body) : undefined })
      .catch(function () { throw new Error('Can’t reach the server. Check your internet connection.'); })
      .then(function (resp) {
        return resp.json().catch(function () { return {}; }).then(function (json) {
          if (resp.status === 401 && state.token) signedOut();
          if (!resp.ok) throw new Error(json.error || 'Something went wrong (' + resp.status + ').');
          return json;
        });
      });
  }

  function saveToken(token) {
    state.token = token;
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token); else localStorage.removeItem(TOKEN_KEY);
    } catch (e) { /* storage unavailable: stay logged in for this visit only */ }
  }

  function loadToken() {
    try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
  }

  // ---------------------------------------------------------------- QR model

  var ECL_ORDER = ['L', 'M', 'Q', 'H'];

  function makeModel(text, ecl) {
    var qr = qrcode(0, ecl);
    qr.addData(text, 'Byte');
    qr.make();
    var n = qr.getModuleCount();
    var m = [];
    for (var r = 0; r < n; r++) {
      var row = [];
      for (var c = 0; c < n; c++) row.push(qr.isDark(r, c));
      m.push(row);
    }
    return m;
  }

  // ---------------------------------------------------------------- rendering

  function rrect(x, y, w, h, r) {
    if (r <= 0) return 'M' + x + ' ' + y + 'h' + w + 'v' + h + 'h' + (-w) + 'z';
    r = Math.min(r, w / 2, h / 2);
    var a = ' ' + r + ' ' + r + ' 0 0 1 ';
    return 'M' + (x + r) + ' ' + y + 'h' + (w - 2 * r) +
      'a' + a + r + ' ' + r + 'v' + (h - 2 * r) +
      'a' + a + (-r) + ' ' + r + 'h' + (-(w - 2 * r)) +
      'a' + a + (-r) + ' ' + (-r) + 'v' + (-(h - 2 * r)) +
      'a' + a + r + ' ' + (-r) + 'z';
  }

  var EYE_RADII = { square: [0, 0, 0], rounded: [2, 1.3, 0.9], dots: [3.5, 2.5, 1.5] };

  function inEye(r, c, n) {
    return (r < 7 && c < 7) || (r < 7 && c >= n - 7) || (r >= n - 7 && c < 7);
  }

  // Builds one SVG path (module units, quiet zone included) for every dark module.
  // The same path string is used for SVG export and, through Path2D, for the canvas.
  function buildPath(m, margin, style) {
    var n = m.length, d = '';
    var eyes = [[0, 0], [0, n - 7], [n - 7, 0]];
    var er = EYE_RADII[style];
    eyes.forEach(function (e) {
      var y = e[0] + margin, x = e[1] + margin;
      d += rrect(x, y, 7, 7, er[0]) + rrect(x + 1, y + 1, 5, 5, er[1]) + rrect(x + 2, y + 2, 3, 3, er[2]);
    });
    for (var r = 0; r < n; r++) {
      for (var c = 0; c < n; c++) {
        if (!m[r][c] || inEye(r, c, n)) continue;
        var x = c + margin, y = r + margin;
        if (style === 'square') {
          // merge horizontal runs to keep the path small
          var run = 1;
          while (c + run < n && m[r][c + run] && !inEye(r, c + run, n)) run++;
          d += 'M' + x + ' ' + y + 'h' + run + 'v1h' + (-run) + 'z';
          c += run - 1;
        } else if (style === 'rounded') {
          d += rrect(x + 0.05, y + 0.05, 0.9, 0.9, 0.35);
        } else {
          d += rrect(x + 0.1, y + 0.1, 0.8, 0.8, 0.4);
        }
      }
    }
    return d;
  }

  function readOptions() {
    return {
      fg: design.elements.fg.value,
      bg: design.elements.bg.value,
      style: design.elements.style.value,
      ecl: design.elements.ecl.value,
      size: parseInt(design.elements.size.value, 10),
      margin: parseInt(design.elements.margin.value, 10)
    };
  }

  function logoBox(total) {
    var s = Math.round(total * 0.22 * 100) / 100;
    var pad = Math.max(0.5, s * 0.08);
    return { x: (total - s) / 2, s: s, pad: pad };
  }

  // Draws onto any canvas; returns the canvas.
  function drawCanvas(target, model, o) {
    var total = model.length + o.margin * 2;
    var px = Math.max(1, Math.round(o.size / total));
    var dim = px * total;
    target.width = dim;
    target.height = dim;
    var ctx = target.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = o.bg;
    ctx.fillRect(0, 0, dim, dim);
    ctx.scale(px, px);
    ctx.fillStyle = o.fg;
    ctx.fill(new Path2D(buildPath(model, o.margin, o.style)), 'evenodd');
    if (state.logo) {
      var b = logoBox(total);
      ctx.fillStyle = o.bg;
      ctx.fill(new Path2D(rrect(b.x - b.pad, b.x - b.pad, b.s + 2 * b.pad, b.s + 2 * b.pad, b.pad)));
      var img = state.logo, ratio = img.width / img.height;
      var w = ratio >= 1 ? b.s : b.s * ratio, h = ratio >= 1 ? b.s / ratio : b.s;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(img, b.x + (b.s - w) / 2, b.x + (b.s - h) / 2, w, h);
    }
    return target;
  }

  function buildSvg(model, o) {
    var total = model.length + o.margin * 2;
    var out = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + total + ' ' + total +
      '" width="' + o.size + '" height="' + o.size + '" shape-rendering="' +
      (o.style === 'square' ? 'crispEdges' : 'geometricPrecision') + '">' +
      '<rect width="100%" height="100%" fill="' + o.bg + '"/>' +
      '<path fill="' + o.fg + '" fill-rule="evenodd" d="' + buildPath(model, o.margin, o.style) + '"/>';
    if (state.logo) {
      var b = logoBox(total);
      out += '<path fill="' + o.bg + '" d="' + rrect(b.x - b.pad, b.x - b.pad, b.s + 2 * b.pad, b.s + 2 * b.pad, b.pad) + '"/>' +
        '<image href="' + state.logoDataUrl + '" x="' + b.x + '" y="' + b.x + '" width="' + b.s + '" height="' + b.s +
        '" preserveAspectRatio="xMidYMid meet"/>';
    }
    return out + '</svg>';
  }

  // ---------------------------------------------------------------- account views

  var authMode = 'register';

  function setAuthMode(mode) {
    authMode = mode;
    Array.prototype.forEach.call($('authTabs').querySelectorAll('button'), function (b) {
      b.setAttribute('aria-selected', String(b.dataset.mode === mode));
    });
    Array.prototype.forEach.call(document.querySelectorAll('[data-only]'), function (el) {
      el.hidden = el.dataset.only !== mode;
    });
    $('authForm').elements.password.autocomplete = mode === 'register' ? 'new-password' : 'current-password';
    $('authSubmit').textContent = mode === 'register' ? 'Start free trial' : 'Log in';
    $('authError').textContent = '';
  }

  function showAuth() {
    $('authView').hidden = false;
    $('appView').hidden = true;
    $('accountBar').hidden = true;
  }

  function signedOut() {
    saveToken(null);
    state.account = null;
    state.codes = [];
    resetEditor();
    showAuth();
  }

  function formatDate(iso) {
    return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function showAccount(account) {
    state.account = account;
    $('authView').hidden = true;
    $('appView').hidden = false;
    $('accountBar').hidden = false;
    $('accountEmail').textContent = account.email;

    var badge = $('trialBadge'), banner = $('expiredBanner');
    var msLeft = new Date(account.access_until) - Date.now();
    var days = Math.ceil(msLeft / 86400000);
    badge.className = 'badge';
    if (account.active) {
      if (days > 30) {
        badge.textContent = 'Active until ' + formatDate(account.access_until);
      } else {
        badge.textContent = days <= 1 ? 'Last day of trial' : days + ' days left';
        if (days <= 2) badge.classList.add('warn');
      }
      banner.hidden = true;
    } else {
      badge.textContent = account.disabled ? 'Disabled' : 'Expired';
      badge.classList.add('bad');
      banner.textContent = (account.disabled ? 'Your account is disabled. ' :
        'Your free trial ended on ' + formatDate(account.access_until) + '. ') +
        'Your QR codes have stopped working. ' + account.contact;
      banner.hidden = false;
    }
    $('saveBtn').disabled = !account.active;
  }

  function refreshAccount() {
    return api('GET', '/api/me').then(function (r) { showAccount(r.account); });
  }

  function loadCodes() {
    return api('GET', '/api/codes').then(function (r) {
      state.codes = r.codes;
      renderCodeList();
    });
  }

  // ---------------------------------------------------------------- editor

  function fieldsFor(type) {
    return document.querySelector('.fields[data-for="' + type + '"]');
  }

  function collectData() {
    var data = {};
    Array.prototype.forEach.call(fieldsFor(state.type).querySelectorAll('[name]'), function (el) {
      data[el.name] = el.type === 'checkbox' ? el.checked : el.value;
    });
    return data;
  }

  function selectType(type) {
    state.type = type;
    Array.prototype.forEach.call(tabs.querySelectorAll('button'), function (b) {
      b.setAttribute('aria-selected', String(b.dataset.type === type));
    });
    Array.prototype.forEach.call(form.querySelectorAll('.fields[data-for]'), function (f) {
      f.hidden = f.dataset.for !== type;
    });
  }

  function clearFields() {
    Array.prototype.forEach.call(form.querySelectorAll('[name]'), function (el) {
      if (el.type === 'checkbox') el.checked = false;
      else if (el.tagName === 'SELECT') el.selectedIndex = 0;
      else el.value = '';
    });
  }

  function resetEditor() {
    state.current = null;
    clearFields();
    selectType('url');
    syncEditor();
    renderCodeList();
    scheduleRender();
  }

  function openCode(code) {
    state.current = code;
    clearFields();
    selectType(code.type);
    Object.keys(code.data).forEach(function (k) {
      var el = form.elements[k];
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!code.data[k]; else el.value = code.data[k];
    });
    form.elements.label.value = code.label;
    syncEditor();
    renderCodeList();
    scheduleRender();
  }

  function syncEditor() {
    var editing = !!state.current;
    $('editorTitle').textContent = editing ? 'Edit QR code' : 'New QR code';
    $('saveBtn').textContent = editing ? 'Save changes' : 'Create QR code';
    $('newCodeBtn').hidden = !editing;
    $('deleteBtn').hidden = !editing;
    $('saveError').textContent = '';
  }

  function saveCode(e) {
    e.preventDefault();
    var btn = $('saveBtn');
    var body = { type: state.type, data: collectData(), label: form.elements.label.value };
    var editing = state.current;
    btn.disabled = true;
    $('saveError').textContent = '';
    api(editing ? 'PUT' : 'POST', editing ? '/api/codes/' + editing.id : '/api/codes', body)
      .then(function (r) {
        state.codes = [r.code].concat(state.codes.filter(function (c) { return c.id !== r.code.id; }));
        openCode(r.code);
        setStatus(editing ? 'Saved. Printed copies of this code now show the new content.' : 'QR code created.');
      })
      .catch(function (err) {
        $('saveError').textContent = err.message;
        if (/trial has ended/.test(err.message)) refreshAccount().catch(function () {});
      })
      .then(function () { btn.disabled = !(state.account && state.account.active); });
  }

  function deleteCode() {
    var code = state.current;
    if (!code || !confirm('Delete “' + code.label + '”? Printed copies of this QR code will stop working.')) return;
    api('DELETE', '/api/codes/' + code.id).then(function () {
      state.codes = state.codes.filter(function (c) { return c.id !== code.id; });
      resetEditor();
      setStatus('QR code deleted.');
    }, function (err) { $('saveError').textContent = err.message; });
  }

  var TYPE_NAMES = { url: 'Link', text: 'Text', wifi: 'Wi-Fi', vcard: 'Contact', email: 'Email', sms: 'SMS', phone: 'Phone' };

  function renderCodeList() {
    codeList.textContent = '';
    if (!state.codes.length) {
      var empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = 'Codes you create appear here. You can edit them any time — the printed code stays the same.';
      codeList.appendChild(empty);
      return;
    }
    state.codes.forEach(function (code) {
      var li = document.createElement('li'), b = document.createElement('button');
      var name = document.createElement('span'), scans = document.createElement('span'), meta = document.createElement('span');
      b.type = 'button';
      name.className = 'name';
      name.textContent = code.label;
      scans.className = 'scans';
      scans.textContent = code.scans + (code.scans === 1 ? ' scan' : ' scans');
      meta.className = 'meta';
      meta.textContent = TYPE_NAMES[code.type] + ' · created ' + formatDate(code.created_at);
      b.appendChild(name);
      b.appendChild(scans);
      b.appendChild(meta);
      b.setAttribute('aria-current', String(!!state.current && state.current.id === code.id));
      b.addEventListener('click', function () { openCode(code); });
      li.appendChild(b);
      codeList.appendChild(li);
    });
  }


  function setStatus(msg, isError) {
    statusEl.textContent = msg || '';
    statusEl.classList.toggle('error', !!isError);
  }

  function luminance(hex) {
    var n = parseInt(hex.slice(1), 16);
    return [n >> 16, (n >> 8) & 255, n & 255].map(function (c) {
      c /= 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    }).reduce(function (s, c, i) { return s + c * [0.2126, 0.7152, 0.0722][i]; }, 0);
  }

  // ---------------------------------------------------------------- preview

  var pending = 0;
  function scheduleRender() {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(render);
  }

  function render() {
    var o = readOptions();
    $('sizeOut').textContent = o.size + ' px';
    $('marginOut').textContent = o.margin;

    var code = state.current, link = $('shortLink');
    state.model = null;
    $('placeholder').hidden = !!code;
    link.hidden = !code;
    if (!code) {
      var ctx = canvas.getContext('2d');
      canvas.width = canvas.height = 256;
      ctx.fillStyle = o.bg;
      ctx.fillRect(0, 0, 256, 256);
      setStatus('');
      toggleActions(false);
      return;
    }

    link.textContent = '';
    var a = document.createElement('a');
    a.href = code.short_url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = code.short_url;
    link.appendChild(document.createTextNode('Opens: '));
    link.appendChild(a);

    // A logo hides part of the code, so require at least Quartile correction.
    var ecl = o.ecl;
    if (state.logo && ECL_ORDER.indexOf(ecl) < 2) ecl = 'H';
    state.model = makeModel(code.short_url, ecl);
    drawCanvas(canvas, state.model, o);
    toggleActions(true);

    var notes = [], warn = true;
    if (ecl !== o.ecl) notes.push('Error correction raised to High for the logo');
    var lf = luminance(o.fg), lb = luminance(o.bg);
    if (state.account && !state.account.active) notes.push('This code is not working right now — your trial has ended');
    else if (lf > lb) notes.push('Light-on-dark codes may not scan in every app');
    else if ((lb + 0.05) / (lf + 0.05) < 3) notes.push('Low contrast may not scan reliably');
    else warn = false;
    if (notes.length) setStatus(notes.join(' — '), warn);
    else if (/^QR code (created|deleted)|^Saved/.test(statusEl.textContent)) { /* keep the save message */ }
    else setStatus('');
  }

  function toggleActions(on) {
    actionButtons.forEach(function (b) { b.disabled = !on; });
  }

  // ---------------------------------------------------------------- export

  function fileBase() {
    var slug = (state.current ? state.current.label : 'code')
      .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
    return 'qr-' + (slug || state.current.slug);
  }

  function exportCanvas() {
    return drawCanvas(document.createElement('canvas'), state.model, readOptions());
  }

  function canvasBlob(c) {
    return new Promise(function (resolve) { c.toBlob(resolve, 'image/png'); });
  }

  function saveBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }

  function toBase64(str) {
    return btoa(unescape(encodeURIComponent(str)));
  }

  function downloadPng() {
    if (!state.model) return;
    var c = exportCanvas(), name = fileBase() + '.png';
    if (android) android.saveFile(c.toDataURL('image/png').split(',')[1], 'image/png', name);
    else canvasBlob(c).then(function (b) { saveBlob(b, name); });
  }

  function downloadSvg() {
    if (!state.model) return;
    var svg = buildSvg(state.model, readOptions()), name = fileBase() + '.svg';
    if (android) android.saveFile(toBase64(svg), 'image/svg+xml', name);
    else saveBlob(new Blob([svg], { type: 'image/svg+xml' }), name);
  }

  function copyImage() {
    if (!state.model) return;
    if (!navigator.clipboard || !window.ClipboardItem) {
      setStatus('Copying images is not supported in this browser — use Download instead.', true);
      return;
    }
    // Pass the promise directly so Safari keeps the user-gesture context.
    navigator.clipboard.write([new ClipboardItem({ 'image/png': canvasBlob(exportCanvas()) })])
      .then(function () { setStatus('Copied to clipboard.'); },
        function () { setStatus('Could not copy — use Download instead.', true); });
  }

  function shareImage() {
    if (!state.model) return;
    var c = exportCanvas(), name = fileBase() + '.png';
    if (android) {
      android.shareImage(c.toDataURL('image/png').split(',')[1], name);
      return;
    }
    canvasBlob(c).then(function (blob) {
      var file = new File([blob], name, { type: 'image/png' });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        return navigator.share({ files: [file], title: 'QR code' }).catch(function () {});
      }
      saveBlob(blob, name);
      setStatus('Sharing is not supported here, so the image was downloaded instead.');
    });
  }

  // ---------------------------------------------------------------- wiring

  $('authTabs').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-mode]');
    if (b) setAuthMode(b.dataset.mode);
  });

  $('authForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var f = e.target, btn = $('authSubmit');
    var body = { email: f.elements.email.value, password: f.elements.password.value, name: f.elements.name.value };
    btn.disabled = true;
    $('authError').textContent = '';
    api('POST', authMode === 'register' ? '/api/register' : '/api/login', body)
      .then(function (r) {
        saveToken(r.token);
        f.elements.password.value = '';
        showAccount(r.account);
        resetEditor();
        return loadCodes();
      })
      .catch(function (err) { $('authError').textContent = err.message; })
      .then(function () { btn.disabled = false; });
  });

  $('logoutBtn').addEventListener('click', function () {
    api('POST', '/api/logout').catch(function () {}).then(signedOut);
  });

  tabs.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-type]');
    if (!b) return;
    selectType(b.dataset.type);
  });

  form.addEventListener('submit', saveCode);
  $('deleteBtn').addEventListener('click', deleteCode);
  $('newCodeBtn').addEventListener('click', function () { resetEditor(); setStatus(''); });
  design.addEventListener('input', scheduleRender);
  design.addEventListener('change', scheduleRender);
  design.addEventListener('submit', function (e) { e.preventDefault(); });

  logoInput.addEventListener('change', function () {
    var file = logoInput.files && logoInput.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function () {
      var img = new Image();
      img.onload = function () {
        state.logo = img;
        state.logoDataUrl = reader.result;
        removeLogoBtn.hidden = false;
        scheduleRender();
      };
      img.onerror = function () { setStatus('That file could not be read as an image.', true); };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });

  removeLogoBtn.addEventListener('click', function () {
    state.logo = state.logoDataUrl = null;
    logoInput.value = '';
    removeLogoBtn.hidden = true;
    scheduleRender();
  });

  $('downloadPng').addEventListener('click', downloadPng);
  $('downloadSvg').addEventListener('click', downloadSvg);
  $('copyImg').addEventListener('click', copyImage);
  $('shareImg').addEventListener('click', shareImage);

  // The Android app saves and shares natively; hide Copy there (WebView has no image clipboard).
  if (android) $('copyImg').hidden = true;

  // Prefill from outside — the Android app calls this with text shared from other apps.
  function fill(text) {
    text = String(text || '').trim();
    resetEditor();
    if (/^https?:\/\/\S+$/i.test(text)) {
      form.elements.url.value = text;
    } else {
      selectType('text');
      form.elements.text.value = text;
    }
  }

  window.QRGen = { makeModel: makeModel, buildSvg: buildSvg, fill: fill };

  // ---------------------------------------------------------------- start

  setAuthMode('register');
  resetEditor();
  state.token = loadToken();
  if (state.token) {
    refreshAccount().then(loadCodes).catch(function (err) {
      if (!state.token) return;  // token was rejected; signedOut() already showed the login form
      // Still logged in but the server is unreachable: show the error with a way to retry.
      $('authView').hidden = true;
      $('appView').hidden = false;
      $('saveBtn').disabled = true;
      var banner = $('expiredBanner');
      banner.textContent = err.message + ' ';
      var retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'link';
      retry.textContent = 'Retry';
      retry.addEventListener('click', function () { location.reload(); });
      banner.appendChild(retry);
      banner.hidden = false;
    });
  } else {
    showAuth();
  }
})();
