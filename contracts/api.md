# HTTP API (contract 2.3.0)

The browser talks only to the proxy. The proxy talks to NetBox with its token; the token never reaches the
browser, a response body, or a log line. Routes are in `openapi.yaml`; bodies are `$defs` in
`scene.schema.json`; examples are in `../fixtures/` (`index.json` maps each request to its file).

| Method and path | Body in | 200 body | Zoom level fed | Example |
|---|---|---|---|---|
| `GET /api/health` | - | `HealthResponse` | (boot) | `health.json` |
| `GET /api/scene/world` | - | `WorldResponse` | world | `world.json` |
| `GET /api/scene/site/{site_id}` | - | `SiteScene` | site, room | `site-1.json` .. `site-3.json` |
| `GET /api/scene/rack/{rack_id}` | - | `RackScene` | rack | `rack-1.json` .. `rack-12.json` |
| `GET /api/device/{device_id}` | - | `DeviceDetail` | device, info panel | `device-2.json`, `device-3.json`, `device-4.json` |
| `GET /api/trace/{kind}/{port_id}` | - | `TraceResponse` | device (on demand) | `trace-interface-173.json` (patch panels), `-210` (circuit), `-172` (no cable); `-21` and `-30` are 173 and 210 traced from the other end |
| `POST /api/changes/preview` | `ChangeSet` | `ChangeResult` (mode preview) | drag/edit | `changes-request.json` -> `changes-preview.json` |
| `POST /api/changes/apply` | `ChangeSet` | `ChangeResult` (mode apply) | drag/edit | `changes-request.json` -> `error-write-disabled.json` (403) |
| `GET /api/game/session` | - | `GameSession` | (game boot) | `game-session.json`, `game-session-signed-out.json` |
| `POST /api/game/session` | `SignInRequest` | `GameSession` + cookie | sign-in | `game-sign-in-request.json` -> `game-session.json` |
| `DELETE /api/game/session` | - | `GameSession` (signed out) | menu | `game-session-signed-out.json` |
| `GET /api/game/state` | - | `GameState` | (game boot) | `game-state.json`; signed out: `error-unauthorized.json` (401) |
| `PUT /api/game/saves/{slot}` | `SaveData` | `SaveSlot` | autosave | `game-save-request.json` -> `game-save.json` |
| `PUT /api/game/settings` | `GameSettings` | `GameSettings` | settings page | `game-settings.json` |
| `PATCH /api/game/flags` | `FlagsPatch` | `Flags` | dialogue | `game-flags-request.json` -> `game-flags.json` |

