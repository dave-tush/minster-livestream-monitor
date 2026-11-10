/* =====================================================================
   Dominion Radio — frontend player

   Backend contract (verified against the FastAPI source and a running instance):
     GET {backend}/radio/now  ->  200 {
         station, track:{id,title,performer,duration,audio_url}, position, remaining,
         started_at, next:{id,title,performer,duration,audio_url}|null, server_time,
         tracks_in_rotation }
                               ->  503 when the station has no audio yet
     GET {backend}/radio/audio/{id}  ->  audio file, supports HTTP Range (206)
   audio_url is root-relative ("/radio/audio/101") and is resolved against the backend URL.

   The backend does not return a "status" field; a 200 response with a track means "on air".
   No secrets live in this file. The admin API key is only ever used server-side (PHP).
   ===================================================================== */

(function () {
  'use strict';

  var POLL_MS = 15000;                 // refresh interval while the page is in use
  var TIMEOUT_MS = 10000;              // per-request timeout
  var DRIFT_S = 4;                     // re-sync to the station if audio drifts further than this
  var OFF_LIVE_S = 3;                  // seeking further than this from live shows "Back to live"
  var BACKOFF_MS = [3000, 6000, 12000, 20000, 30000];
  var MAX_AUDIO_RETRIES = 3;

  var instances = [];
  var inflight = {};                   // one in-flight /radio/now request per backend URL

  /* ---------- helpers ---------- */

  function fmt(sec) {
    var s = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var r = s % 60;
    return h > 0 ? h + ':' + (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r
                 : m + ':' + (r < 10 ? '0' : '') + r;
  }

  function spoken(sec) {
    var s = Math.max(0, Math.floor(sec || 0));
    var m = Math.floor(s / 60);
    var r = s % 60;
    var out = [];
    if (m) { out.push(m + (m === 1 ? ' minute' : ' minutes')); }
    if (r || !m) { out.push(r + (r === 1 ? ' second' : ' seconds')); }
    return out.join(' ');
  }

  function normalizeBase(raw) {
    try {
      var u = new URL(raw);
      if (u.protocol !== 'https:' && u.protocol !== 'http:') { return ''; }
      return (u.origin + u.pathname).replace(/\/+$/, '');
    } catch (e) {
      return '';
    }
  }

  function resolveAudio(base, path) {
    var url;
    if (/^https?:\/\//i.test(path)) { url = path; }
    else if (path.charAt(0) === '/') { url = base + path; }
    else { url = base + '/' + path; }
    try { return new URL(url).href; } catch (e) { return ''; }
  }

  function err(kind, status) {
    var e = new Error(kind);
    e.kind = kind;
    e.status = status || 0;
    return e;
  }

  // `hidden` is not a reflected property on SVG elements, so toggle the attribute.
  function setHidden(el, hide) {
    if (!el) { return; }
    if (hide) { el.setAttribute('hidden', ''); } else { el.removeAttribute('hidden'); }
  }

  function str(v, max) {
    return typeof v === 'string' ? v.slice(0, max || 300) : '';
  }

  /* ---------- backend ---------- */

  function parseNow(d) {
    if (!d || typeof d !== 'object' || !d.track || typeof d.track !== 'object') { throw err('bad'); }
    var t = d.track;
    if (typeof t.id !== 'number' || typeof t.audio_url !== 'string' || !(t.duration > 0) || typeof d.position !== 'number') {
      throw err('bad');
    }
    var next = null;
    if (d.next && typeof d.next === 'object' && typeof d.next.id === 'number') {
      next = { id: d.next.id, title: str(d.next.title), performer: str(d.next.performer) };
    }
    return {
      id: t.id,
      title: str(t.title) || 'Untitled',
      performer: str(t.performer),
      duration: t.duration,
      audioPath: t.audio_url,
      position: Math.max(0, d.position),
      next: next
    };
  }

  function fetchNow(base) {
    if (inflight[base]) { return inflight[base]; }
    var ctrl = new AbortController();
    var timedOut = false;
    var timer = setTimeout(function () { timedOut = true; ctrl.abort(); }, TIMEOUT_MS);
    var t0 = performance.now();

    var p = fetch(base + '/radio/now', {
      method: 'GET',
      mode: 'cors',
      cache: 'no-store',
      credentials: 'omit',
      headers: { Accept: 'application/json' },   // simple request: no CORS preflight needed
      signal: ctrl.signal
    }).then(function (r) {
      if (r.status === 503) { throw err('unavailable', 503); }
      if (!r.ok) { throw err('http', r.status); }
      return r.json().then(function (d) {
        var at = performance.now();
        return { now: parseNow(d), rtt: (at - t0) / 1000, at: at };
      }, function () { throw err('bad'); });
    }, function () {
      throw err(timedOut ? 'timeout' : 'network');
    }).then(function (v) {
      clearTimeout(timer); delete inflight[base]; return v;
    }, function (e) {
      clearTimeout(timer); delete inflight[base]; throw e;
    });

    inflight[base] = p;
    return p;
  }

  /* ---------- one player ---------- */

  function Player(root) {
    this.root = root;
    this.base = normalizeBase(root.getAttribute('data-backend-url') || '');
    this.name = root.getAttribute('data-name') || 'Dominion Radio';
    this.offlineMsg = root.getAttribute('data-offline-message') || 'Dominion Radio is not on air right now.';

    var self = this;
    this.el = {};
    ['onair', 'status', 'notice', 'notice-title', 'notice-sub', 'retry', 'card', 'play', 'play-label', 'golive', 'title', 'performer',
      'seek', 'elapsed', 'total', 'mute', 'next', 'next-title', 'next-performer', 'announce', 'audio']
      .forEach(function (k) { self.el[k] = root.querySelector('[data-dr="' + k + '"]'); });
    this.audio = this.el.audio;
    this.icoPlay = root.querySelector('.dr-icon--play');
    this.icoPause = root.querySelector('.dr-icon--pause');
    this.icoVol = root.querySelector('.dr-icon--vol');
    this.icoMuted = root.querySelector('.dr-icon--muted');

    this.mode = this.base ? 'loading' : 'config';   // config | loading | ok | unavailable | offline | error
    this.errorKind = '';                            // 'audio' when the failure came from the audio element
    this.station = null;                            // { id,title,performer,duration,audioUrl,pos,anchor }
    this.next = null;
    this.wantPlay = false;                          // the listener's intent: playing the live stream
    this.buffering = false;
    this.switching = false;
    this.offLive = false;
    this.dragging = false;
    this.dragPos = 0;
    this.audioId = null;                            // track id currently loaded in <audio>
    this.pendingSeek = null;
    this.needsReload = false;
    this.failCount = 0;
    this.audioFails = 0;
    this.lastOk = 0;
    this.pollTimer = null;
    this.lastAnnounce = '';
    this.lastAudioErr = 0;
    this.suppressPauseUntil = 0;

    this.bind();
    this.render();
    if (this.mode === 'config') { return; }
    if (!window.fetch || !window.AbortController || !window.URL) {
      this.mode = 'error';
      this.render();
      return;
    }
    instances.push(this);
    this.poll();
  }

  /* ----- backend polling ----- */

  Player.prototype.livePos = function () {
    if (!this.station) { return 0; }
    var p = this.station.pos + (performance.now() - this.station.anchor) / 1000;
    return Math.min(Math.max(0, p), this.station.duration);
  };

  Player.prototype.poll = function () {
    var self = this;
    clearTimeout(this.pollTimer);
    if (!this.base) { return; }
    fetchNow(this.base).then(function (res) {
      self.failCount = 0;
      self.lastOk = Date.now();
      self.onNow(res);
      self.scheduleNext();
    }, function (e) {
      self.onFetchError(e);
      self.scheduleRetry();
    });
  };

  Player.prototype.scheduleNext = function () {
    var self = this;
    clearTimeout(this.pollTimer);
    if (document.hidden && !this.wantPlay) { return; }          // resumes on visibilitychange
    var delay = POLL_MS;
    if (this.station) {
      var untilEnd = (this.station.duration - this.livePos()) * 1000 + 800;
      if (untilEnd < delay) { delay = Math.max(1000, untilEnd); }   // refresh right at the track boundary
    }
    this.pollTimer = setTimeout(function () { self.poll(); }, delay);
  };

  Player.prototype.scheduleRetry = function () {
    var self = this;
    clearTimeout(this.pollTimer);
    if (document.hidden && !this.wantPlay) { return; }
    var delay = BACKOFF_MS[Math.min(this.failCount - 1, BACKOFF_MS.length - 1)];
    this.pollTimer = setTimeout(function () { self.poll(); }, delay);
  };

  Player.prototype.onFetchError = function (e) {
    this.failCount++;
    this.errorKind = '';
    if (e.kind === 'unavailable') { this.mode = 'unavailable'; }
    else if (e.kind === 'network' || e.kind === 'timeout') { this.mode = 'offline'; }
    else { this.mode = 'error'; }      // 'bad' (unusable body) or an unexpected HTTP status
    this.render();
  };

  Player.prototype.onNow = function (res) {
    var n = res.now;
    var changed = !this.station || this.station.id !== n.id;
    this.station = {
      id: n.id, title: n.title, performer: n.performer, duration: n.duration,
      audioUrl: resolveAudio(this.base, n.audioPath),
      pos: n.position + res.rtt / 2,                 // position at the moment the response arrived
      anchor: res.at
    };
    this.next = n.next;
    this.mode = 'ok';
    this.errorKind = '';

    if (changed) {
      this.offLive = false;
      this.updateMeta();
      if (this.wantPlay) {
        this.announce('Now playing ' + n.title + (n.performer ? ' by ' + n.performer : ''));
      }
    }
    if (!this.station.audioUrl) {
      this.mode = 'error';
    } else if (this.wantPlay && (changed || this.needsReload || this.audioId !== n.id)) {
      this.needsReload = false;
      this.loadAndPlay();
    } else if (!this.wantPlay && changed && this.audioId !== null) {
      this.audio.removeAttribute('src');             // free the previous track; next play loads fresh
      this.audio.load();
      this.audioId = null;
    }
    this.render();
  };

  /* ----- playback ----- */

  Player.prototype.loadAndPlay = function () {
    if (!this.station) { return; }
    this.switching = true;
    this.audioId = this.station.id;
    this.pendingSeek = this.livePos();
    this.audio.src = this.station.audioUrl;
    this.startPlayback();
  };

  Player.prototype.startPlayback = function () {
    var self = this;
    var p;
    try { p = this.audio.play(); } catch (e) { p = null; }
    if (p && typeof p.then === 'function') {
      p.then(function () { self.switching = false; }, function (e) {
        self.switching = false;
        if (e && e.name === 'AbortError') { return; }     // superseded by a newer load/pause
        if (e && e.name === 'NotAllowedError') {
          self.wantPlay = false;
          self.announce('Press Join the Room to start listening.');
        } else {
          self.onAudioError();
        }
        self.render();
      });
    } else {
      this.switching = false;
    }
  };

  Player.prototype.play = function () {
    if (!this.station || this.mode !== 'ok') { this.poll(); return; }
    instances.forEach(function (o) { if (o !== this && o.wantPlay) { o.pause(); } }, this);
    this.wantPlay = true;
    this.offLive = false;
    this.audioFails = 0;
    var live = this.livePos();
    if (live >= this.station.duration - 0.5) {            // track is about to change: get the new one first
      this.render();
      this.poll();
      return;
    }
    if (this.audioId === this.station.id && this.audio.getAttribute('src') && !this.audio.error) {
      if (this.audio.readyState >= 1) {
        if (Math.abs(this.audio.currentTime - live) > 2) { this.audio.currentTime = live; }
      } else {
        this.pendingSeek = live;
      }
      this.startPlayback();
    } else {
      this.loadAndPlay();
    }
    this.render();
  };

  Player.prototype.pause = function () {
    this.wantPlay = false;
    this.buffering = false;
    this.offLive = false;
    this.audio.pause();
    this.render();
  };

  Player.prototype.toggle = function () {
    if (this.wantPlay) { this.pause(); } else { this.play(); }
  };

  Player.prototype.goLive = function () {
    if (!this.station || !this.wantPlay) { return; }
    this.offLive = false;
    this.seekTo(this.livePos(), true);
    this.render();
  };

  Player.prototype.seekTo = function (t, silent) {
    if (!this.station) { return; }
    var max = this.station.duration;
    if (isFinite(this.audio.duration) && this.audio.duration > 0) { max = Math.min(max, this.audio.duration); }
    t = Math.min(Math.max(0, t), Math.max(0, max - 0.25));
    if (this.audioId === this.station.id && this.audio.readyState >= 1) {
      try { this.audio.currentTime = t; } catch (e) { /* ignore */ }
    } else {
      this.pendingSeek = t;
    }
    if (!silent) { this.offLive = Math.abs(t - this.livePos()) > OFF_LIVE_S; }
    this.paint(t);
  };

  // A pause caused by the player itself or by a media error (browsers dispatch 'pause' after 'error').
  // The 'pause' listener ignores these so they are not mistaken for an outside pause (media keys, headset).
  Player.prototype.quietPause = function () {
    this.suppressPauseUntil = Date.now() + 600;
    if (!this.audio.paused) { this.audio.pause(); }
  };

  Player.prototype.onAudioError = function () {
    var now = Date.now();
    if (now - this.lastAudioErr < 800) { return; }   // the 'error' event and the play() rejection report the same failure
    this.lastAudioErr = now;
    this.quietPause();
    this.buffering = false;
    this.audioFails++;
    if (this.audioFails <= MAX_AUDIO_RETRIES && this.wantPlay) {
      // Soft recovery: ask the station what is on air now, then reload.
      var self = this;
      this.needsReload = true;
      this.mode = 'offline';
      this.errorKind = 'soft';
      clearTimeout(this.pollTimer);
      this.pollTimer = setTimeout(function () { self.poll(); }, 1500 * this.audioFails);
    } else {
      this.wantPlay = false;
      this.mode = 'error';
      this.errorKind = 'audio';
      clearTimeout(this.pollTimer);
    }
    this.render();
  };

  /* ----- events ----- */

  Player.prototype.bind = function () {
    var self = this;
    var a = this.audio;
    var seek = this.el.seek;

    this.el.play.addEventListener('click', function () { self.toggle(); });
    this.el.mute.addEventListener('click', function () { a.muted = !a.muted; });
    this.el.golive.addEventListener('click', function () { self.goLive(); });
    this.el.retry.addEventListener('click', function () {
      self.failCount = 0; self.audioFails = 0; self.errorKind = '';
      if (self.mode !== 'ok') { self.mode = 'loading'; }
      self.render();
      self.poll();
    });

    a.addEventListener('volumechange', function () { self.render(); });
    a.addEventListener('loadedmetadata', function () {
      if (self.pendingSeek !== null) {
        var max = isFinite(a.duration) ? a.duration - 0.25 : self.pendingSeek;
        try { a.currentTime = Math.max(0, Math.min(self.pendingSeek, max)); } catch (e) { /* ignore */ }
        self.pendingSeek = null;
      }
    });
    a.addEventListener('play', function () { self.buffering = a.readyState < 3; self.render(); });
    a.addEventListener('playing', function () {
      self.buffering = false; self.audioFails = 0; self.switching = false; self.render();
    });
    a.addEventListener('waiting', function () { if (self.wantPlay) { self.buffering = true; self.render(); } });
    a.addEventListener('canplay', function () { if (self.buffering) { self.buffering = false; self.render(); } });
    a.addEventListener('pause', function () {
      if (Date.now() < self.suppressPauseUntil) { return; }
      if (self.switching || a.ended || !self.wantPlay) { return; }
      self.wantPlay = false;                  // paused from outside the page (media keys, headset, OS)
      self.buffering = false;
      self.render();
    });
    a.addEventListener('ended', function () {
      if (!self.wantPlay) { return; }
      self.poll();                            // the station should already be on the next track
    });
    a.addEventListener('error', function () {
      if (!a.getAttribute('src')) { return; } // ignore the error raised by clearing src
      self.onAudioError();
    });

    // Seek: native <input type="range">. The browser provides keyboard,
    // screen-reader and touch interaction; we only track the drag state
    // so the periodic tick does not override the user's position.
    seek.addEventListener('input', function () {
      if (seek.disabled || !self.station) { return; }
      self.dragging = true;
      self.dragPos = parseFloat(seek.value) || 0;
      self.paint(self.dragPos);
    });
    seek.addEventListener('change', function () {
      if (seek.disabled || !self.station) { self.dragging = false; return; }
      self.dragPos = parseFloat(seek.value) || 0;
      self.dragging = false;
      self.seekTo(self.dragPos);
      self.render();
    });

    // Media keys / lock-screen controls
    if ('mediaSession' in navigator) {
      try {
        navigator.mediaSession.setActionHandler('play', function () { self.play(); });
        navigator.mediaSession.setActionHandler('pause', function () { self.pause(); });
      } catch (e) { /* unsupported action */ }
    }
  };

  /* ----- rendering ----- */

  Player.prototype.currentPos = function () {
    if (this.dragging) { return this.dragPos; }
    if (this.wantPlay && this.station && this.audioId === this.station.id && this.audio.readyState >= 1) {
      return this.audio.currentTime;
    }
    return this.livePos();
  };

  Player.prototype.updateMeta = function () {
    var s = this.station;
    if (!s) { return; }
    this.el.title.textContent = s.title;
    this.el.performer.textContent = s.performer;
    if ('mediaSession' in navigator && window.MediaMetadata) {
      try {
        navigator.mediaSession.metadata = new MediaMetadata({ title: s.title, artist: s.performer || this.name, album: this.name });
      } catch (e) { /* ignore */ }
    }
  };

  Player.prototype.announce = function (msg) {
    if (!msg || msg === this.lastAnnounce) { return; }
    this.lastAnnounce = msg;
    var el = this.el.announce;
    el.textContent = '';
    setTimeout(function () { el.textContent = msg; }, 60);
  };

  // Progress and time only (called every 250 ms and while dragging).
  Player.prototype.paint = function (pos) {
    var s = this.station;
    var dur = s ? s.duration : 0;
    if (pos === undefined) { pos = this.currentPos(); }
    pos = Math.min(Math.max(0, pos), dur);
    var pct = dur > 0 ? (pos / dur) * 100 : 0;
    var seek = this.el.seek;
    if (seek && seek.type === 'range') {
      try { seek.max = String(dur); } catch (e) { /* ignore */ }
      if (document.activeElement !== seek || !this.dragging) {
        try { seek.value = String(pos); } catch (e) { /* ignore */ }
      }
      seek.style.setProperty('--p', pct + '%');
      seek.setAttribute('aria-valuemax', String(Math.floor(dur)));
      seek.setAttribute('aria-valuenow', String(Math.floor(pos)));
      seek.setAttribute('aria-valuetext', spoken(pos) + ' of ' + spoken(dur));
    }
    var e = fmt(pos), t = fmt(dur);
    if (this.el.elapsed && this.el.elapsed.textContent !== e) { this.el.elapsed.textContent = e; }
    if (this.el.total && this.el.total.textContent !== t) { this.el.total.textContent = t; }
  };

  Player.prototype.tick = function () {
    if (!this.station) { return; }
    this.paint();
    // keep the listener in step with the station, unless they deliberately moved away from live
    if (this.wantPlay && !this.buffering && !this.offLive && !this.dragging && !this.switching &&
        this.audioId === this.station.id && this.audio.readyState >= 2 && !this.audio.seeking) {
      var live = this.livePos();
      if (live < this.station.duration - 1 && Math.abs(this.audio.currentTime - live) > DRIFT_S) {
        try { this.audio.currentTime = live; } catch (e) { /* ignore */ }
      }
    }
  };

  Player.prototype.render = function () {
    var m = this.mode;
    var hasTrack = !!this.station;
    var listening = this.wantPlay && hasTrack;
    var failing = m === 'offline' || m === 'unavailable' || m === 'error';
    var soft = failing && listening;                               // keep the player usable while reconnecting
    var empty = failing && !soft;

    var state;
    if (m === 'config') { state = 'config'; }
    else if (m === 'loading') { state = 'loading'; }
    else if (failing && !soft) { state = m; }
    else { state = this.buffering ? 'buffering' : (listening ? 'playing' : 'ready'); }
    this.root.setAttribute('data-state', state);
    this.root.setAttribute('data-empty', empty || m === 'config' ? '1' : '0');
    // Local playback status kept separate from station (backend) status:
    // data-state reflects the station; data-local reflects this visitor's player.
    var local = this.buffering ? 'buffering' : (listening ? 'playing' : 'paused');
    this.root.setAttribute('data-local', local);

    var labels = { config: 'Not set up', loading: 'Connecting', ready: 'On air', playing: 'On air', buffering: 'On air',
                   unavailable: 'Off air', offline: 'Offline', error: 'Unavailable' };
    this.el.status.textContent = labels[state] || '';

    // notice
    var title = '', sub = '', tone = '';
    if (m === 'config') {
      title = this.offlineMsg; sub = '';
    } else if (soft) {
      title = 'Reconnecting…'; tone = 'soft';
    } else if (m === 'unavailable') {
      title = this.offlineMsg; sub = 'The station is preparing its next broadcast. We will keep checking.';
    } else if (m === 'offline') {
      title = 'We can’t reach ' + this.name + ' right now.'; sub = 'Please check your internet connection. We will keep trying and reconnect automatically.';
    } else if (m === 'error') {
      if (this.errorKind === 'audio') {
        title = 'This track can’t be played right now.'; sub = 'Please try again in a moment. The station will move on to the next track on its own.';
      } else {
        title = 'The station isn’t responding correctly.'; sub = 'Please try again in a moment.';
      }
    }
    this.el.notice.hidden = !title;
    this.el.notice.setAttribute('data-tone', tone);
    this.el['notice-title'].textContent = title;
    this.el['notice-sub'].textContent = sub;
    this.el.retry.hidden = m === 'config' || m === 'loading' || soft;
    if (title && state !== this.lastNoticeState) { this.announce(title + ' ' + sub); }
    this.lastNoticeState = state;

    // card contents
    this.el.next.hidden = !(this.next && hasTrack && !empty);
    if (this.next) {
      this.el['next-title'].textContent = this.next.title;
      this.el['next-performer'].textContent = this.next.performer;
    }
    if (!hasTrack) {
      this.el.title.textContent = m === 'config' ? '' : 'Connecting…';
      this.el.performer.textContent = '';
    } else {
      this.el.title.textContent = this.station.title;
      this.el.performer.textContent = this.station.performer;
    }

    // controls
    var canPlay = hasTrack && (m === 'ok' || soft);
    this.el.play.disabled = !canPlay;
    // Visible primary action: Join the Room / Joining… / Leave Room.
    // Retry (Try Again) lives in the notice for error states.
    var playLabel = listening ? 'Leave Room' : (this.buffering ? 'Joining…' : (state === 'loading' ? 'Connecting…' : 'Join the Room'));
    this.el.play.setAttribute('aria-label', playLabel);
    if (this.el['play-label']) { this.el['play-label'].textContent = playLabel; }
    setHidden(this.icoPlay, listening);
    setHidden(this.icoPause, !listening);

    var seekOn = listening && !empty && this.audioId === (this.station && this.station.id);
    this.el.seek.setAttribute('aria-disabled', seekOn ? 'false' : 'true');
    this.el.seek.disabled = !seekOn;
    this.el.golive.hidden = !(seekOn && this.offLive);

    var muted = this.audio.muted;
    this.el.mute.setAttribute('aria-label', muted ? 'Unmute' : 'Mute');
    this.el.mute.setAttribute('aria-pressed', muted ? 'true' : 'false');
    setHidden(this.icoVol, muted);
    setHidden(this.icoMuted, !muted);

    if ('mediaSession' in navigator) {
      try { navigator.mediaSession.playbackState = listening ? 'playing' : 'paused'; } catch (e) { /* ignore */ }
    }
    this.paint();
  };

  /* ---------- boot ---------- */

  function boot() {
    var nodes = document.querySelectorAll('.dr-radio');
    for (var i = 0; i < nodes.length; i++) {
      if (!nodes[i].__drPlayer) { nodes[i].__drPlayer = new Player(nodes[i]); }
    }
  }

  setInterval(function () {
    if (document.hidden) { return; }
    for (var i = 0; i < instances.length; i++) { instances[i].tick(); }
  }, 250);

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { return; }
    instances.forEach(function (p) {
      if (Date.now() - p.lastOk > 5000 || p.mode !== 'ok') { p.poll(); } else { p.scheduleNext(); }
    });
  });
  window.addEventListener('online', function () {
    instances.forEach(function (p) { if (p.mode !== 'ok') { p.failCount = 0; p.poll(); } });
  });

  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', boot); }
  else { boot(); }
})();
