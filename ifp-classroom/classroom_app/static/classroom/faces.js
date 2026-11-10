/* Shared face helpers for the enrolment pages and the classroom kiosk.
 * Uses face-api (vladmandic build, MIT) served from /static/faceapi/, so it
 * works without internet access. */
(function () {
  'use strict';
  var loaded = {};

  function base() {
    var el = document.querySelector('[data-face-models]');
    return el ? el.getAttribute('data-face-models') : '/static/faceapi/model';
  }

  var FaceKit = {
    ready: false,

    /* Load the detector ("ssd" or "tiny") plus landmark and recognition models. */
    load: async function (detector) {
      if (typeof faceapi === 'undefined') throw new Error('face-api did not load');
      if (!loaded.backend) {
        try { await faceapi.tf.setBackend('webgl'); } catch (e) { /* falls back to cpu/wasm */ }
        await faceapi.tf.ready();
        loaded.backend = true;
      }
      var url = base();
      var jobs = [];
      if (detector === 'tiny' && !loaded.tiny) { jobs.push(faceapi.nets.tinyFaceDetector.loadFromUri(url)); loaded.tiny = true; }
      if (detector !== 'tiny' && !loaded.ssd) { jobs.push(faceapi.nets.ssdMobilenetv1.loadFromUri(url)); loaded.ssd = true; }
      if (!loaded.rest) {
        jobs.push(faceapi.nets.faceLandmark68Net.loadFromUri(url));
        jobs.push(faceapi.nets.faceRecognitionNet.loadFromUri(url));
        loaded.rest = true;
      }
      await Promise.all(jobs);
      FaceKit.ready = true;
    },

    options: function (detector, minConfidence) {
      if (detector === 'tiny') return new faceapi.TinyFaceDetectorOptions({ inputSize: 608, scoreThreshold: minConfidence || 0.45 });
      return new faceapi.SsdMobilenetv1Options({ minConfidence: minConfidence || 0.5, maxResults: 100 });
    },

    /* All faces in a video frame / image, with 128-number descriptors. */
    detectAll: function (input, detector, minConfidence) {
      return faceapi.detectAllFaces(input, FaceKit.options(detector, minConfidence)).withFaceLandmarks().withFaceDescriptors();
    },

    /* The one main face in an enrolment photo. Rejects photos with no face or
     * with two faces of similar size (it would be unclear who is meant). */
    describeOne: async function (input) {
      var all = await FaceKit.detectAll(input, 'ssd', 0.5);
      if (!all.length) throw new Error('no face found - use a clear, front-facing photo');
      all.sort(function (a, b) { return area(b) - area(a); });
      if (all.length > 1 && area(all[1]) > 0.4 * area(all[0])) throw new Error('more than one face in the photo - crop it to one person');
      if (all[0].detection.box.width < 60) throw new Error('the face is too small - use a closer photo');
      return all[0];
    },

    /* Small square JPEG of the face for the admin pages. */
    thumbnail: function (input, box, size) {
      size = size || 112;
      var pad = box.width * 0.35;
      var sx = Math.max(0, box.x - pad), sy = Math.max(0, box.y - pad);
      var sw = box.width + 2 * pad, sh = box.height + 2 * pad;
      var c = document.createElement('canvas');
      c.width = c.height = size;
      c.getContext('2d').drawImage(input, sx, sy, sw, sh, 0, 0, size, size);
      return c.toDataURL('image/jpeg', 0.8);
    },

    /* Read an image file, shrunk so detection stays fast on big phone photos. */
    imageFromFile: function (file, maxSide) {
      maxSide = maxSide || 1280;
      return new Promise(function (resolve, reject) {
        var img = new Image();
        var url = URL.createObjectURL(file);
        img.onload = function () {
          URL.revokeObjectURL(url);
          var scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
          var c = document.createElement('canvas');
          c.width = Math.round(img.naturalWidth * scale);
          c.height = Math.round(img.naturalHeight * scale);
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          resolve(c);
        };
        img.onerror = function () { URL.revokeObjectURL(url); reject(new Error('not an image this browser can read')); };
        img.src = url;
      });
    },

    /* Copy the current video frame to a canvas. */
    frame: function (video) {
      var c = document.createElement('canvas');
      c.width = video.videoWidth; c.height = video.videoHeight;
      c.getContext('2d').drawImage(video, 0, 0);
      return c;
    },

    cameraError: function (err) {
      if (!window.isSecureContext) {
        return 'The browser only allows the camera on HTTPS pages. Either open this page with https://, ' +
          'or in Chrome open chrome://flags, search "Insecure origins treated as secure", enter ' + location.origin +
          ', choose Enabled and tap Relaunch.';
      }
      if (err && err.name === 'NotAllowedError') return 'Camera permission was refused. Allow the camera for this site in the browser settings.';
      if (err && err.name === 'NotFoundError') return 'No camera found. Check that the panel camera is connected.';
      if (err && err.name === 'NotReadableError') return 'The camera is in use by another app (e.g. a video call). Close it and reload.';
      return 'Could not start the camera: ' + (err && err.message ? err.message : err);
    },

    post: async function (url, fields) {
      var body = new FormData();
      Object.keys(fields).forEach(function (k) { body.append(k, fields[k]); });
      var resp = await fetch(url, { method: 'POST', body: body, credentials: 'same-origin' });
      var data = {};
      try { data = await resp.json(); } catch (e) { /* non-JSON error page */ }
      if (!resp.ok || !data.ok) throw new Error(data.error || ('server returned ' + resp.status));
      return data;
    }
  };

  function area(d) { return d.detection.box.width * d.detection.box.height; }

  window.FaceKit = FaceKit;
})();