`/api/trace/interface/{id}` is the common form; `kind` accepts every `PortKind`. A port with no cable answers
200 with `hops: []`, `is_complete: false`, `is_split: false` (NetBox's `/trace/` returns `[]`).

Trace source per kind:

* `interface`, `console_port`, `console_server_port`, `power_port`, `power_outlet`, `power_feed`: NetBox
  `/trace/`. It carries no flags, so the proxy infers them from the last segment: `is_complete` when every last
  far end is a path endpoint kind (one of these six); `is_split` when the trace stops on rear port(s) (NetBox
  stops there only when it cannot choose a position).
* `front_port`, `rear_port`, `circuit_termination`: NetBox 4.7.4 answers 404 to `/trace/` for these, so the
  proxy reads `/paths/` and returns the tail of the lowest-id path in which the port is a near end;
  `is_complete`/`is_split` are that path's own NetBox values. No such path -> `hops: []`.

`DeviceDetail.interfaces` is ordered by kind in `PortKind` enum order, then within a kind exactly as NetBox's list
endpoint returns that device's ports (`?device_id=`); the proxy keeps NetBox's order and does not sort names itself.

Fixture ids are the live dev NetBox's ids on a stack seeded once from empty (`make netbox-reset`);
`make netbox-check-fixtures` compares every id, the port order and the trace fixtures with the live stack.

## Errors: one shape

Every non-2xx response is an `ErrorResponse`:

```json
{"error": {"code": "not_found", "message": "Site 999 does not exist.", "details": {"kind": "site", "id": 999}},
 "request_id": "3f2a9c1d0b7e"}
```

| code | HTTP | When |
|---|---|---|
| `bad_request` | 400 | malformed path/query: a non-integer id (`/api/scene/site/abc`), an unknown port kind |
| `not_found` | 404 | object or route does not exist |
| `validation_failed` | 422 | request body does not match the schema (FastAPI body validation is mapped to this) |
| `conflict` | 409 | reserved for whole-request conflicts; per-op conflicts go inside `ChangeResult.ops[].error` |
| `write_disabled` | 403 | `POST /api/changes/apply` while `WRITE_ENABLED` is not `1` |
| `unauthorized` | 401 | a game route without a valid session; a sign-in token NetBox does not accept (or that holds characters no NetBox token has); a change route of a write-enabled proxy without a signed-in player |
| `forbidden` | 403 | a game write, or a change request to a write-enabled proxy, without the session's `X-RackWalker-CSRF` header or with a foreign `Origin` |
| `netbox_unreachable` | 502 | NetBox did not answer (timeout, connection refused) |
| `netbox_error` | 502 | NetBox answered 4xx/5xx to a read the proxy expected to work |
| `unsupported_netbox_version` | 502 | NetBox is not 4.7.x |
| `internal` | 500 (501 for not-yet-built routes) | anything else; also a game state file the proxy cannot use (`details.store`: `newer_schema` or `unreadable`, the message says which) |

`message` is one human sentence; `details` is machine detail or `null`. `request_id` is always a string
(never `null`; the schema forbids it).

The generated pydantic models are lax (they coerce types and accept `null` for optional request fields), so the
proxy validates request bodies against `scene.schema.json` as well and answers 422 for anything the schema rejects.

## Changes: preview and apply

A `ChangeSet` is an ordered list of at most 100 ops. Each op is `{op, target: {kind, id}, fields, client_ref?, if_unmodified_since?}`:

| op | target | fields | NetBox effect |
|---|---|---|---|
| `move_rack` | `{kind: rack, id}` | any of `location_id`, `iso_x`, `iso_y`, `iso_facing` (at least one) | PATCH rack `location`, custom fields |
| `place_device` | `{kind: device, id}` | `rack_id`, `position` (multiple of 0.5), `face` (all required) | PATCH device `rack`, `position`, `face`, and `location` = the rack's location when it differs (NetBox 4.7 refuses a rack in another location: measured 400 "Rack ND-R1 does not belong to location Store."); `before`/`after`/`diff` then include `location_id` |
| `unplace_device` | `{kind: device, id}` | `{}` | PATCH device `rack`, `position`, `face` to null |
| `create_rack` | `{kind: rack, id: null}` | `site_id`, `name` required; `location_id`, `status` (default planned), `u_height` (default 42), `iso_x`, `iso_y`, `iso_facing` | POST rack |
| `update_fields` | `{kind, id}`, kind in site, location, rack, device, cable, interface | whitelist below | PATCH |

References: an op may name an object created earlier in the same ChangeSet as `"@<client_ref>"` (in
`target.id`, `rack_id` or `location_id`), e.g. create rack `client_ref: "newrack"` then
`place_device ... rack_id: "@newrack"`. In a preview the reference stays as the string, in `fields`, `diff` and
`OpResult.target.id` alike (`target.id` accepts a `Ref`); after apply, `OpResult.target.id` and every
resolved field are the real id. `changes-preview.json` op 5 moves `@newrack`.

`update_fields` whitelist (contract field names; anything else -> op error `validation_failed` with
`details.allowed`):

| kind | editable fields |
|---|---|
| site | `name`, `description`, `status`, `latitude`, `longitude`, `iso_theme`, `facility`, `country` (2.3.0: two upper-case letters or null; written to the custom field `iso_country`) |
| location | `name`, `description`, `status`, `iso_x`, `iso_y`, `iso_w`, `iso_h`, `iso_facing`, `latitude`, `longitude` |
| rack | `name`, `description`, `status`, `comments`, `iso_x`, `iso_y`, `iso_facing`, `latitude`, `longitude` |
| device | `name`, `status`, `serial`, `asset_tag`, `description`, `comments`, `platform_id` (2.3.0: an existing platform's id, or null) |
| cable | `label`, `status`, `type`, `color`, `length`, `length_unit` |
| interface | `description`, `enabled` (kind interface only) |

Semantics:

* **Preview** never writes. It reads current NetBox state, validates every op (existence, whitelist, U-slot
  free and in range, rack/device in the same site, `if_unmodified_since` not older than `last_updated`), and
  returns per-op `before`, `after` (predicted) and `diff`. It also checks the NetBox 4.7 rules a write would
  hit: rack name unique per location (case-sensitive; racks without a location never collide), location name
  unique per site and parent (case-sensitive), device name unique per site and tenant (case-insensitive), and a
  cable `length` needs a `length_unit`. Also: a device `asset_tag` is unique across all of NetBox and a
  site `name` across all sites (both case-sensitive; the proxy reads the current holders of the value), and a site
  `latitude`/`longitude` has at most 6 decimal places (NetBox stores them as decimal(8,6)/(9,6); rack and location
  coordinates are decimal custom fields without that limit). A lock conflict (`if_unmodified_since` older than
  `last_updated`) is `conflict` with `details.field: "if_unmodified_since"`, `last_updated` and
  `if_unmodified_since`; its message names the object for people and carries no ids or raw timestamps. Per-op problems are `status: "error"` with an
  `ErrorBody`; the HTTP status is still 200.
* **Who may write.** A proxy with `WRITE_ENABLED=1` answers both change routes only for a signed-in player (the
  game's session cookie, see "Game state") with the session's `X-RackWalker-CSRF` header and no foreign `Origin`:
  without a session 401 `unauthorized`, without the header 403 `forbidden`. In the single-user local mode the header
  alone is enough (`local`). A dry-run proxy previews for anyone, as before. The write itself still uses the proxy's
  NetBox token, so NetBox records the proxy's user as the author; the changelog message names the player (below).
  **The player's own NetBox permissions must cover the write (2.3.0).** Before `POST /api/changes/apply` writes, the
  proxy reads the signed-in player's NetBox object permissions from NetBox with its own token (for every write; only
  the quest log uses an answer up to 30 seconds old) (`GET /api/users/users/<id>/`,
  `GET /api/users/users/?id=<id>&is_superuser=true`, `GET /api/users/permissions/?user_id=<id>&enabled=true` and the
  same with `group_id` for each of the user's groups) and needs, for every op, an enabled permission without
  constraints that names the object type and the action: `change` on the target's type for `update_fields`,
  `move_rack`, `place_device` and `unplace_device`; `add` on `dcim.rack`, `dcim.cable`, `circuits.circuit`,
  `circuits.circuittermination` and `ipam.prefix` for the ops that create them; `add` on `ipam.ipaddress` and `change`
  on `dcim.device` for `assign_ip`. A superuser may do everything; an inactive user nothing. A permission with
  constraints never counts (the proxy cannot evaluate a constraint). Otherwise the answer is 403 `forbidden` with
  `details.reason` `player_lacks_permission` and `details.missing` (`[{object_type, action, constrained}]`), and nothing
  is written. When NetBox refuses the proxy's token the view of users or of object permissions, writes answer 502
  `netbox_error` with `details.reason` `proxy_cannot_view_users` or `proxy_cannot_view_permissions`, and `/api/health`
  reports the same in `netbox.permission_check`. The single-user local mode has no NetBox user and is not checked.
  Permissions are read at most every 30 s per player and again after any write. Preview is not restricted.
* **Apply** is refused with 403 `write_disabled` unless the proxy runs with `WRITE_ENABLED=1` (dry-run is the
  default). Otherwise it runs the preview first; if any op errors, nothing is written and the answer is 200
  with `applied: false`. Then it writes ops in order. If a write fails, later ops are `skipped` and
  `applied: false` (NetBox has no cross-request transaction; earlier ops stay written and are visible in the
  NetBox changelog). `after` is read back from NetBox.
* After an error in the validation pass, nothing is written and every op that has no error of its own is
  reported `status: "skipped"` with `after: null`. After a failed write, the ops already written stay `ok` with
  `after` read back from NetBox (they are in NetBox), the failing op is `error`, and every later op is `skipped`
  with `after: null`. A failed write's `error.details` carry `partial` (`true`: some ops of the set are in
  NetBox; `false`: nothing was written; `null`: unknown, see `outcome_unknown`), `written_ops` and `skipped_ops`
  (op indexes), `outcome_unknown` and `netbox` (NetBox's error body, when it sent one).
* `outcome_unknown: true` means the failing op's request reached NetBox's connection but no answer came back (read
  timeout, connection lost): NetBox may or may not have committed that op. Its code is `netbox_unreachable` and the
  message says so; the client must check the NetBox changelog (or reload) before retrying. A failure before the
  request was sent (connect error or timeout) is `outcome_unknown: false`.
* Per-op error codes: a U slot occupied by another device -> `conflict` (`details`: `field: "position"`,
  `reason: "U<n> occupied by device <id>"`, `device_id`); outside the rack's units, a zero-U device, a rack in
  another site -> `validation_failed`.
* `status` per op: `ok`, `no_change` (target already had those values), `error`, `skipped`.
* The NetBox changelog is the audit trail; the proxy logs the player and `reason` with each apply and sends both to
  NetBox as each write's `changelog_message` (`"rackwalker (<username>): <reason>"`, at most 200 characters).

## Change ops added in 2.3.0

Each creates one NetBox object (status `active` / `connected`) or assigns an address; all go through preview and apply
like the others, and each write carries the changelog message.

| Op | Target | Fields | Refused when |
|---|---|---|---|
| `connect_cable` | `{kind: "cable", id: null}` | `a_kind`, `a_id`, `b_kind`, `b_id` (an end is an `interface`, `front_port`, `rear_port`, `power_port`, `power_outlet` or `circuit_termination`; an id or `@<client_ref>` of a termination made earlier in the set), optional `type`, `label` | an end does not exist (`not_found`); an end already has a cable (`conflict`, `details.cable_id`); both ends are the same; a power end meets a data end |
| `create_circuit` | `{kind: "circuit", id: null}` | `cid`, `provider_id`, `type_id` | the provider or circuit type does not exist; the provider already has a circuit with that `cid` |
| `create_circuit_termination` | `{kind: "circuit_termination", id: null}` | `circuit_id` (id or `@ref`), `term_side` (`A` or `Z`), `site_id` | the circuit or site does not exist; that side already has a termination (`conflict`) |
| `assign_ip` | `{kind: "device", id}` | `address` (with mask), `interface_id`, optional `primary` (default true) | the interface is not on that device; the address is not valid or has no mask; the same host address already exists elsewhere (`conflict`). An address already on that interface is reused |
| `create_prefix` | `{kind: "prefix", id: null}` | `prefix` (host bits zero) | not a network; that prefix exists (`conflict`) |

Ids above 9,000,000 belong to the practice island, which is not in NetBox: both change routes refuse them with 400
`bad_request` (`details.reason` `practice_island`).

## Quests (2.3.0)

`docs/quests.md` describes the rules for a NetBox admin; this section fixes what the routes do.

* **The estate.** The quest routes analyse one bounded read of NetBox (slim field selections, capped lists), kept for
  `RACKWALKER_ESTATE_TTL` seconds (default 60) and dropped by any write made through the proxy. In fixture mode the
  estate is `fixtures/estate.json`. Lists that were capped, or that NetBox refused to the proxy's token, are named in
  `incomplete`; rules that need them report nothing.
* **`GET /api/quests/gaps/{site_id}?offset=&limit=`** (no sign-in, like a scene): the site's gaps, one per empty
  field, id `<object type>:<object id>:<field>`, ordered by rank, object type, object id, field; `limit` 1 to 500
  (default 100). `unlisted` counts expected-cable gaps beyond the per-site cap of 50. `completeness` is checks passed
  out of checks made at the site.
* **`GET /api/game/quests?scope=netbox|practice&site_id=&fresh=`**: the quest log (`fresh=true` reads NetBox now, for a
  change made in NetBox itself within the last minute: with `site_id` that site only, at most every 5 seconds;
  without it the whole estate, at most every 15 seconds; asked sooner, the kept read answers). Without `site_id`,
  the lines open at every site. At most three fresh lines are listed per site (the most valuable first: connectivity, identity,
  layout; the first line of each kind before a second line of any kind), parked and practised lines beside them, the
  rest counted in `queued`; then the last finished lines.
  `next_quest_id` is the most valuable fresh line of the scope. `can_write` is false, with `write_blocked` saying why,
  on the practice island, in fixture mode, on a dry-run proxy, when the proxy cannot read the player's NetBox
  permissions, and when those permissions cover nothing a quest writes. When they cover only some of it, `can_write`
  stays true and `write_blocked` says in plain words what is missing. `Quest.write_blocked` says the same for one line's own
  proposal, so the game can say "practice run" before the player presses Apply.
  A list that was cut at its cap or that NetBox refuses to the proxy is named in `incomplete`; then no site is
  reported above 99 %, `boss_ready` and `all_in_order` are false, and no spot check is offered.
* **Statuses.** `offered` (not started), `active` (a question waits), `ready` (a change waits for preview and apply;
  with `proposal: null`, NetBox already shows the outcome and only `/verify` is needed), `parked` (the player
  answered "I don't know yet": `checklist` says what to look for; no penalty), `dismissed` (the player says the
  finding is how things are), `practised` (completed as a dry run), `done` (NetBox confirms it).
* **`POST /api/game/quests/answer`** takes the answer to the step the line is waiting at: `choice` (a choice id),
  `text`, or `values` (form field id to value; blank or absent means not known). An answer to any other step
  changes nothing and returns the line as it stands; `step: "restart"` clears the line's answers and
  `step: "back"` takes back one answer (the question a parked line waits at, else the last one answered). Choice kinds:
  `candidate` (an object of the estate), `other` (not listed, or a typed value follows: the id `named` asks for the
  name of any other object of that kind in NetBox, in the step `<step>_name`), `unknown` ("I don't know yet"),
  `not_applicable` (also on a form step: `choice: "none"` closes the line with nothing written), `confirm`, `skip`. A typed value or a form value that does not fit comes back as the same
  step with `error`.
* **`POST /api/game/quests/preview`** previews the proposal exactly as `/api/changes/preview` would. Never writes.
* **`POST /api/game/quests/apply`**: on the practice island the proposal changes the player's island (game store
  only). On the player's NetBox it is written through the same code as `/api/changes/apply`, only when the proxy is
  write-enabled and the player's NetBox permissions cover it; otherwise it is a dry run (`mode: "dry_run"`, the line
  becomes `practised`, no XP, the message says why). The player's answers are kept by a dry run, by a refused write
  and by a check that confirms nothing; when part of an errand is confirmed, only the answers about that part go.
  After a write the proxy reads the sites the change reached again (not the whole estate) and analyses the estate
  again: `verified` and `complete` come from that read, never from the request. `trace` quotes NetBox's own
  `/trace/` from the first interface the proposal cabled. One apply runs at a time per player. A change to an
  existing site, rack or device carries `if_unmodified_since` (the object's `last_updated` when the question was
  asked), so a value recorded in NetBox meanwhile is never written over. When NetBox takes nothing of the change,
  the answer is `mode: "real"`, `verified: false`, no XP, `result.applied: false`, and `message` gives NetBox's
  reason; the kept estate is dropped.
* **Settlement.** A check of a line is *confirmed* only when the fresh analysis makes that check and it passes. A
  check the analysis no longer makes (the object was deleted, is no longer live, or moved) confirms nothing; while a
  list is incomplete such a check counts as still open. A line left with no open check and nothing confirmed is
  closed as "no longer applies": its row is deleted, the verdict's quest has status `dismissed`, `verified` and
  `complete` are false and nothing is paid. What was confirmed stays counted when it is undone later (`lapsed`); a
  `done` line is never reopened by `/verify`. Each check pays once per player (store table `paid`), whichever line
  confirms it and however many requests arrive at once; the row is read, paid and saved in one store transaction.
* **Line ids.** A quest id is `q:` + its finding's id. `interface_no_cable` is per device
  (`q:interface_no_cable:device:<id>`, checks `interface:<id>:cable` for each of its ports). A finding about a group
  (`site_island`, `address_no_prefix`) is named after its first member and has one check per member; when the first
  member goes, a begun line of that player is moved to the finding's new id (same kind, a shared open check).
* **`POST /api/game/quests/verify`** does the read and the settlement without a write (scope `netbox` only): the
  line's own site is read again, at most every 5 seconds. A spot check is refused (400): it is answered by the
  player. A spot-check id is also refused by `/answer` (409 `conflict`) at a site that still has something open.
* **Rewards.** XP per settlement: the line's XP (120, 50 or 30 by rank; 50 for `address_no_prefix`) times the share
  newly confirmed of one pass (checks newly confirmed / min(checks the line stands for, 4), at most 1), times 1, 0.8,
  0.6 or 0.4 for the first, second, third and later XP award of that quest kind at that
  site. Only `mode: "real"` pays. A potion with each new level (250 XP). `label_maker` with the first completed
  identity line, `cable_tracer` with the first completed connectivity line. A line that was done and whose checks
  are open again in NetBox is `lapsed`: nothing is reopened or taken away; `Rewards.lapsed` counts them. A finished
  errand that gains an object it never covered (a new device with no serial in an audited rack) is offered again
  for that object. A check the player answered with "it has none" (a device nobody logs in to) counts as answered
  in that player's completeness and pays nothing.
* **Site boss.** `SiteProgress.boss_ready` is true when the site has devices and no open check (every check of a
  dismissed line, and every "it has none" answer, counts as answered for that player; a parked or practised line
  does not). `POST /api/game/boss/ack` records that the player has seen it (409 `conflict` when the
  site is not ready).
* **Practice island.** Site id 9,000,001 and the ids of its rooms, racks, devices and ports are made up by the
  proxy. `scope: "practice"` plays on it; `GET /api/scene/site|rack/...`, `/api/device/...` and `/api/trace/...`
  answer for those ids with `meta.source: "fixture"` and the signed-in player's practice changes applied;
  `POST /api/game/practice/reset` puts the island and its quest log back. Nothing about it is read from or written
  to NetBox.

## Caching (proxy-internal, visible only as `meta.source`)

Reads may be served from a short cache (`meta.source: "cache"`); every apply (also a refused or failed one)
invalidates it, and a read that was still running when an apply finished is not cached.
`meta.generated_at` is when the body was built.

## Site country (2.2.0)

`Site.country` is the site's ISO 3166-1 alpha-2 country code, upper case, or `null` when unknown. The proxy takes the
site's custom field `iso_country` (text, `^[A-Z]{2}$`, created by `devnetbox/seed.py`) when it holds two letters, else
the site's own region (not its parents): its slug or name when that is a two-letter code (`gb`) or a country name the
proxy knows (`United Kingdom`, `Netherlands`, `UK`; `proxy/rackwalker_proxy/country.py`), else the last line of the
site's physical address read the same way (`... Utrecht, Netherlands`), else `null`. Two-letter codes that are not
countries (`EU`, `UN`, `XX`, `ZZ`) count as unknown. Only the overworld uses it, to pick a region's landmark and villager set; it is read-only for the editor.

## Game state (`/api/game/*`, added in 2.1.0)

The game keeps its own state in one SQLite file owned by the proxy (`<RACKWALKER_STATE_DIR>/game.sqlite3`, default
`data/game.sqlite3`, gitignored, mode 600). It is never written into NetBox and no game route sends a request to
NetBox, except the identity check at sign-in and the recheck of that user (below). The file is schema-versioned (`PRAGMA user_version`, forward
migrations only); a file from a newer RackWalker is refused, never rewritten.

* **Player = NetBox user.** `POST /api/game/session {token}`: the proxy calls NetBox `GET /api/authentication-check/`
  with that token (NetBox >= 4.3), takes the user's `id`, `username` and name, and forgets the token. It is never
  stored, logged or used again; every NetBox read and write still uses the proxy's own token. Why a token rather
  than a password: no password ever reaches the game; NetBox has no side-effect-free password check over the API
  (token provisioning creates a token); and it works the same for local, LDAP and SSO users. NetBox answering
  401/403 -> `unauthorized`.
* **What the token may do carries over (2.3.0).** In the same sign-in the proxy asks NetBox, with the player's token,
  for that token's own record (`GET /api/users/tokens/?key=<the public key part of a v2 token>`; every user may list
  their own tokens) and keeps on the session its NetBox id (not a secret), whether it may write (`write_enabled` and
  `enabled`) and when it expires. A session started with a read-only token plays in dry-run: `POST /api/changes/apply`
  answers 403 `forbidden` with `details.reason` `read_only_token`, a quest's apply is a dry run, and the quest log's
  `can_write` is false with `write_blocked` saying why. A session never outlives its token's `expires`. A token whose
  record NetBox does not show (a legacy v1 token: looking it up would put the secret in a URL) is taken as read-only.
  Sessions from before this rule may not write until the player signs in again.
