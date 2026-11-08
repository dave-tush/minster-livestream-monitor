# Dominion Radio

A continuous, 24/7 audio radio station WordPress plugin powered by the
[Minister Livestream Monitor](https://github.com/dave-tush/minster-livestream-monitor)
Python backend. Use the `[dominion_radio]` shortcode to embed the player.

The plugin is a **separate, independent WordPress plugin** — it does not modify
the existing Minister Live livestream plugin, the YouTube integration, or any
other site functionality.

---

## What visitors see

- A compact station identity with the Apostle's photograph, "Listen Live"
  heading and supporting message
- Current track title and performer
- Play / pause, mute, and playback progress with seeking
- Compact "Up next" track information (next track only)
- Offline and error states with retry

All listeners hear the same track at the same playback position, because the
Python backend maintains one shared playback timeline.

The full playlist presentation has been removed from the visible player. The
backend playlist — ordering, sequencing, looping and playback transitions —
remains fully functional and unchanged.

---

## Installation

1. Copy the `dominion-radio/` folder into your WordPress
   `wp-content/plugins/` directory.
2. Go to **Plugins > Installed Plugins** and activate **Dominion Radio**.
3. Go to **Settings > Dominion Radio** to configure the backend URL.

---

## Backend URL configuration

The plugin needs the public base URL of your Dominion Radio Python backend.
Enter it on **Settings > Dominion Radio** (no trailing slash), for example:

```
https://minister-livestream-monitor.onrender.com
```

The backend must be running with the Dominion Radio engine active
(`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`,
`TELEGRAM_CHANNEL` environment variables configured).

---

## CORS configuration (required)

The WordPress frontend fetches `/radio/now`, `/radio/playlist`, and
`/radio/audio/{id}` from the Python backend across origins. The backend must
allow cross-origin requests from your WordPress site.

Add the following CORS middleware to your FastAPI backend (`app/main.py`):

```python
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(...)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://toluwalogoagboola.org"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=[
        "Accept", "Accept-Language", "Cache-Control",
        "Content-Language", "Content-Type", "Range",
    ],
    allow_credentials=False,
    max_age=86400,
    expose_headers=[
        "Accept-Ranges", "Content-Range", "Content-Length", "Content-Type",
    ],
)
```

Do **not** use a wildcard origin (`*`) in production.

---

## Creating the Radio page

1. In WordPress, go to **Pages > Add New**.
2. Title the page "Radio".
3. Insert a **Shortcode** block (or use the Shortcode widget in Elementor).
4. Paste the shortcode:

   ```
   [dominion_radio]
   ```

5. Publish the page.

Optional shortcode attributes:

| Attribute | Default | Description |
|---|---|---|
| `name` | "Dominion Radio" | Station name shown in the header |
| `label` | "Dominion Radio" | Eyebrow label above the name |
| `tagline` | "Worship, the Word and prayer, wherever you are." | Supporting phrase under the name |
| `autoplay` | "no" | "yes" to start muted playback (browser restrictions apply) |
| `offline` | (station-specific message) | Custom offline message |

Example:

```
[dominion_radio name="Dominion Radio" label="24/7" tagline="A Sound of Worship" autoplay="no"]
```

---

## How playback synchronisation works

1. The plugin fetches `GET {backend}/radio/now` every 15 seconds.
2. The backend returns the current track, playback position (wall-clock based),
   and server time.
3. The frontend sets the HTML5 `<audio>` source to
   `{backend}/radio/audio/{message_id}` and seeks to the shared position.
4. When the backend advances to the next track, the next poll detects the
   track ID change and loads the new audio source.
5. A local pause or seek does **not** affect other listeners or the backend
   timeline.

---

## Troubleshooting

| Issue | Likely cause | Fix |
|---|---|---|
| "Backend URL not configured" | Settings > Dominion Radio URL is empty | Enter the backend public base URL |
| "Unable to reach the radio backend" | CORS missing or backend down | Add CORS middleware; check backend is running |
| Audio won't play | Browser autoplay restriction | Click the play button (user gesture required) |
| "No track available" | Backend has no tracks yet | Wait for Telegram sync; check backend logs |
| Position drift | Network delay | Auto-corrects within 5 seconds on next poll |

---

## Files

```
dominion-radio/
├── dominion-radio.php          # Main plugin file
├── includes/
│   ├── class-settings.php      # Settings registration & sanitization
│   └── class-shortcode.php     # Shortcode rendering & asset enqueue
├── assets/
│   ├── css/
│   │   └── dominion-radio.css  # Player styling
│   └── js/
│       └── dominion-radio.js   # Frontend player logic
├── templates/
│   └── radio-player.php        # Player HTML template
└── README.md                   # This file
```

---

## License

GPL-2.0-or-later

---

## What is NOT touched

This plugin does **not** modify:

- `wordpress-plugin/minister-live/` (existing YouTube livestream plugin)
- Any YouTube API integration or livestream discovery
- `minister-live/v1`, `lv/v1`, or the `/live-stream/` page
- Sermon functionality
- The Python backend radio engine (only CORS middleware is added)

---
