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

## Departures with platform fields

Real `GET https://v6.bvg.transport.rest/stops/900120003/departures?duration=30&results=12&remarks=true`
response, recorded once and trimmed the same way as above, but keeping `platform` and
`plannedPlatform` too (charter G3: the "today" page shows the platform). Kind:
`verbatim-trimmed`.

| File | Stop | Recorded (UTC) | What makes it this kind |
|---|---|---|---|
| platforms_ostkreuz.json | S Ostkreuz (900120003) | 2026-09-30T15:20:13+00:00 | 13 departures, most with a `platform`/`plannedPlatform` track number |

## Departures with a platform change

Real `GET https://v6.bvg.transport.rest/stops/900003201/departures?duration=30&results=12&remarks=true`
response, recorded once and trimmed the same way as `platforms_ostkreuz.json`. Kind:
`verbatim-trimmed`.

| File | Stop | Recorded (UTC) | What makes it this kind |
|---|---|---|---|
| departures_platform_change.json | S+U Berlin Hauptbahnhof (900003201) | 2026-10-01T04:32:58+00:00 | RE20, planned 06:07, delayed to 06:32, platform 3 instead of planned 1 |

## Stop-search fixtures

Real `GET https://v6.bvg.transport.rest/locations?query=<q>&results=5` responses,
recorded with `scripts/record_fixtures.py --locations` (one request per query, 1.5 s
apart): only the fields the app reads (`type`, `id`, `name`, plus `station` id/name
when present) are kept; every other field is dropped, and no value is edited. Kind:
`verbatim-trimmed`.

| File | Query | Recorded (UTC) | Contains |
|---|---|---|---|
| locations_alexanderplatz.json | Alexanderplatz | 2026-09-30T11:23:23+00:00 | S+U Alexanderplatz Bhf (900100003) among 5 stop matches |
| locations_ostkreuz.json | Ostkreuz | 2026-09-30T11:23:25+00:00 | S Ostkreuz Bhf (900120003) among 4 stops and 1 POI |

`tests/test_web_stops.py` replays `locations_alexanderplatz.json` through `/stops` to
prove the real API shape survives the app's `type`/`id`/`name` filter unchanged.