* **Session.** A random cookie `rackwalker_session` (HttpOnly, SameSite=Strict, Path=/api, 7 days;
  `RACKWALKER_COOKIE_SECURE=1` adds Secure behind https). Path `/api` so the change routes see it too (writes need a
  player). Only its SHA-256 is stored, with the player id and a CSRF token. Sign-out deletes it, and signing in again
  ends the previous session of that browser.
* **Recheck (2.2.0).** The proxy asks NetBox, with its own token, whether the session's user still exists and is
  active (`GET /api/users/users/<id>/`, `is_active`): before every write (game writes and NetBox change writes), and on
  reads when the last good answer is older than `RACKWALKER_SESSION_RECHECK` seconds (default 60). A user NetBox no
  longer has (404) or has deactivated loses every session at once: the request answers 401 `unauthorized` with
  `details.reason` `gone` or `inactive` (`GET /api/game/session` answers signed out and clears the cookie). At the
  same moments the session's token is looked up again by its id with the proxy's token (`GET /api/users/tokens/<id>/`;
  the proxy's NetBox user needs view on `users.token`): a token that was deleted, disabled or has expired ends that
  session (401 with `details.reason` `token_gone`, `token_disabled` or `token_expired`), and a changed write flag or
  expiry is taken over. When the proxy may not view tokens, the facts from sign-in stand. If NetBox
  cannot answer, reads go on and writes answer 502 `netbox_unreachable` until NetBox answers again; sign-out never needs
  NetBox. The proxy's NetBox user needs view permission on users (`devnetbox/proxy_tokens.py` grants it): when NetBox
  refuses the lookup to the proxy's token (401 or 403), sessions are kept, reads go on and writes answer 502
  `netbox_error` with `details.reason` `proxy_cannot_view_users`, which does not mend itself. A session the recheck
  ended keeps answering 401 with its `details.reason` to that browser until it signs in again or its cookie is
  cleared. In fixture mode a player whose `fixtures/players.json` entry has `"active": false` cannot sign in, and a
  user that file does not list counts as deleted.
* **CSRF.** Every write (`POST`/`PUT`/`PATCH`/`DELETE` under `/api/game/`) needs the `X-RackWalker-CSRF` header: the
  session's `csrf_token` (from `GameSession`), or any value on sign-in. A custom header cannot be sent cross-site
  without a CORS preflight, which the proxy never grants; an `Origin` header, when present, must name an allowed host.
  The Host allow-list (`RACKWALKER_ALLOWED_HOSTS`) applies to the game routes as to all others.
* **Keyed by NetBox user id.** Players never see each other's state. Saves: slot `auto` (the game writes it on every
  change of place) and `1`-`3`; each write bumps `rev`; last write wins. Settings default as in
  `game-settings.json`. Flags: names `^[a-z0-9][a-z0-9_.:-]{0,63}$`, values boolean, integer or short string, at most
  500 per player. `PUT /api/game/settings` refuses a key bound to two actions (`validation_failed`).
