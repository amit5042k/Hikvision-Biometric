/* QR Generator — runs fully client-side. Used by the website and bundled into the Android app. */
(function () {
  'use strict';

  qrcode.stringToBytes = qrcode.stringToBytesFuncs['UTF-8'];

  var form = document.getElementById('form');
  var canvas = document.getElementById('qr');
  var statusEl = document.getElementById('status');
  var tabs = document.getElementById('tabs');
  var logoInput = document.getElementById('logoInput');
  var removeLogoBtn = document.getElementById('removeLogo');
  var historyList = document.getElementById('historyList');
  var actionButtons = ['downloadPng', 'downloadSvg', 'copyImg', 'shareImg'].map(function (id) {
    return document.getElementById(id);
  });

  // Present only inside the Android app (see MainActivity.java).
  var android = window.AndroidBridge || null;

  var state = { type: 'url', logo: null, logoDataUrl: null, payload: '', model: null };

  // ---------------------------------------------------------------- payloads

  function v(name) {
    var el = form.elements[name];
    return el ? (el.type === 'checkbox' ? el.checked : el.value.trim()) : '';
  }

  function escWifi(s) { return s.replace(/([\\;,:"])/g, '\\$1'); }
  function escVcard(s) { return s.replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/([,;])/g, '\\$1'); }

  var builders = {
    url: function () {
      var u = v('url');
      if (u && !/^[a-z][a-z0-9+.-]*:/i.test(u)) u = 'https://' + u;
      return u;
    },
    text: function () { return form.elements.text.value; },
    wifi: function () {
      var ssid = v('ssid');
      if (!ssid) return '';
      var sec = v('security');
      var s = 'WIFI:T:' + sec + ';S:' + escWifi(ssid) + ';';
      if (sec !== 'nopass') s += 'P:' + escWifi(form.elements.wifiPassword.value) + ';';
      if (v('hidden')) s += 'H:true;';
      return s + ';';
    },
    vcard: function () {
      var first = v('firstName'), last = v('lastName');
      if (!(first || last || v('vPhone') || v('vEmail') || v('org'))) return '';
      var lines = ['BEGIN:VCARD', 'VERSION:3.0',
        'N:' + escVcard(last) + ';' + escVcard(first) + ';;;',
        'FN:' + escVcard((first + ' ' + last).trim() || v('org'))];
      if (v('org')) lines.push('ORG:' + escVcard(v('org')));
      if (v('title')) lines.push('TITLE:' + escVcard(v('title')));
      if (v('vPhone')) lines.push('TEL;TYPE=CELL:' + escVcard(v('vPhone')));
      if (v('vEmail')) lines.push('EMAIL:' + escVcard(v('vEmail')));
      if (v('vUrl')) lines.push('URL:' + escVcard(v('vUrl')));
      if (v('address')) lines.push('ADR:;;' + escVcard(v('address')) + ';;;;');
      lines.push('END:VCARD');
      return lines.join('\r\n');
    },
    email: function () {
      var to = v('emailTo');
      if (!to) return '';
      var q = [];
      if (v('emailSubject')) q.push('subject=' + encodeURIComponent(v('emailSubject')));
      if (form.elements.emailBody.value) q.push('body=' + encodeURIComponent(form.elements.emailBody.value));
      return 'mailto:' + to + (q.length ? '?' + q.join('&') : '');
    },
    sms: function () {
      var n = v('smsNumber').replace(/[^\d+]/g, '');
      if (!n) return '';
      return 'SMSTO:' + n + ':' + form.elements.smsBody.value;
    },
    phone: function () {
      var n = v('phone').replace(/[^\d+]/g, '');
      return n ? 'tel:' + n : '';
    }
  };

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
      fg: form.elements.fg.value,
      bg: form.elements.bg.value,
      style: form.elements.style.value,
      ecl: form.elements.ecl.value,
      size: parseInt(form.elements.size.value, 10),
      margin: parseInt(form.elements.margin.value, 10)
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

  // ---------------------------------------------------------------- status

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

  // ---------------------------------------------------------------- update loop

  var pending = 0;
  function scheduleRender() {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(render);
  }

  function render() {
    var o = readOptions();
    document.getElementById('sizeOut').textContent = o.size + ' px';
    document.getElementById('marginOut').textContent = o.margin;

    var payload = builders[state.type]();
    state.payload = payload;
    state.model = null;
    if (!payload) {
      var ctx = canvas.getContext('2d');
      canvas.width = canvas.height = 256;
      ctx.fillStyle = o.bg;
      ctx.fillRect(0, 0, 256, 256);
      setStatus('Fill in the fields to create your QR code.');
      toggleActions(false);
      return;
    }

    // A logo hides part of the code, so require at least Quartile correction.
    var ecl = o.ecl;
    if (state.logo && ECL_ORDER.indexOf(ecl) < 2) ecl = 'H';
    try {
      state.model = makeModel(payload, ecl);
    } catch (e) {
      setStatus('Too much data for a QR code. Shorten the content or lower error correction.', true);
      toggleActions(false);
      return;
    }
    drawCanvas(canvas, state.model, o);
    toggleActions(true);

    var n = state.model.length;
    var notes = ['Version ' + ((n - 17) / 4) + ' · ' + n + '×' + n + ' modules · ' + ecl + ' correction'];
    if (ecl !== o.ecl) notes.push('raised to High for the logo');
    var lf = luminance(o.fg), lb = luminance(o.bg), warn = true;
    if (lf > lb) notes.push('light-on-dark codes may not scan in every app');
    else if ((lb + 0.05) / (lf + 0.05) < 3) notes.push('low contrast may not scan reliably');
    else warn = false;
    setStatus(notes.join(' — '), warn);
  }

  function toggleActions(on) {
    actionButtons.forEach(function (b) { b.disabled = !on; });
  }

  // ---------------------------------------------------------------- export

  function fileBase() {
    var slug = (state.type + '-' + state.payload.replace(/^[a-z]+:(\/\/)?/i, ''))
      .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
    return 'qr-' + (slug || 'code');
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
    if (android) {
      android.saveFile(c.toDataURL('image/png').split(',')[1], 'image/png', name);
    } else {
      canvasBlob(c).then(function (b) { saveBlob(b, name); });
    }
    remember();
  }

  function downloadSvg() {
    if (!state.model) return;
    var svg = buildSvg(state.model, readOptions()), name = fileBase() + '.svg';
    if (android) android.saveFile(toBase64(svg), 'image/svg+xml', name);
    else saveBlob(new Blob([svg], { type: 'image/svg+xml' }), name);
    remember();
  }

  function copyImage() {
    if (!state.model) return;
    if (!navigator.clipboard || !window.ClipboardItem) {
      setStatus('Copying images is not supported in this browser — use Download instead.', true);
      return;
    }
    // Pass the promise directly so Safari keeps the user-gesture context.
    navigator.clipboard.write([new ClipboardItem({ 'image/png': canvasBlob(exportCanvas()) })])
      .then(function () { setStatus('Copied to clipboard.'); remember(); },
        function () { setStatus('Could not copy — use Download instead.', true); });
  }

  function shareImage() {
    if (!state.model) return;
    var c = exportCanvas(), name = fileBase() + '.png';
    if (android) {
      android.shareImage(c.toDataURL('image/png').split(',')[1], name);
      remember();
      return;
    }
    canvasBlob(c).then(function (blob) {
      var file = new File([blob], name, { type: 'image/png' });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        return navigator.share({ files: [file], title: 'QR code' }).then(remember, function () {});
      }
      saveBlob(blob, name);
      setStatus('Sharing is not supported here, so the image was downloaded instead.');
    });
  }

  // ---------------------------------------------------------------- history

  var HISTORY_KEY = 'qrgen.history.v1';

  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; } catch (e) { return []; }
  }

  function snapshot() {
    var data = {};
    var fields = document.querySelector('.fields[data-for="' + state.type + '"]');
    Array.prototype.forEach.call(fields.querySelectorAll('[name]'), function (el) {
      data[el.name] = el.type === 'checkbox' ? el.checked : el.value;
    });
    return { type: state.type, data: data, label: state.payload.split(/\r?\n/)[0].slice(0, 80), t: Date.now() };
  }

  function remember() {
    var entry = snapshot();
    var list = loadHistory().filter(function (h) {
      return !(h.type === entry.type && JSON.stringify(h.data) === JSON.stringify(entry.data));
    });
    list.unshift(entry);
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, 12))); } catch (e) { /* storage unavailable */ }
    renderHistory();
  }

  function renderHistory() {
    var list = loadHistory();
    historyList.textContent = '';
    if (!list.length) {
      var li = document.createElement('li');
      li.textContent = 'Codes you download or share appear here.';
      li.style.color = 'var(--muted)';
      li.style.fontSize = '13px';
      historyList.appendChild(li);
    }
    list.forEach(function (h) {
      var li = document.createElement('li'), b = document.createElement('button'), s = document.createElement('small');
      b.type = 'button';
      s.textContent = h.type;
      b.appendChild(s);
      b.appendChild(document.createTextNode(h.label));
      b.title = h.label;
      b.addEventListener('click', function () {
        selectType(h.type);
        Object.keys(h.data).forEach(function (k) {
          var el = form.elements[k];
          if (!el) return;
          if (el.type === 'checkbox') el.checked = h.data[k]; else el.value = h.data[k];
        });
        scheduleRender();
      });
      li.appendChild(b);
      historyList.appendChild(li);
    });
    document.getElementById('clearHistory').hidden = !list.length;
  }

  // ---------------------------------------------------------------- wiring

  function selectType(type) {
    state.type = type;
    Array.prototype.forEach.call(tabs.querySelectorAll('button'), function (b) {
      b.setAttribute('aria-selected', String(b.dataset.type === type));
    });
    Array.prototype.forEach.call(document.querySelectorAll('.fields'), function (f) {
      f.hidden = f.dataset.for !== type;
    });
  }

  tabs.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-type]');
    if (!b) return;
    selectType(b.dataset.type);
    scheduleRender();
  });

  form.addEventListener('input', scheduleRender);
  form.addEventListener('change', scheduleRender);
  form.addEventListener('submit', function (e) { e.preventDefault(); });

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

  document.getElementById('downloadPng').addEventListener('click', downloadPng);
  document.getElementById('downloadSvg').addEventListener('click', downloadSvg);
  document.getElementById('copyImg').addEventListener('click', copyImage);
  document.getElementById('shareImg').addEventListener('click', shareImage);
  document.getElementById('clearHistory').addEventListener('click', function () {
    try { localStorage.removeItem(HISTORY_KEY); } catch (e) { /* storage unavailable */ }
    renderHistory();
  });

  // The Android app saves and shares natively; hide Copy there (WebView has no image clipboard).
  if (android) document.getElementById('copyImg').hidden = true;

  // Offline support for the website (service workers don't run from file:// in the app).
  if ('serviceWorker' in navigator && /^https?:$/.test(location.protocol)) {
    navigator.serviceWorker.register('sw.js').catch(function () {});
  }

  // Prefill from outside — the Android app calls this with text shared from other apps.
  function fill(text) {
    text = String(text || '').trim();
    if (/^https?:\/\/\S+$/i.test(text)) {
      selectType('url');
      form.elements.url.value = text;
    } else {
      selectType('text');
      form.elements.text.value = text;
    }
    scheduleRender();
  }

  window.QRGen = { builders: builders, makeModel: makeModel, buildSvg: buildSvg, select: selectType, fill: fill };

  renderHistory();
  render();
})();
