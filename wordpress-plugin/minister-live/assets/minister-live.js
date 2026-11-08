/* Minister Live: keeps the [minister_live] block up to date without a page refresh.
 * Polls the site's own REST endpoint (never YouTube's API for status) and shows/hides the player.
 * The video is played through YouTube's IFrame Player API with YouTube's own controls switched off;
 * play/pause, mute and fullscreen are our own buttons. Optional "go-live" alerts use the browser's
 * Notification API and work while the page is open (including in a background tab). */
(function () {
  'use strict';

  var ID_RE = /^[A-Za-z0-9_-]{11}$/;
  var STORE_KEY = 'ml_notify';
  var STATES = ['is-loading', 'is-paused', 'is-playing', 'is-buffering'];

  /* ---------- small helpers ---------- */
  function store(key, val) {
    try {
      if (val === undefined) { return window.localStorage.getItem(key); }
      window.localStorage.setItem(key, val);
    } catch (e) { /* private mode etc. */ }
    return null;
  }

  /* ---------- YouTube IFrame API loader (shared by every widget on the page) ---------- */
  var ytQueue = [], ytLoading = false;
  function withYT(cb) {
    if (window.YT && window.YT.Player) { cb(); return; }
    ytQueue.push(cb);
    if (ytLoading) { return; }
    ytLoading = true;
    var prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = function () {
      if (typeof prev === 'function') { try { prev(); } catch (e) {} }
      var q = ytQueue.slice(); ytQueue.length = 0;
      q.forEach(function (fn) { try { fn(); } catch (e) {} });
    };
    var s = document.createElement('script');
    s.src = 'https://www.youtube.com/iframe_api';
    s.async = true;
    document.head.appendChild(s);
  }

  function Widget(root) {
    var self = this;
    this.root = root;
    this.endpoint = root.getAttribute('data-endpoint');
    this.interval = Math.max(15, Math.min(300, parseInt(root.getAttribute('data-interval'), 10) || 30)) * 1000;
    this.autoplay = root.getAttribute('data-autoplay') === '1';
    this.name = root.getAttribute('data-name') || 'The minister';

    var q = function (sel) { return root.querySelector(sel); };
    this.status = q('.ml-live__status');
    this.statusText = q('.ml-live__status-text');
    this.offline = q('.ml-live__offline');
    this.stage = q('.ml-live__player');
    this.frame = q('.ml-live__frame');
    this.ytWrap = q('.ml-live__yt');
    this.poster = q('.ml-live__poster');
    this.shield = q('.ml-live__shield');
    this.titleEl = q('.ml-live__title');
    this.playBtn = q('.ml-live__btn--main');
    this.iconPlay = q('.ml-live__i-play');
    this.iconPause = q('.ml-live__i-pause');
    this.muteBtn = q('.ml-live__mute');
    this.iconVol = q('.ml-live__i-vol');
    this.iconMuted = q('.ml-live__i-muted');
    this.fsBtn = q('.ml-live__fs');
    this.switchEl = q('.ml-live__switch');
    this.notifySub = q('.ml-live__notify-sub');
    this.notifyDefault = this.notifySub ? this.notifySub.textContent : '';

    this.player = null;
    this.playerId = '';
    this.ready = false;
    this.live = !!root.getAttribute('data-video-id');
    this.lastPointer = 'mouse';
    this.wasIdle = false;
    this.idleT = null;
    this.timer = null;

    this.bindPlayerUI();
    this.initNotify();

    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { self.restoreTitle(); self.recheckPermission(); self.tick(); }   // catch up as soon as the tab is visible again
    });
    window.addEventListener('focus', function () { self.recheckPermission(); });
    try {
      if (navigator.permissions && navigator.permissions.query) {
        navigator.permissions.query({ name: 'notifications' }).then(function (ps) {
          ps.onchange = function () { self.recheckPermission(); };
        }).catch(function () {});
      }
    } catch (e) {}
    document.addEventListener('fullscreenchange', function () { self.syncFs(); });
    document.addEventListener('webkitfullscreenchange', function () { self.syncFs(); });

    var initial = root.getAttribute('data-video-id') || '';
    if (initial) { this.buildPlayer(initial); }
    this.schedule();
  }

  /* ---------- polling ---------- */
  Widget.prototype.schedule = function () {
    var self = this;
    clearTimeout(this.timer);
    this.timer = setTimeout(function () { self.tick(); }, this.interval);
  };

  Widget.prototype.tick = function () {
    var self = this;
    clearTimeout(this.timer);
    // Background tabs are only polled when go-live alerts are on.
    if (document.hidden && !this.notifyOn()) { this.schedule(); return; }
    var url;
    try {
      url = new URL(this.endpoint, window.location.href);
      url.searchParams.set('_', Date.now());            // defeats CDN/page caches
    } catch (e) { this.schedule(); return; }

    fetch(url.toString(), { cache: 'no-store', credentials: 'omit', headers: { Accept: 'application/json' } })
      .then(function (r) { if (!r.ok) { throw new Error('HTTP ' + r.status); } return r.json(); })
      .then(function (state) { self.render(state); })
      .catch(function () { /* network hiccup: keep showing what we have */ })
      .then(function () { self.schedule(); });
  };

  Widget.prototype.render = function (state) {
    var live = !!(state && state.status === 'live' && ID_RE.test(state.video_id || ''));
    var wasLive = this.live;
    if (live) {
      this.showLive(state);
      if (!wasLive) { this.alertLive(state); }
    } else {
      this.showOffline();
    }
    this.live = live;
  };

  Widget.prototype.setStatus = function (live) {
    this.status.classList.toggle('is-live', live);
    this.statusText.textContent = live ? 'LIVE NOW' : 'NOT LIVE';
  };

  Widget.prototype.showLive = function (state) {
    if (this.playerId !== state.video_id) { this.buildPlayer(state.video_id); }
    this.titleEl.textContent = state.title || '';          // textContent: never HTML
    this.titleEl.hidden = !state.title;
    this.setStatus(true);
    this.offline.hidden = true;
    this.stage.hidden = false;
  };

  Widget.prototype.showOffline = function () {
    this.destroyPlayer();                                  // removing the player stops playback
    this.setStatus(false);
    this.stage.hidden = true;
    this.offline.hidden = false;
  };

  /* ---------- player ---------- */
  Widget.prototype.setState = function (name) {
    var f = this.frame, i;
    for (i = 0; i < STATES.length; i++) { f.classList.remove(STATES[i]); }
    if (name) { f.classList.add('is-' + name); }
    if (name === 'playing') { f.classList.add('is-played'); this.poke(); }
    else { clearTimeout(this.idleT); f.classList.remove('is-idle'); }
    var running = (name === 'playing' || name === 'buffering');
    this.iconPlay.hidden = running;
    this.iconPause.hidden = !running;
    this.playBtn.setAttribute('aria-label', running ? 'Pause' : 'Play');
  };

  Widget.prototype.buildPlayer = function (id) {
    var self = this;
    this.destroyPlayer();
    this.playerId = id;
    this.frame.classList.remove('is-played');
    var err = this.frame.querySelector('.ml-live__error');
    if (err) { err.parentNode.removeChild(err); }
    this.poster.style.backgroundImage = "url('https://i.ytimg.com/vi/" + id + "/hqdefault.jpg')";
    this.setState('loading');

    var host = document.createElement('div');
    this.ytWrap.appendChild(host);
    withYT(function () {
      if (self.playerId !== id) { return; }                // switched or went offline meanwhile
      self.player = new window.YT.Player(host, {
        videoId: id,
        playerVars: {
          controls: 0, disablekb: 1, fs: 0, modestbranding: 1, rel: 0, playsinline: 1,
          iv_load_policy: 3, cc_load_policy: 0,
          autoplay: self.autoplay ? 1 : 0, mute: self.autoplay ? 1 : 0,
          origin: window.location.origin
        },
        events: {
          onReady: function () {
            if (self.playerId !== id) { return; }
            self.ready = true;
            self.setState('paused');
            self.syncMute();
            if (self.autoplay) { try { self.player.mute(); self.player.playVideo(); } catch (e) {} }
          },
          onStateChange: function (e) {
            if (self.playerId !== id) { return; }
            if (e.data === 1) { self.setState('playing'); }
            else if (e.data === 3) { self.setState('buffering'); }
            else if (e.data === 2 || e.data === 0 || e.data === 5) { self.setState('paused'); }
            self.syncMute();
          },
          onError: function () {
            if (self.playerId !== id) { return; }
            self.setState('paused');
            var m = document.createElement('div');
            m.className = 'ml-live__error';
            m.textContent = 'This broadcast can’t be played right now. Please refresh the page in a moment.';
            self.frame.appendChild(m);
          }
        }
      });
    });
  };

  Widget.prototype.destroyPlayer = function () {
    if (this.player) { try { this.player.destroy(); } catch (e) {} }
    this.player = null;
    this.ready = false;
    this.playerId = '';
    this.ytWrap.textContent = '';
    if (document.fullscreenElement || document.webkitFullscreenElement) {
      try { (document.exitFullscreen || document.webkitExitFullscreen).call(document); } catch (e) {}
    }
  };

  Widget.prototype.toggle = function () {
    if (!this.player || !this.ready) { return; }
    var st;
    try { st = this.player.getPlayerState(); } catch (e) { return; }
    try {
      if (st === 1 || st === 3) { this.player.pauseVideo(); } else { this.player.playVideo(); }
    } catch (e) {}
  };

  Widget.prototype.syncMute = function () {
    var muted = false;
    try { muted = !!(this.player && this.player.isMuted && this.player.isMuted()); } catch (e) {}
    this.iconVol.hidden = muted;
    this.iconMuted.hidden = !muted;
    this.muteBtn.setAttribute('aria-label', muted ? 'Unmute' : 'Mute');
  };

  Widget.prototype.toggleMute = function () {
    if (!this.player || !this.ready) { return; }
    try { if (this.player.isMuted()) { this.player.unMute(); } else { this.player.mute(); } } catch (e) {}
    this.syncMute();
  };

  Widget.prototype.toggleFs = function () {
    var d = document, el = this.frame;
    try {
      if (d.fullscreenElement || d.webkitFullscreenElement) { (d.exitFullscreen || d.webkitExitFullscreen).call(d); }
      else { (el.requestFullscreen || el.webkitRequestFullscreen).call(el); }
    } catch (e) {}
  };

  Widget.prototype.syncFs = function () {
    var on = (document.fullscreenElement || document.webkitFullscreenElement) === this.frame;
    this.fsBtn.setAttribute('aria-label', on ? 'Exit full screen' : 'Full screen');
  };

  /* Hide the controls after a short idle while playing */
  Widget.prototype.poke = function () {
    var self = this, f = this.frame;
    f.classList.remove('is-idle');
    clearTimeout(this.idleT);
    if (f.classList.contains('is-playing')) {
      this.idleT = setTimeout(function () { f.classList.add('is-idle'); }, 2600);
    }
  };

  Widget.prototype.bindPlayerUI = function () {
    var self = this;
    this.frame.addEventListener('pointerdown', function (e) {
      self.lastPointer = e.pointerType || 'mouse';
      self.wasIdle = self.frame.classList.contains('is-idle');
      self.poke();
    });
    this.frame.addEventListener('pointermove', function () { self.poke(); });
    this.shield.addEventListener('click', function () {
      // On touch screens, the first tap while the controls are hidden only brings them back.
      if (self.lastPointer === 'touch' && self.wasIdle) { self.wasIdle = false; return; }
      self.toggle();
    });
    this.playBtn.addEventListener('click', function () { self.toggle(); });
    this.muteBtn.addEventListener('click', function () { self.toggleMute(); });
    if (document.fullscreenEnabled || document.webkitFullscreenEnabled) {
      this.fsBtn.addEventListener('click', function () { self.toggleFs(); });
    } else {
      this.fsBtn.hidden = true;                            // e.g. iPhone Safari has no element fullscreen
    }
  };

  /* ---------- go-live alerts ---------- */
  Widget.prototype.notifyOn = function () {
    return 'Notification' in window && Notification.permission === 'granted' && store(STORE_KEY) === '1';
  };

  Widget.prototype.setNotifyText = function (text) {
    if (this.notifySub) { this.notifySub.textContent = text; }
  };

  Widget.prototype.syncSwitch = function () {
    var on = this.notifyOn();
    this.switchEl.setAttribute('aria-checked', on ? 'true' : 'false');
    this.setNotifyText(on ? 'On. You’ll be alerted while this page stays open.' : this.notifyDefault);
  };

  Widget.prototype.initNotify = function () {
    var self = this;
    if (!this.switchEl) { return; }
    this.switchEl.addEventListener('click', function () { self.onSwitch(); });
    if (!('Notification' in window)) {
      if (platform().ios) {                                // iPhone Safari tab: needs the Home Screen app
        this.setNotifyText('Tap the switch to see how to turn on alerts on your iPhone.');
      } else {
        this.switchEl.disabled = true;
        this.setNotifyText('Alerts aren’t supported in this browser.');
      }
      return;
    }
    if (Notification.permission === 'denied') {
      store(STORE_KEY, '0');
      this.switchEl.setAttribute('aria-checked', 'false');
      this.setNotifyText('Notifications are blocked. Tap the switch for a quick fix.');
    } else {
      this.syncSwitch();
    }
  };

  Widget.prototype.onSwitch = function () {
    var self = this;
    if (this.notifyOn()) { store(STORE_KEY, '0'); this.syncSwitch(); return; }
    if (!('Notification' in window) || Notification.permission === 'denied') { this.openHelp(); return; }
    var called = false;
    function done(p) {
      if (called) { return; }
      called = true;
      if (p === 'granted') {
        store(STORE_KEY, '1');
        self.syncSwitch();
        self.popup(self.name, 'Alerts are on. We’ll let you know when the broadcast begins.');
      } else {
        store(STORE_KEY, '0');
        self.syncSwitch();
        if (p === 'denied') { self.setNotifyText('Notifications are blocked. Tap the switch for a quick fix.'); self.openHelp(); }
        else { self.setNotifyText('Alerts were not turned on. Tap the switch to try again.'); }
      }
    }
    try {
      var r = Notification.requestPermission(done);
      if (r && typeof r.then === 'function') { r.then(done); }
    } catch (e) { done('default'); }
  };

  /* ---------- "fix it" sheet for blocked notifications ---------- */
  function platform() {
    var ua = navigator.userAgent || '';
    var ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    var android = /Android/.test(ua);
    var standalone = !!(window.navigator.standalone || (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches));
    return { ios: ios, android: android, mobile: ios || android, standalone: standalone, firefox: /Firefox|FxiOS/.test(ua), samsung: /SamsungBrowser/.test(ua) };
  }

  var HELP = {
    android: {
      label: 'Android',
      steps: [
        'Look at the **top of your screen**, where the website address is. Tap the small **lock 🔒 icon** just to the left of it.',
        'Tap **Permissions**. (Some phones say **Site settings**.)',
        'Tap **Notifications**, then choose **Allow**.',
        'Come back to this page. The alerts will switch on by themselves.'
      ],
      tip: 'Can’t see a lock? Tap the **three dots ⋮** at the top right, then **Settings**, **Site settings**, **Notifications**, and allow this website.'
    },
    iphone_home: {
      label: 'iPhone',
      steps: [
        'Tap the **Share button** at the bottom of Safari. It looks like a square with an arrow pointing up.',
        'Scroll down and tap **Add to Home Screen**, then tap **Add**.',
        'Close Safari. Open the **new icon on your Home Screen**.',
        'Tap the **Go-live alerts** switch, then tap **Allow**.'
      ],
      tip: 'On iPhone, alerts only work from the Home Screen icon. This is Apple’s rule, not ours.'
    },
    iphone_settings: {
      label: 'iPhone',
      steps: [
        'Open the **Settings** app on your iPhone.',
        'Tap **Notifications**.',
        'Scroll down and tap the **name of this website**.',
        'Turn on **Allow Notifications**, then come back here.'
      ],
      tip: 'Can’t find it? Scroll right to the bottom of the list.'
    },
    computer: {
      label: 'Computer',
      steps: [
        'Look at the **top of your browser**, where the website address is. Click the small **lock 🔒 icon** just to the left of it.',
        'Find **Notifications** and change it to **Allow**.',
        'Come back to this page. The alerts will switch on by themselves.'
      ],
      tip: 'If nothing changes, refresh the page once.'
    }
  };

  function helpKey(which) {
    if (which === 'iphone') { return (platform().ios && ('Notification' in window)) ? 'iphone_settings' : 'iphone_home'; }
    return which;
  }

  function firstTab() {
    var p = platform();
    if (p.ios) { return 'iphone'; }
    if (p.android) { return 'android'; }
    return 'computer';
  }

  /* Turns "tap **Allow**" into text with bold words (built with DOM nodes, never innerHTML) */
  function richText(el, str) {
    el.textContent = '';
    str.split('**').forEach(function (part, i) {
      if (!part) { return; }
      if (i % 2) { var b = document.createElement('strong'); b.textContent = part; el.appendChild(b); }
      else { el.appendChild(document.createTextNode(part)); }
    });
  }

  Widget.prototype.closeHelp = function () {
    if (this.sheet && this.sheet.parentNode) { this.sheet.parentNode.removeChild(this.sheet); }
    this.sheet = null;
    document.removeEventListener('keydown', this.escHandler || function () {});
  };

  Widget.prototype.openHelp = function () {
    var self = this;
    if (this.sheet) { return; }
    var wrap = document.createElement('div');
    wrap.className = 'ml-sheet';
    wrap.setAttribute('role', 'dialog');
    wrap.setAttribute('aria-modal', 'true');
    wrap.setAttribute('aria-label', 'How to allow notifications');

    var card = document.createElement('div');
    card.className = 'ml-sheet__card';
    var t = document.createElement('div');
    t.className = 'ml-sheet__title';
    t.textContent = 'Let’s turn on your alerts';
    var intro = document.createElement('p');
    intro.className = 'ml-sheet__intro';
    intro.textContent = 'Your device is currently stopping this website from sending alerts. Choose your device and follow the steps.';

    var tabs = document.createElement('div');
    tabs.className = 'ml-sheet__tabs';
    tabs.setAttribute('role', 'tablist');
    var ol = document.createElement('ol');
    ol.className = 'ml-sheet__steps';
    var tip = document.createElement('p');
    tip.className = 'ml-sheet__tip';

    var again = document.createElement('button');
    again.type = 'button';
    again.className = 'ml-sheet__btn ml-sheet__btn--main';
    again.textContent = 'I’ve done it — check again';
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'ml-sheet__btn';
    close.textContent = 'Not now';
    var msg = document.createElement('p');
    msg.className = 'ml-sheet__msg';
    msg.setAttribute('role', 'status');

    var tabBtns = {};
    function show(which) {
      var h = HELP[helpKey(which)];
      Object.keys(tabBtns).forEach(function (k) { tabBtns[k].setAttribute('aria-selected', k === which ? 'true' : 'false'); });
      ol.textContent = '';
      h.steps.forEach(function (txt) { var li = document.createElement('li'); richText(li, txt); ol.appendChild(li); });
      richText(tip, h.tip);
      msg.textContent = '';
    }
    ['iphone', 'android', 'computer'].forEach(function (k) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'ml-sheet__tab';
      b.setAttribute('role', 'tab');
      b.textContent = HELP[k === 'iphone' ? 'iphone_home' : k].label;
      b.addEventListener('click', function () { show(k); });
      tabBtns[k] = b;
      tabs.appendChild(b);
    });

    card.appendChild(t); card.appendChild(intro); card.appendChild(tabs); card.appendChild(ol);
    card.appendChild(tip); card.appendChild(msg); card.appendChild(again); card.appendChild(close);
    wrap.appendChild(card);
    document.body.appendChild(wrap);
    this.sheet = wrap;
    show(firstTab());
    try { again.focus(); } catch (e) {}

    again.addEventListener('click', function () {
      if (!self.recheckPermission(true)) { msg.textContent = 'Not on yet. Finish the steps above, then tap this button again.'; }
    });
    close.addEventListener('click', function () { self.closeHelp(); });
    wrap.addEventListener('click', function (e) { if (e.target === wrap) { self.closeHelp(); } });
    this.escHandler = function (e) { if (e.key === 'Escape') { self.closeHelp(); } };
    document.addEventListener('keydown', this.escHandler);
  };

  /* Called when the visitor returns from settings: turns alerts on by itself once allowed. */
  Widget.prototype.recheckPermission = function (fromButton) {
    if (!('Notification' in window)) { return false; }
    if (Notification.permission === 'granted' && (this.sheet || fromButton)) {
      store(STORE_KEY, '1');
      this.syncSwitch();
      this.closeHelp();
      this.popup(this.name, 'Alerts are on. We’ll let you know when the broadcast begins.');
      return true;
    }
    if (Notification.permission === 'default' && this.sheet) { this.closeHelp(); }
    return Notification.permission === 'granted';
  };

  Widget.prototype.popup = function (title, body) {
    try {
      var n = new Notification(title, { body: body, tag: 'ml-live' });
      n.onclick = function () { try { window.focus(); n.close(); } catch (e) {} };
    } catch (e) { /* some mobile browsers only allow service-worker notifications */ }
  };

  Widget.prototype.alertLive = function (state) {
    if (!this.notifyOn()) { return; }
    this.popup(this.name + ' is live now', state.title || 'Tap to join the broadcast.');
    if (document.hidden && !this.origTitle) {
      this.origTitle = document.title;
      document.title = '● LIVE · ' + this.origTitle;
    }
  };

  Widget.prototype.restoreTitle = function () {
    if (this.origTitle) { document.title = this.origTitle; this.origTitle = ''; }
  };

  function boot() {
    var nodes = document.querySelectorAll('.ml-live');
    for (var i = 0; i < nodes.length; i++) { new Widget(nodes[i]); }
  }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', boot); } else { boot(); }
})();
