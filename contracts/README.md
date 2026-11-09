# Contracts (version 2.2.0)

These files are the interface between the two halves of RackWalker: the Python proxy (`proxy/`) and the browser
game (`web/`). Each side builds and tests against them on its own, so a change here is deliberate and versioned
(see "Changing a contract" below).

| File | What it fixes | Who implements it |
|---|---|---|
| `scene.schema.json` | Every JSON body between web and proxy (JSON Schema 2020-12, `$defs`). **Source of truth.** | proxy produces, web consumes |
| `openapi.yaml` | The 15 HTTP routes (8 scene and change routes, 7 game routes), wired to the `$defs` above | proxy |
| `api.md` | Route rules: errors, status codes, ChangeSet semantics, the `update_fields` whitelist, caching | proxy, web editor |
| `iso.md` + `web/src/contracts/iso.ts` | Tile size, axes, facing, screen mapping, depth key, zoom levels, GPS projection | web engine |
| `theme.md` + `theme-slots.json` + `web/src/contracts/theme.ts` | Theme interface, palette keys, the 21 sprite slots with size and anchor | web engine, themes |
| `dialogue.schema.json` | The dialogue script format (`web/src/game/scripts/*.json`): nodes, choices, branches, flags, `{key:<action>}` tokens. Not an HTTP body; the graph rules are in `validateScript` (`web/src/game/dialogue.ts`) | web (game data) |
| `../fixtures/` | One canned response per route, rendered from the dev NetBox seed model | web (fixture mode), tests |

## Generated code (never edit by hand)

| Generated file | From | Command | Freshness check |
|---|---|---|---|
| `proxy/rackwalker_proxy/contracts_gen.py` (pydantic v2) | `scene.schema.json` | `proxy/gen-models.sh` | `proxy/tests/test_generated_fresh.py` |
| `web/src/contracts/scene.gen.ts` (TS types) | `scene.schema.json` | `cd web && npm run gen:types` | `web/tests/contracts.test.ts` |
| `fixtures/*.json` | `devnetbox/seed_model.py` | `python fixtures/make_fixtures.py` | `proxy/tests/test_generated_fresh.py` |

`make gen` runs all three. `make test` fails if any generated file is stale.

## The scene model in one page

```
WorldResponse   meta, sites[ {site: Site, counts: SiteCounts} ], links[ WorldLink ]        zoom world
SiteScene       meta, site, locations[], racks[], devices[], cables[]                       zoom site, room
RackScene       meta, rack, devices[] (rack_id = rack), cables[] touching them              zoom rack
DeviceDetail    meta, device, info, interfaces[] (every port kind), cables[]                zoom device
TraceResponse   meta, trace { origin, hops[ {near_end[], cable|null, far_end[]} ], is_complete, is_split }
ChangeSet       ops[ move_rack | place_device | unplace_device | create_rack | update_fields ], reason?
ChangeResult    mode, applied, write_enabled, ops[ {index, op, target, status, before, after, diff[], error} ]
ErrorResponse   { error: {code, message, details}, request_id? }   (every non-2xx answer)

GameSession     signed_in, player: Player|null, csrf_token, local_mode                     sign-in (cookie session)
GameState       contract_version, player, settings: GameSettings, flags: Flags, saves[ SaveSlot ]
SaveSlot        slot (auto|1|2|3), rev, updated_at, data: SaveData {scene, overworld {x,y,facing}, iso|null}
```

Game bodies (2.1.0) are the game's own state, kept by the proxy in SQLite and never written into NetBox; rules in
`api.md` "Game state".

Entities: `Site`, `Location` (a room), `Rack`, `Device` (with `Role`, `DeviceType`), `Interface`
(any cable-able port; `kind` says which), `Termination` (a cable end), `Cable`, `CableRef`, `CableTrace`.

Rules that both sides rely on:

1. **Every property is required; absence of a value is `null`.** Objects are closed (`additionalProperties: false`)
   so drift fails loudly. The only optional properties are in requests (`ChangeSet.reason`, `client_ref`,
   `if_unmodified_since`, some `fields`). Python serialises with `model_dump(mode="json", exclude_unset=True)`.
   Exceptions that are open on purpose (`additionalProperties: true`, so the TS types are indexable):
   `DeviceInfo.custom_fields`, `OpResult.before/after`,
   `ErrorBody.details`; `FieldDiff.before/after` take any JSON value.
