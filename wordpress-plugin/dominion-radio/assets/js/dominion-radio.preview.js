/* =====================================================================
   Dominion Radio — Frontend player
   
   Polls the Python backend /radio/now endpoint for the shared station
   state, drives an HTML5 <audio> element, and keeps every listener
   synchronised to the same playback position.
   ===================================================================== */

(function () {
  'use strict';

  /* ---------- Constants ---------- */

  var POLL_INTERVAL_MS = 15000;       // How often we refresh /radio/now
  var DRIFT_THRESHOLD_S = 5;          // Seconds of drift before we re-seek
  var REQUEST_TIMEOUT_MS = 10000;     // Fetch timeout

  /* ---------- Small helpers ---------- */

  function fmt(seconds) {
    var s = Math.max(0, Math.floor(seconds || 0));
    var m = Math.floor(s / 60);
    var rem = s % 60;
    return m + ':' + String(rem).padStart(2, '0');
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function absoluteUrl(backendUrl, path) {
    if (!backendUrl) return path;
    if (path && path.indexOf('://') !== -1) return path;
    return backendUrl + path;
  }

  function log(msg) {
    if (window.console && window.console.log) {
      window.console.log('[Dominion Radio] ' + msg);
    }
  }

  /* ---------- Fetch helper ---------- */

  function fetchJson(url, backendUrl) {
    var controller = new AbortController();
    var timeoutId = setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS);

    return fetch(url, {
      cache: 'no-store',
      credentials: 'omit',
      headers: {
        Accept: 'application/json',
        'Cache-Control': 'no-store',
      },
      signal: controller.signal,
    }).then(function (r) {
      clearTimeout(timeoutId);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).catch(function (err) {
      clearTimeout(timeoutId);
      throw err;
    });
  }

  /* ---------- Widget: one player instance ---------- */

  function Widget(root) {
    var self = this;
    this.root = root;

    // Per-instance configuration from data attributes.
    this.backendUrl = root.getAttribute('data-backend-url') || '';
    this.autoplay = root.getAttribute('data-autoplay') === '1';
    this.name = root.getAttribute('data-name') || 'Dominion Radio';

    var q = function (sel) { return root.querySelector(sel); };

    // Element refs — station identity status.
    this.identityStatusDot = q('.dr-identity__status-dot');
    this.identityStatusText = q('.dr-identity__status-text');

    // Element refs — now playing info.
    this.trackTitleEl = q('.dr-player__track-title');
    this.performerEl = q('.dr-player__performer');

    // Element refs — controls.
    this.playBtn = q('.dr-btn--play');
    this.iconPlay = q('.dr-icon--play');
    this.iconPause = q('.dr-icon--pause');
    this.muteBtn = q('.dr-btn--mute');
    this.iconVol = q('.dr-icon--vol');
    this.iconMuted = q('.dr-icon--muted');
    this.progress = q('.dr-player__progress');
    this.progressFill = q('.dr-player__progress-fill');
    this.progressHandle = q('.dr-player__progress-handle');

    // Single elapsed time readout.
    this.timeEl = q('.dr-player__time');

    // Element refs — compact Up next (populated from /radio/now "next").
    this.nextEl = q('.dr-player__next');
    this.nextLabel = q('.dr-player__next-label');
    this.nextTitle = q('.dr-player__next-title');
    this.nextPerformer = q('.dr-player__next-performer');

    // Element refs — offline / retry.
    this.offline = q('.dr-offline');
    this.offlineText = q('.dr-offline__text');
    this.offlineSub = q('.dr-offline__sub');
    this.btnRetry = q('.dr-btn--main');

    this.audio = q('.dr-audio');

    // State.
    this.playing = false;
    this.currentId = null;
    this.currentTrack = null;
    this.loaded = false;
    this.fetching = false;
    this.position = 0;
    this.duration = 0;
    this.serverTimeOffset = 0;
    this.lastPoll = 0;
    this.userHasInteracted = false;
    this.manualSeek = false;
    this.timer = null;

    this.bindPlayerUI();
    this.initAudio();

    var initialBackend = this.backendUrl;
    if (initialBackend) {
      this.loadNow();
    } else {
      this.showOffline('Backend URL not configured.');
    }

    this.schedule();
  }

  /* ---------- Scheduling ---------- */

  Widget.prototype.schedule = function () {
    var self = this;
    clearTimeout(this.timer);
    this.timer = setTimeout(function () { self.tick(); }, POLL_INTERVAL_MS);
  };

  /* ---------- Backend: /radio/now ---------- */

  Widget.prototype.tick = function () {
    var self = this;
    clearTimeout(this.timer);
    if (this.fetching) { this.schedule(); return; }
    if (!this.backendUrl) { this.schedule(); return; }

    var url = absoluteUrl(this.backendUrl, '/radio/now');
    this.fetching = true;

    fetchJson(url, this.backendUrl)
      .then(function (data) {
        self.lastPoll = Date.now();
        self.applyNowData(data);
      })
      .catch(function (err) {
        log('Now fetch failed: ' + err.message);
        self.showOffline('Unable to reach the radio backend.');
      })
      .finally(function () {
        self.fetching = false;
        self.schedule();
      });
  };

  Widget.prototype.applyNowData = function (data) {
    if (!data || !data.track) {
      this.showOffline('No track available.');
      return;
    }

    var track = data.track;
    var isLive = data.status === 'live' && !!track.id;

    // Update station identity status (live / not live).
    if (isLive) {
      this.identityStatusDot.parentElement.classList.add('dr-identity__status--live');
      if (this.identityStatusText) this.identityStatusText.textContent = 'ON AIR';
      if (this.identityStatusDot) this.identityStatusDot.style.background = '#e5645a';
    } else {
      this.identityStatusDot.parentElement.classList.remove('dr-identity__status--live');
      if (this.identityStatusText) this.identityStatusText.textContent = 'NOT LIVE';
      if (this.identityStatusDot) this.identityStatusDot.style.background = '#6c6455';
    }

    // New track?
    if (track.id !== this.currentId) {
      this.currentId = track.id;
      this.currentTrack = track;

      if (this.trackTitleEl) this.trackTitleEl.textContent = track.title || 'Unknown Track';
      if (this.performerEl) this.performerEl.textContent = track.performer || '';

      // Compact Up next from the /radio/now "next" field (no playlist fetch).
      var nextTrack = data.next || null;
      this.renderNext(nextTrack);

      // Load audio source if changed.
      var audioUrl = absoluteUrl(this.backendUrl, track.audio_url);
      if (this.audio.src !== audioUrl) {
        this.audio.src = audioUrl;
        this.loaded = false;
      }

      // Sync position.
      var serverStarted = data.started_at ? new Date(data.started_at).getTime() : 0;
      var serverNow = data.server_time ? new Date(data.server_time).getTime() : Date.now();
      this.serverTimeOffset = serverNow - Date.now();
      this.position = Math.max(0, (data.position || 0));
      this.duration = track.duration || 0;

      this.updateDisplay();
    } else {
      // Same track: update position and Up next only.
      this.position = Math.max(0, (data.position || 0));
      this.duration = track.duration || 0;
      var nextTrack = data.next || null;
      this.renderNext(nextTrack);
      this.updateDisplay();
    }

    this.hideOffline();
  };

  Widget.prototype.renderNext = function (nextTrack) {
    // Hide the Up next section entirely when there is no next track.
    if (!this.nextEl || !nextTrack) {
      if (this.nextEl) this.nextEl.hidden = true;
      return;
    }
    if (this.nextEl) this.nextEl.hidden = false;
    if (this.nextTitle) this.nextTitle.textContent = nextTrack.title || 'Unknown Track';
    if (this.nextPerformer) {
      this.nextPerformer.textContent = nextTrack.performer ? nextTrack.performer : '';
      if (this.nextPerformer.textContent === '') {
        this.nextPerformer.hidden = true;
      } else {
        this.nextPerformer.hidden = false;
      }
    }
  };

  /* ---------- Audio ---------- */

  Widget.prototype.initAudio = function () {
    var self = this;
    var audio = this.audio;

    audio.addEventListener('loadedmetadata', function () {
      self.loaded = true;
      self.duration = audio.duration || self.duration;
      var target = Math.min(self.position, audio.duration - 0.5);
      if (target > 0 && !isNaN(target)) {
        try { audio.currentTime = target; } catch (e) {}
      }
      self.updateDisplay();
    });

    audio.addEventListener('play', function () {
      self.playing = true;
      self.updatePlayButton();
    });

    audio.addEventListener('pause', function () {
      self.playing = false;
      self.updatePlayButton();
    });

    audio.addEventListener('ended', function () {
      self.playing = false;
      self.updatePlayButton();
    });

    audio.addEventListener('error', function () {
      log('Audio element error: ' + (audio.error ? audio.error.message : 'unknown'));
      self.showOffline('Unable to play this track.');
    });

    audio.addEventListener('timeupdate', function () {
      if (self.manualSeek) return;
      self.position = audio.currentTime;
      self.updateDisplay();
    });
  };

  Widget.prototype.play = function () {
    var self = this;
    if (!self.audio.src) return;
    var p = self.audio.play();
    if (p && typeof p.then === 'function') {
      p.catch(function (err) {
        if (err.name === 'NotAllowedError') {
          self.playing = false;
          self.updatePlayButton();
        }
      });
    }
  };

  Widget.prototype.pause = function () {
    this.audio.pause();
  };

  Widget.prototype.toggle = function () {
    if (this.audio.paused || this.audio.ended) {
      this.play();
    } else {
      this.pause();
    }
  };

  Widget.prototype.toggleMute = function () {
    this.audio.muted = !this.audio.muted;
    this.updateMuteButton();
  };

  Widget.prototype.updatePlayButton = function () {
    var playing = this.playing && !this.audio.paused;
    if (this.iconPlay) this.iconPlay.hidden = playing;
    if (this.iconPause) this.iconPause.hidden = !playing;
    if (this.playBtn) {
      this.playBtn.setAttribute('aria-pressed', playing ? 'true' : 'false');
      this.playBtn.setAttribute('aria-label', playing ? 'Pause' : 'Play');
    }
  };

  Widget.prototype.updateMuteButton = function () {
    if (this.iconVol) this.iconVol.hidden = this.audio.muted;
    if (this.iconMuted) this.iconMuted.hidden = !this.audio.muted;
    if (this.muteBtn) {
      this.muteBtn.setAttribute('aria-pressed', this.audio.muted ? 'true' : 'false');
      this.muteBtn.setAttribute('aria-label', this.audio.muted ? 'Unmute' : 'Mute');
    }
  };

  /* ---------- Display ---------- */

  Widget.prototype.updateDisplay = function () {
    if (!this.currentTrack) return;

    var dur = this.duration || 0;
    var pos = this.position;
    if (pos > dur) pos = dur;
    if (pos < 0) pos = 0;

    if (this.timeEl) this.timeEl.textContent = fmt(pos);

    var pct = dur > 0 ? (pos / dur) * 100 : 0;
    if (this.progressFill) this.progressFill.style.width = pct + '%';
    if (this.progressHandle) this.progressHandle.style.left = pct + '%';

    // Re-seek if drift exceeds threshold (only when playing).
    if (this.playing && !this.manualSeek && this.loaded && this.audio.duration) {
      var expected = this.position + (Date.now() - this.lastPoll) / 1000;
      var actual = this.audio.currentTime;
      if (Math.abs(expected - actual) > DRIFT_THRESHOLD_S) {
        try { this.audio.currentTime = expected; } catch (e) {}
      }
    }
  };

  /* ---------- UI states ---------- */

  Widget.prototype.showOffline = function (msg) {
    if (this.offline) this.offline.hidden = false;
    if (this.offlineText) this.offlineText.textContent = msg || 'Radio is not available.';
    if (this.offlineSub) this.offlineSub.textContent = '';
    var card = this.root.querySelector('.dr-player__card');
    if (card) card.style.opacity = '0.4';
  };

  Widget.prototype.hideOffline = function () {
    if (this.offline) this.offline.hidden = true;
    var card = this.root.querySelector('.dr-player__card');
    if (card) card.style.opacity = '1';
  };

  /* ---------- UI bindings ---------- */

  Widget.prototype.bindPlayerUI = function () {
    var self = this;

    this.playBtn.addEventListener('click', function () { self.toggle(); });
    this.muteBtn.addEventListener('click', function () { self.toggleMute(); });
    this.btnRetry.addEventListener('click', function () {
      self.hideOffline();
      self.loadNow();
    });

    // Progress bar click: local seek only (does not affect backend).
    this.progress.addEventListener('click', function (e) {
      if (!self.loaded || !self.audio.duration) return;
      var rect = self.progress.getBoundingClientRect();
      var x = e.clientX - rect.left;
      var pct = Math.max(0, Math.min(1, x / rect.width));
      self.manualSeek = true;
      try { self.audio.currentTime = pct * self.audio.duration; } catch (e) {}
      self.updateDisplay();
      setTimeout(function () { self.manualSeek = false; }, 500);
    });

    // Keyboard support.
    this.playBtn.addEventListener('keydown', function (e) {
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault();
        self.toggle();
      }
    });

    // Re-check when tab becomes visible.
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { self.loadNow(); }
    });
  };

  /* ---------- PREVIEW-ONLY SHIM: the shipped file never defines loadNow() ---------- */
  Widget.prototype.loadNow = function () { this.tick(); };

  /* ---------- Boot ---------- */

  function boot() {
    var nodes = document.querySelectorAll('.dr-radio');
    for (var i = 0; i < nodes.length; i++) {
      new Widget(nodes[i]);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
