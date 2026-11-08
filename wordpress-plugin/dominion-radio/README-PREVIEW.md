# Dominion Radio — design previews (temporary, NOT plugin source)

Run:  `python3 serve.py`  then open http://127.0.0.1:8765/
(serve.py mocks /radio/now and /radio/audio/{id}; it never contacts the real backend.)

* concept-a.html / concept-b.html / concept-c.html — the three previews
* assets/js/dominion-radio.original.js — byte-identical copy of the plugin JS
* assets/js/dominion-radio.preview.js — same file + ONE added line (a `loadNow` shim; see report)
* The portrait loads from the real URL; if offline it falls back to a labelled stand-in.
* Header/footer are mock chrome to judge integration; the real ones come from the WordPress theme.
* build.py regenerates the HTML; shoot.py / interact.py reproduce screenshots and behaviour tests.
