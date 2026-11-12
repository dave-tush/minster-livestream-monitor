/* =====================================================================
   Backend Keep Alive — page logic
   ---------------------------------------------------------------------
   The page does one thing: every ten minutes it sends a plain GET to the
   monitoring service so the service never goes to sleep.

   Two details worth knowing:

   1. Requests are scheduled with a chained setTimeout, never setInterval,
      so a slow answer can never overlap the next request.

   2. A browser request that "fails" does not prove the server is down. When
      the reply is cross-origin and the server does not send CORS headers, the
      browser hides it and the promise rejects — even though the server
      answered with a perfectly good response. So when a request fails we
      probe once more without CORS: if the server replies to that probe it is
      alive, and only the status code is hidden.
   ===================================================================== */
(function () {
  'use strict';

  /* ---- settings ---- */
  var PING_URL = 'https://minster-livestream-monitor-zcvp.onrender.com/';
  var PING_INTERVAL_MS = 600 * 1000;   /* exactly 10 minutes */
  var REQUEST_TIMEOUT_MS = 45000;       /* a cold Render start can take ~35 s */
  var PROBE_TIMEOUT_MS = 20000;

  /* ---- state ---- */
  var el = {};
  var pingTimer = null;
  var clockTimer = null;
  var inFlight = false;
  var hasResult = false;
  var lastPingAt = 0;      /* when the current/last attempt started */
  var lastSuccessAt = 0;   /* when the server last answered us       */
  var nextPingAt = 0;

  /* ==================================================================
     Helpers
     ================================================================== */
  function $(id) { return document.getElementById(id); }

  function pad(n) { return (n < 10 ? '0' : '') + n; }

  function clockTime(ts) {
    var d = new Date(ts);
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
  }

  function relTime(ts) {
    if (!ts) return '';
    var s = Math.max(0, Math.round((Date.now() - ts) / 1000));
    if (s < 10) return 'just now';
    if (s < 60) return s + ' seconds ago';
    var m = Math.floor(s / 60);
    if (m < 60) return m + (m === 1 ? ' minute ago' : ' minutes ago');
    var h = Math.floor(m / 60);
    if (h < 24) return h + (h === 1 ? ' hour ago' : ' hours ago');
    var d = Math.floor(h / 24);
    return d + (d === 1 ? ' day ago' : ' days ago');
  }

  /* ==================================================================
     Display
     ================================================================== */
  function setStatus(state) {
    document.body.setAttribute('data-status', state);
    if (el.statusText) {
      el.statusText.textContent =
        state === 'online' ? 'Online' :
        state === 'offline' ? 'Unreachable' : 'Checking';
    }
  }

  function setResult(text, hint) {
    if (el.lastResult) el.lastResult.textContent = text;
    if (el.lastResultHint) el.lastResultHint.textContent = hint || '';
  }

  function renderLastPing() {
    if (!el.lastPing) return;
    if (!lastSuccessAt) {
      el.lastPing.textContent = 'Never';
      if (el.lastPingHint) el.lastPingHint.textContent = 'No successful request yet';
      return;
    }
    el.lastPing.textContent = clockTime(lastSuccessAt);
    if (el.lastPingHint) el.lastPingHint.textContent = relTime(lastSuccessAt);
  }

  function renderCountdown() {
    if (!el.countdown) return;
    var left = Math.max(0, Math.round((nextPingAt - Date.now()) / 1000));
    el.countdown.textContent = pad(Math.floor(left / 60)) + ':' + pad(left % 60);
  }

  /* ==================================================================
     The request
     ================================================================== */
  function ping() {
    if (inFlight) return;            /* never run two at once */
    inFlight = true;
    lastPingAt = Date.now();
    if (!hasResult) setStatus('checking');

    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = controller
      ? setTimeout(function () { try { controller.abort(); } catch (e) {} }, REQUEST_TIMEOUT_MS)
      : null;

    /* The button stays focusable while a request is running (aria-disabled
       instead of the disabled attribute) so keyboard users are never stranded. */
    if (el.pingBtn) el.pingBtn.setAttribute('aria-disabled', 'true');

    var options = {
      method: 'GET',
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'follow'
    };
    if (controller) options.signal = controller.signal;

    fetch(PING_URL, options).then(function (response) {
      /* We have the real HTTP response: the server is up and answered. */
      if (timer) clearTimeout(timer);
      var ms = Date.now() - lastPingAt;
      lastSuccessAt = Date.now();
      hasResult = true;
      setStatus('online');
      setResult('HTTP ' + response.status + (response.statusText ? ' ' + response.statusText : ''),
                'Answered in ' + ms + ' ms');
      return true;
    }, function (error) {
      if (timer) clearTimeout(timer);
      var timedOut = !!(error && error.name === 'AbortError');
      /* The browser did not hand us a response. That is not proof the server
         is down, so ask again without asking for CORS permission. */
      return probeReachable().then(function (reachable) {
        var ms = Date.now() - lastPingAt;
        hasResult = true;
        if (reachable) {
          lastSuccessAt = Date.now();
          setStatus('online');
          setResult('Server answered — status hidden by the browser',
                    timedOut
                      ? 'Replied after more than ' + Math.round(REQUEST_TIMEOUT_MS / 1000) + ' s'
                      : 'Replied in ' + ms + ' ms (the cross-origin reply cannot be read)');
        } else {
          setStatus('offline');
          setResult(timedOut
                      ? 'No reply within ' + Math.round(REQUEST_TIMEOUT_MS / 1000) + ' seconds'
                      : 'Could not reach the server',
                    'No HTTP response after ' + ms + ' ms');
        }
        return reachable;
      });
    }).then(function () {
      inFlight = false;
      if (el.pingBtn) el.pingBtn.removeAttribute('aria-disabled');
      renderLastPing();
      scheduleNext();
    }, function () {
      /* Should not happen, but the page must never stop checking. */
      inFlight = false;
      if (el.pingBtn) el.pingBtn.removeAttribute('aria-disabled');
      scheduleNext();
    });
  }

  /* A second, CORS-free request. It resolves whenever the server sends any
     response at all (even 404 or 500) and rejects only when nothing comes
     back — which is the case that actually means "unreachable". */
  function probeReachable() {
    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = controller
      ? setTimeout(function () { try { controller.abort(); } catch (e) {} }, PROBE_TIMEOUT_MS)
      : null;
    var options = {
      method: 'GET',
      mode: 'no-cors',
      cache: 'no-store',
      credentials: 'omit'
    };
    if (controller) options.signal = controller.signal;

    return fetch(PING_URL, options).then(function () {
      if (timer) clearTimeout(timer);
      return true;
    }, function () {
      if (timer) clearTimeout(timer);
      return false;
    });
  }

  /* ==================================================================
     Scheduling — a chained setTimeout, so requests never overlap
     ================================================================== */
  function scheduleNext() {
    if (pingTimer) clearTimeout(pingTimer);
    var elapsed = Date.now() - lastPingAt;
    var delay = Math.max(0, PING_INTERVAL_MS - elapsed);
    nextPingAt = Date.now() + delay;
    pingTimer = setTimeout(ping, delay);
    renderCountdown();
  }

  /* ==================================================================
     Boot
     ================================================================== */
  function init() {
    el.statusText = $('status-text');
    el.lastPing = $('last-ping');
    el.lastPingHint = $('last-ping-hint');
    el.countdown = $('countdown');
    el.lastResult = $('last-result');
    el.lastResultHint = $('last-result-hint');
    el.pingBtn = $('ping-btn');
    el.targetUrl = $('target-url');

    if (el.targetUrl) el.targetUrl.textContent = PING_URL;

    setStatus('checking');
    setResult('Waiting for the first request…', 'The page sends a request as soon as it opens');

    if (el.pingBtn) {
      el.pingBtn.addEventListener('click', function () {
        if (inFlight) return;
        if (pingTimer) clearTimeout(pingTimer);
        ping();
      });
    }

    /* The clock only redraws text; it never talks to the network. */
    clockTimer = setInterval(function () {
      renderCountdown();
      renderLastPing();
    }, 1000);

    /* Show the countdown straight away, rather than waiting for the first
       request to finish before the timer starts. */
    nextPingAt = Date.now() + PING_INTERVAL_MS;
    renderCountdown();

    /* If the machine slept or the tab was frozen, catch up straight away. */
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && nextPingAt && Date.now() >= nextPingAt - 1000) {
        if (pingTimer) clearTimeout(pingTimer);
        ping();
      }
    });

    ping();   /* first request goes out immediately */
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
