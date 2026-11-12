# Backend Keep Alive

A small, self-contained web page whose only job is to keep the **Minister Livestream
Monitor** backend awake.

Every ten minutes it sends a plain `GET` request to

```
https://minster-livestream-monitor-zcvp.onrender.com/
```

Render puts a free web service to sleep after 15 minutes without inbound traffic. This
page produces that traffic, so the monitor keeps running and stays warm.

---

## What is in this folder

```
keep-alive-page/
├── index.html              the page
├── README.md               this file
└── assets/
    ├── css/keep-alive.css  black & gold theme, responsive, accessible
    ├── js/keep-alive.js    the ping loop and the readouts
    └── fonts/              Playfair Display + Inter (self-hosted, no Google Fonts)
```

Nothing here talks to the backend except one `GET` request. There is no API key, no
session, no cookie, and nothing is written to disk.

---

## What the page shows

| Readout | Meaning |
|---|---|
| **Online / Unreachable** | Whether the server answered. "Online" means an HTTP response came back (any status code). "Unreachable" means nothing came back at all. |
| **Last ping** | The clock time of the last reply that actually arrived, and how long ago that was. |
| **Next ping** | A countdown to the next request. Always 10:00 straight after a ping. |
| **Last result** | The detail of the most recent request — the HTTP status and how long it took, or the reason it failed. |

There is also a **Ping now** button that sends a request immediately, and a small note
showing exactly which address is being called.

---

## How the timing works

- The first request goes out **the moment the page opens**.
- The next one is scheduled **600 seconds** after that one started.
- The schedule is a chained `setTimeout`, never `setInterval`, so a slow answer can
  never overlap the next request. There is also a hard guard that refuses to start a
  second request while one is in flight.
- If the machine was asleep or the tab was frozen, the page catches up as soon as it
  is looked at again.

---

## A note about cross-origin replies

The page is a browser client, so it is subject to the backend's CORS rules. The backend
currently allows only the WordPress origins, so a request from this page is normally
**answered but not readable** by the browser.

That is why the page does not simply treat a failed request as "the backend is down".
When the browser hides a reply, the page sends **one more request without asking for
cross-origin permission**. A no-cors request still resolves whenever the server sends
any response at all, so:

- the probe succeeds → the server **is** alive, and the page shows **Online** with the
  note *"Server answered — status hidden by the browser"*;
- the probe fails too → nothing answered, and the page shows **Unreachable**.

When the backend does allow the page's origin, the real HTTP status code is shown
instead (for example *HTTP 200 OK*). Either way the decision is based on the actual
HTTP response, never on a guess.

The consequence: on a cross-origin page **each ping sends two requests** — the first to
read the status code, and the second to prove the server answered. If the backend is
later configured to allow this page's origin, the first request succeeds on its own and
the second is never needed.

---

## Hosting it

Upload the whole folder to any static host — Cloudflare Pages, Netlify, Vercel, GitHub
Pages, or a plain directory on a web server. There is no build step and no server-side
runtime.

Two things to keep in mind:

1. **Serve it over HTTPS.** A page loaded over plain `http://` will still work, but
   browsers warn about it, and some hosts refuse to serve mixed content.
2. **Keep the tab open.** The requests are made by JavaScript in the browser, so they
   only happen while the page is open. Opening the page on a screen that is always on
   (a second monitor, a small always-on machine, a hosted browser tab) is the usual
   arrangement. If you would rather have something that runs with no browser at all,
   use `keep_alive.py` in the project root instead — it does the same job from Python.

---

## Changing the address or the interval

Both live at the top of `assets/js/keep-alive.js`:

```js
var PING_URL = 'https://minster-livestream-monitor-zcvp.onrender.com/';
var PING_INTERVAL_MS = 600 * 1000;   /* exactly 10 minutes */
```

Changing `PING_URL` also updates the address shown on the page, because the page reads
it from the same place.

---

## Accessibility

- A visible gold focus ring on every interactive control.
- The connection state is announced through an `aria-live` region.
- Layout works from 320 px upwards; the readouts stack on small screens.
- `prefers-reduced-motion` and `prefers-contrast` are both respected.
