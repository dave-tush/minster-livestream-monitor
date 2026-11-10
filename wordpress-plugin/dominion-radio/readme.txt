=== Dominion Radio ===
Requires at least: 5.8
Tested up to: 7.2
Requires PHP: 7.4
Stable tag: 2.6.3
License: GPLv2 or later

A listen-live radio player for the ministry website. Connects to the Dominion Radio backend.

== Description ==

Add the player to any page with the shortcode:

    [dominion_radio]

The player shows what is on air, a seekable progress bar, mute, and the next track. Every listener is kept in sync with the station.

Optional shortcode attributes: backend, name, tagline, offline_message, heading (h1, h2 or h3; default h2).

== Installation ==

1. Plugins > Add New > Upload Plugin, choose dominion-radio.zip, Install, Activate.
2. Settings > Dominion Radio: the backend URL is pre-filled with the production backend (change it only if the backend moves), optionally choose the portrait, then Save.
3. Click "Test connection". It must report "Connected" and "CORS OK".
4. Add the [dominion_radio] shortcode to the Radio page (Elementor: Shortcode widget, inside a Full Width container).

The backend must list this website's exact origin (for example https://toluwalogoagboola.org) in its CORS settings.

== Security ==

The backend admin API key (optional, used only by "Sync new uploads now") is never sent to visitors. Prefer defining DOMINION_RADIO_ADMIN_KEY in wp-config.php.

== Changelog ==
= 2.6.3 =
* The player now defaults to the production backend (https://minster-livestream-monitor.onrender.com) when no backend URL is saved, so it plays immediately after activation. Settings, the backend attribute on the shortcode, or the dominion_radio_default_backend filter still override it.

= 2.6.2 =
* Radio page redesigned as a native ministry inner page using measured Sermons/About tokens (dark hero section, Playfair Display headings with gold italic accent, Inter body, pill actions).
* Studio layout: player first with portrait as supporting 4:5 framed image; player remains visible without scrolling.
* Preserves all v2.4.0 functionality: automatic Radio page creation, native range seek, station/local status separation, shared sync.
= 2.3.0 =
* The Live Console: transparent page shell with an immersive dark player panel as the first element; portrait secondary below.
* Native <input type="range"> seek control with keyboard/ARIA announcements and visible focus; station (ON AIR) status kept separate from local player status.
* Player panel is fluid (up to 880px) and fully responsive; restrained gold accents, ministry palette.
= 2.2.0 =
* Premium Editorial Broadcast redesign: combines Concept B's immersive live-radio player with the editorial design language of the About page (toluwalogoagboola.org).
* New portrait-led hero with Oswald headline, eyebrow identity label, ON AIR badge and gold-rule separator.
* Refined broadcast console: prominent play button, current track/performer, seek bar, time, mute, Back to Live and compact Up Next.
* Responsive compositions for 1440px desktop, 768px tablet and 390px/375px mobile.
* All playback logic, backend integration and WordPress hooks unchanged.
= 2.1.0 =
* Integrated the actual Apostle Toluwalogo Agboola portrait (WebP with JPEG fallback, natural 2:3 proportions, no external image dependency).
* Matched the Radio page design to the toluwalogoagboola.org design system (dark #0a0908 background, cream #f5efe0 text, gold #f0d17e/#d4af37/#a97e2f accents, Playfair Display headings, Inter body).
* Removed the placeholder portrait and all preview harnesses/mock assets from the package.
= 2.0.0 =
* New immersive broadcast design, backend-accurate player, accessible seek slider, Up Next, Back to live, settings page with connection test.