* **Keys (2.2.0).** `KeyBindings` also holds the keys used inside buildings: `zoom_in`, `zoom_out`, `search`,
  `rotate`, `trace`, `unrack` and `undo` (pressed with Ctrl or Cmd); `interact` enters the selected object and
  `cancel` (now `Escape` and `Backspace` by default) goes one level out. Settings stored before 2.2.0 take the new
  actions' defaults; where a new default key is already one of the player's own, the player's binding wins.
* **Stored rows are checked when they are read**, not only when they are written, so `GET /api/game/state` always
  answers a valid `GameState`: settings keep every part that is still valid and take the default for the rest (a
  setting added in a later version reaches existing players with its default; keys bound to two actions fall back to
  the default keys); a save slot or flag that does not match the contract is left out of the answer and logged (the
  row stays in the file until the next write to it).
* **The file is created at the first sign-in**, not before: `GET /api/game/session` without a session cookie answers
  `signed_in: false` without touching it. Store work runs off the request loop, so a slow disk delays only the game
  call that is waiting for it, never the scene routes or `/api/health`.
* **Single-user local mode** (`RACKWALKER_LOCAL_PLAYER=1`, off by default): no sign-in, one player with id 0. It
  bypasses identity, so it is only for a NetBox you alone use. Writes still need the CSRF header (`local`).
* **Fixture mode** has no NetBox: sign-in accepts the synthetic tokens in `fixtures/players.json` and the store is
  real, so the game shell runs end to end on fixtures (`make dev-fixtures`, `make e2e`). Their index entries carry
  `"store": true`: examples for the schema checks, not canned answers.
