# Recorded HAFAS fixtures

Real `GET https://v6.bvg.transport.rest/stops/<id>/departures?duration=60&results=200&remarks=true`
responses, recorded once on Wednesday 2026-09-30 (one request per stop, 1.5 s apart) and
trimmed with `scripts/record_fixtures.py --trim`: every departure is kept in response order,
only fields the engine does not read are dropped, and no value is edited. Kind: `verbatim-trimmed`.

| File | Stop | Recorded (UTC) | What makes it this kind |
|---|---|---|---|
| departures_undisturbed.json | S+U Alexanderplatz (900100003) | 2026-09-30T05:12:39+00:00 | U2, U8 and S5 07:15–08:10 carry no cancellation, delay > 10 min or warning |
| departures_cancellation.json | S Westkreuz (900024102) | 2026-09-30T05:12:56+00:00 | S46 07:20 `cancelled: true` |
| departures_delay.json | S+U Berlin Hauptbahnhof (900003201) | 2026-09-30T05:12:45+00:00 | ICE 644 06:47 `delay: 1800`, reported under child stop 900003200 [Gleis 1-8] |
| departures_replacement_service.json | S+U Friedrichstr. (900100001) | 2026-09-30T05:12:53+00:00 | tram 12 warning "Replacement Service" (English text) |
| departures_construction.json | S+U Zoologischer Garten (900023201) | 2026-09-30T05:12:51+00:00 | S5 warning "Wegen Bauarbeiten am Bahnsteig …" |
| departures_warning.json | S Ostkreuz (900120003) | 2026-09-30T05:12:42+00:00 | RB32 warning "Störung."; RB26 cancelled with "Teilausfall Ostkreuz &#60;&#62; Lichtenberg" |

`tests/test_engine_recorded.py` runs the engine over these files. The synthetic fixtures
in `../engine/` stay for edge cases real data cannot pin down (DST, missing keys).
