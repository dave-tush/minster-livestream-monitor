# PREVIEW-ONLY generator: writes concept-a/b/c.html. Not part of the plugin.
REAL = "https://toluwalogoagboola.org/wp-content/uploads/2026/10/Apostle-Toluwalogo-Agboola-Poster-1.png"
STAND = "assets/img/portrait-standin.png"
BACKEND = "http://127.0.0.1:8765"

PLAY = '<svg class="dr-icon dr-icon--play" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8.2 4.9v14.2a.6.6 0 0 0 .92.5l11-7.1a.6.6 0 0 0 0-1l-11-7.1a.6.6 0 0 0-.92.5z"/></svg>'
PAUSE = '<svg class="dr-icon dr-icon--pause" viewBox="0 0 24 24" aria-hidden="true" hidden><rect fill="currentColor" x="6.2" y="5" width="3.8" height="14" rx="1"/><rect fill="currentColor" x="14" y="5" width="3.8" height="14" rx="1"/></svg>'
VOL = '<svg class="dr-icon dr-icon--vol" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M4 9.5v5h3.4L12 18.5v-13L7.4 9.5z"/><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M15.6 9.2a4 4 0 0 1 0 5.6M18.2 6.6a7.6 7.6 0 0 1 0 10.8"/></svg>'
MUTED = '<svg class="dr-icon dr-icon--muted" viewBox="0 0 24 24" aria-hidden="true" hidden><path fill="currentColor" d="M4 9.5v5h3.4L12 18.5v-13L7.4 9.5z"/><path fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" d="M16 9.6l4.8 4.8M20.8 9.6L16 14.4"/></svg>'
MENU = '<svg viewBox="0 0 24 24"><path d="M4 8h16M4 16h16" stroke-linecap="round"/></svg>'

NAV = ["Home","About","Itinerary","Live Stream","Sermons","Letters","Testimonies","Radio"]
def header():
    items = "".join(f'<li><a href="#"{" aria-current=\"page\"" if n=="Radio" else ""}>{n}</a></li>' for n in NAV)
    return f'<header class="site-header"><a class="site-logo" href="#">ATA</a><ul class="site-nav">{items}</ul><button class="site-menu" aria-label="Menu">{MENU}</button></header>'
FOOT = '<footer class="site-footer"><span>ATA</span><span>All rights reserved</span></footer>'

def img(cls="dr-identity__image"):
    return f'<img src="{REAL}" onerror="this.onerror=null;this.src=\'{STAND}\'" alt="Apostle Toluwalogo Agboola" class="{cls}" width="800" height="1200" fetchpriority="high">'

def status():
    return '<div class="dr-identity__status"><span class="dr-identity__status-dot" aria-hidden="true"></span><span class="dr-identity__status-text">Loading&hellip;</span></div>'

def controls(extra_before="", extra_after=""):
    return f'''<div class="dr-player__controls">
  <button type="button" class="dr-btn dr-btn--play" aria-label="Play" aria-pressed="false">{PLAY}{PAUSE}</button>
  <div class="dr-player__progress" role="presentation"><div class="dr-player__progress-fill"></div><div class="dr-player__progress-handle"></div></div>
  <span class="dr-player__time">0:00</span>
  <button type="button" class="dr-btn dr-btn--mute" aria-label="Mute" aria-pressed="false">{VOL}{MUTED}</button>
</div>'''

NEXT = '''<div class="dr-player__next" hidden>
  <span class="dr-player__next-label">Up next</span>
  <span class="dr-player__next-title"></span>
  <span class="dr-player__next-performer"></span>
</div>'''
OFFLINE = '''<div class="dr-offline" hidden>
  <p class="dr-offline__text">Dominion Radio is not on air right now.</p>
  <p class="dr-offline__sub"></p>
  <button type="button" class="dr-btn dr-btn--main">Try again</button>
</div>
<audio class="dr-audio" preload="none" crossorigin="anonymous"></audio>'''
INFO = '''<div class="dr-player__info">
  <span class="dr-player__label">Now playing</span>
  <h2 class="dr-player__track-title">Select a track</h2>
  <p class="dr-player__performer"></p>
</div>'''

def page(cls, title, css, inner, note):
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="assets/css/base.css">
<link rel="stylesheet" href="assets/css/{css}">
</head>
<body class="{cls}">
{header()}
<main>
<div class="dr-radio" data-backend-url="{BACKEND}" data-autoplay="0" data-name="Dominion Radio" data-label="Dominion Radio" data-tagline="Worship, the Word and prayer, wherever you are." data-offline-msg="Dominion Radio is not on air right now.">
{inner}
</div>
</main>
{FOOT}
<div class="mock-note">{note}</div>
<script src="assets/js/dominion-radio.preview.js"></script>
</body></html>'''

TAG = 'Worship, the Word and prayer, wherever you are.'

# ---------------- Concept A: Editorial ----------------
A = f'''<div class="dr-layout">
  <div class="dr-masthead"><span class="dr-identity__eyebrow">Dominion Radio</span>{status()}</div>
  <div class="dr-identity" aria-label="Dominion Radio">
    <h1 class="dr-identity__title">Listen <em>Live</em></h1>
    <p class="dr-identity__tagline">{TAG}</p>
  </div>
  <figure class="dr-figure">{img()}<figcaption>Apostle Toluwalogo Agboola</figcaption></figure>
  <section class="dr-player" aria-label="Radio player">
    <div class="dr-player__card">
      {INFO}
      {controls()}
      {NEXT}
    </div>
    {OFFLINE}
  </section>
</div>'''

# ---------------- Concept B: Immersive ----------------
B = f'''<div class="dr-stage">
  <figure class="dr-figure">{img()}</figure>
  <div class="dr-identity" aria-label="Dominion Radio">
    <div class="dr-identity__meta">{status()}<span class="dr-identity__rule" aria-hidden="true"></span><span class="dr-identity__eyebrow">Dominion Radio</span></div>
    <h1 class="dr-identity__title">Listen <em>Live</em></h1>
    <p class="dr-identity__tagline">{TAG}</p>
  </div>
  <section class="dr-player" aria-label="Radio player">
    <div class="dr-player__card">
      {INFO}
      {controls()}
      {NEXT}
    </div>
    {OFFLINE}
  </section>
</div>'''

# ---------------- Concept C: Minimal luxury ----------------
C = f'''<div class="dr-stage">
  <div class="dr-masthead"><span class="dr-identity__eyebrow">Dominion Radio</span>{status()}</div>
  <figure class="dr-figure">{img()}</figure>
  <div class="dr-identity" aria-label="Dominion Radio">
    <h1 class="dr-identity__title">Listen <em>Live</em></h1>
    <p class="dr-identity__tagline">{TAG}</p>
  </div>
  <section class="dr-player" aria-label="Radio player">
    <div class="dr-player__card">
      {INFO}
      {controls()}
      {NEXT}
    </div>
    {OFFLINE}
  </section>
</div>'''

n = "Design preview · mock site chrome · portrait is a stand-in offline"
open("concept-a.html","w").write(page("c-a","Dominion Radio — Concept A · Editorial","concept-a.css",A,n))
open("concept-b.html","w").write(page("c-b","Dominion Radio — Concept B · Immersive","concept-b.css",B,n))
open("concept-c.html","w").write(page("c-c","Dominion Radio — Concept C · Minimal Luxury","concept-c.css",C,n))
print("built")
