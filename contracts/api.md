# HTTP API (contract 2.0.0)

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
| `netbox_unreachable` | 502 | NetBox did not answer (timeout, connection refused) |
| `netbox_error` | 502 | NetBox answered 4xx/5xx to a read the proxy expected to work |
| `unsupported_netbox_version` | 502 | NetBox is not 4.7.x |
| `internal` | 500 (501 for not-yet-built routes) | anything else |

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
| site | `name`, `description`, `status`, `latitude`, `longitude`, `iso_theme` |
| location | `name`, `description`, `status`, `iso_x`, `iso_y`, `iso_w`, `iso_h`, `iso_facing`, `latitude`, `longitude` |
| rack | `name`, `description`, `status`, `comments`, `iso_x`, `iso_y`, `iso_facing`, `latitude`, `longitude` |
| device | `name`, `status`, `serial`, `asset_tag`, `description`, `comments` |
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
* The NetBox changelog is the audit trail; the proxy logs `reason` with each apply and sends it to NetBox as
  each write's `changelog_message` (`"rackwalker: <reason>"`, at most 200 characters).

## Caching (proxy-internal, visible only as `meta.source`)

Reads may be served from a short cache (`meta.source: "cache"`); every apply (also a refused or failed one)
invalidates it, and a read that was still running when an apply finished is not cached.
`meta.generated_at` is when the body was built.
