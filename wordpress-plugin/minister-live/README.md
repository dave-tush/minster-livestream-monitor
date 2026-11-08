# Minister Live

Shows the minister's current YouTube livestream with the `[minister_live]` shortcode, inside a custom black-and-gold player. The page updates itself when the stream starts or ends. The stream is played from YouTube; nothing is downloaded or re-streamed.

Install: Plugins > Add New > Upload Plugin > activate, then open Settings > Minister Live.

## What visitors see
- Header: "Itinerary" label, the minister's name, and a status pill that reads **LIVE NOW** or **NOT LIVE**.
- Live: custom play/pause, mute and full-screen buttons (YouTube's own controls are switched off).
- Not live: a message that the minister is not live right now.
- A **Go-live alerts** switch. When on, the browser shows a notification as soon as the broadcast starts. Works while the page is open (a background tab is fine); it does not work after the page is closed.

## Shortcode options
`[minister_live name="Apostle Toluwalogo Agboola" label="Itinerary" notify="yes" autoplay="no" interval="30" offline_message=""]`

Colours live in the variables at the top of `assets/minister-live.css`.

Full instructions: see WORDPRESS-SETUP.md (shipped alongside this plugin).