2. **Ids are NetBox primary keys.** Port ids are unique only per kind, so a port is identified by `(kind, id)`.
   Fixture ids equal the dev NetBox's ids on a stack seeded once from empty; `make netbox-check-fixtures` proves
   it (every id, each device's port order, the trace fixtures). NetBox creates a device's ports in the natural
   order of its templates, which is why `seed_model.py` lists them in that order (e.g. `mgmt0` first on EX-C9000).
   Kinds: `interface, front_port, rear_port, console_port, console_server_port, power_port, power_outlet,
   power_feed, circuit_termination`.
3. **Layout lives in NetBox custom fields** (created by `devnetbox/seed.py`, group `rackwalker`):

   | Custom field | Type | On | Contract field |
   |---|---|---|---|
   | `iso_x`, `iso_y` | integer | Location, Rack | `iso_x`, `iso_y` |
   | `iso_w`, `iso_h` | integer (min 1) | Location | `iso_w`, `iso_h` |
   | `iso_facing` | selection (choice set `iso-facing`: N, E, S, W) | Location, Rack | `iso_facing` |
   | `iso_theme` | selection (choice set `iso-theme`) | Site | `iso_theme` |
   | `iso_country` | text (`^[A-Z]{2}$`) | Site | `country` (else the site's region or address; `api.md`) |
   | `iso_latitude`, `iso_longitude` | decimal | Location, Rack | `latitude`, `longitude` |

   NetBox 4.7 returns a selection custom field as `{"value": "N", "label": "North (-y)"}` (it still accepts
   the raw value on write); the contract fields carry the raw `value` only (`"N"`), so the proxy unwraps it.
   Site GPS is NetBox's native `latitude`/`longitude`. Rack U position/face and cables are native.
   `null` iso coordinates mean "not placed": the engine auto-lays them out deterministically and never writes
   that back unless the user drags.
4. **Colours** from NetBox (`Role.color`, `Cable.color`) are 6 lower-case hex digits without `#`; theme colours
   are `#rrggbb`.
5. **Time** is RFC 3339 with an offset. `last_updated` on Site/Location/Rack/Device feeds the optimistic lock
   `if_unmodified_since` on change ops.
6. **Supported NetBox**: 4.7.x for contract 2.2.0 (`/api/health` reports `netbox.supported`). The proxy
   authenticates with a v2 token (`Authorization: Bearer nbt_<key>.<token>`, NetBox >= 4.5); a legacy v1 token
   (deprecated in 4.6, removed in 5.0) is still sent as `Token <hex>`.
7. **Trace normalisation**: NetBox's `/trace/` segments become `hops` in order. Crossing a circuit is a hop with
   `cable: null` from one circuit termination to the other. If NetBox represents a case differently, the proxy
   normalises to this form; the web side never sees raw NetBox shapes.

## Change record

| Version | Date | Change |
|---|---|---|
| 2.0.0 | 2026-11-08 | First public contract of World of Confusion: RackWalker. Bodies cover NetBox data only (sites, locations, racks, devices, ports, cables, traces, change sets); targets NetBox 4.7.x. |
| 2.1.0 | 2026-11-08 | Additive: the game shell. Seven `/api/game/*` routes (sign-in as a NetBox user, sign-out, game state, save slots, settings, flags), their `$defs` (`GameSession`, `SignInRequest`, `Player`, `GameState`, `GameSettings`, `KeyBindings`, `SaveData`, `SaveSlot`, `Flags`, `FlagsPatch`, ...), error codes `unauthorized` (401) and `forbidden` (403), fixtures `game-*.json`, `players.json`, `error-unauthorized.json`. Scene and change bodies are unchanged apart from `contract_version`. Rules (`api.md`): with `WRITE_ENABLED=1` the change routes need a signed-in player and the session's CSRF header, and the changelog message names the player (`rackwalker (<username>): <reason>`); the session cookie's path is `/api`; stored game rows are checked on read. |
| 2.2.0 | 2026-11-09 | Additive, for the overworld: `Site.country` (ISO 3166-1 alpha-2 or null; custom field `iso_country`, else the site's region code or country name, else its address's last line). `KeyBindings` gains `zoom_in`, `zoom_out`, `search`, `rotate`, `trace`, `unrack`, `undo` so every key inside a building is remappable (`cancel` defaults to Escape and Backspace); the new actions are optional, so 2.1.0-shaped settings stay valid, and the proxy fills a missing one with its default on read and write. Rules (`api.md`): the proxy rechecks a signed-in player's NetBox user (exists, active) before every write and on reads every 60 s (`RACKWALKER_SESSION_RECHECK`); a deleted or deactivated user gets 401 with `details.reason`. Fixtures regenerated; the seed sets `iso_country` = `GB` on its three sites. |

## NetBox 4.7 notes

Every NetBox field the scene model maps from (Site, Location, Rack, Device, DeviceType, Role, Interface,
front/rear/power ports, Cable terminations, CircuitTermination, `/trace/`, circuit-termination `/paths/`) was
checked against a live NetBox 4.7.4. NetBox-side details the proxy absorbs, none of which reaches the web side:

- Selection custom fields (`iso_facing`, `iso_theme`) read back as `{value, label}` objects (4.7); write the raw value.
- `CircuitTermination.site` is gone (4.2): the site is `termination` when `termination_type == "dcim.site"`
  (feeds `Termination.site_id`).
- `FrontPort.rear_port`/`rear_port_position` are gone (4.5): mappings are the `rear_ports` list
  (`[{position, rear_port, rear_port_position}]`); `RearPort.front_ports` is the reverse. Traces are unchanged.
- `/api/dcim/cable-terminations/` is read-only (4.5): cable ends are written on `/api/dcim/cables/` only.
- Auth: v2 tokens (`Bearer nbt_...`, 4.5); token plaintexts cannot be read back (4.5) or chosen on create (4.7).

## Changing a contract

1. Describe the change (what, why, which side is affected) and whether it is additive (new optional request
   field, new `$def`) or breaking.
2. Bump `contract_version` (patch for rules only, minor for additive, major for breaking) in `scene.schema.json`
   (`Meta.contract_version`, `HealthResponse.contract_version`, `$comment`), in `openapi.yaml`, here, and in the
   two generated files (`make gen`).
3. Update fixtures (via `devnetbox/seed_model.py` or `fixtures/make_fixtures.py`) and both test suites in the
   same commit. `make test` green on both sides is the gate.
