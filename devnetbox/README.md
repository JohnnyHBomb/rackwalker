# Dev NetBox

Throwaway NetBox **v4.7.4** stacks (image `netboxcommunity/netbox:v4.7.4-5.1.1`, i.e. netbox-docker 5.1.1;
postgres 18.6, valkey 9.1.3; every image pinned by tag and digest in `compose.yml`). Synthetic data only.
API auth uses v2 tokens (NetBox >= 4.5): `Authorization: Bearer nbt_<key>.<token>`; the stack env file carries
`API_TOKEN_PEPPER_1`, which v2 tokens need.

`nb.sh` runs `docker compose` with `compose.yml` against your **local Docker daemon** by default. Per-stack secrets
(DB/valkey passwords, `SECRET_KEY`, admin password, API token key/secret, API token pepper) are generated once into
`devnetbox/.stacks/<project>.env` (mode 600, gitignored). No privileged containers; only the NetBox web port is
published.

```sh
make netbox-up      NB_PROJECT=rackwalker NB_PORT=8100   # start; waits (bounded, 900 s) for /api/status/ = 200
make netbox-seed    NB_PROJECT=rackwalker NB_PORT=8100   # custom fields + synthetic network (idempotent)
make netbox-status  NB_PROJECT=rackwalker NB_PORT=8100   # version + counts as JSON
make netbox-env     NB_PROJECT=rackwalker NB_PORT=8100   # write .env for make dev
make netbox-down    NB_PROJECT=...  NB_PORT=...          # stop, keep volumes
make netbox-destroy NB_PROJECT=...  NB_PORT=...          # remove containers, volumes and the secrets file
make netbox-reset   NB_PROJECT=...  NB_PORT=...          # destroy + up + seed
```

* Side by side: every stack needs its own `NB_PROJECT` (lower-case letters, digits, `-`, `_`; it names the compose
  project, network and volumes) and its own `NB_PORT`. The defaults are `rackwalker` on 8100.
* Remote Docker host (optional): export `DOCKER_HOST` (for example `ssh://user@docker-host`) and set
  `NB_HOST=docker-host`, so the URL points at the published port there. Secrets stay on this machine. If that port
  is firewalled, run `make netbox-tunnel NB_PORT=<port>` (an `ssh -N -L` tunnel) and use
  `NB_URL=http://localhost:<port>`.
* Web UI login: user `admin`, password `SUPERUSER_PASSWORD` in the stack's secrets file.

## Seed data (`seed_model.py`, pushed by `seed.py`)

| | |
|---|---|
| Sites | North Depot (Brackenford), West Works (Wrenmouth), East Exchange (Saltings Cross): invented towns, made-up GPS, `iso_theme` set |
| Locations | 8 (server rooms, halls, comms cupboards, a store), with `iso_x/y/w/h/facing` |
| Racks | 12 (42U and 24U), with `iso_x/y/facing`; one rack and one room carry their own GPS |
| Devices | 60: switches (core/access), routers, firewalls, servers, patch panels, PDUs; 4 unracked; mixed statuses |
| Addresses | primary IPs from the documentation ranges 192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24 (one per site) |
| Cables | 82: servers -> access, access -> core uplinks, patch-panel trunks (front/rear ports), PDU power, BMC network, 2 carrier circuits between sites, 1 planned dark-fibre cable North <-> East |
| Custom fields | `iso_x`, `iso_y`, `iso_w`, `iso_h`, `iso_facing`, `iso_theme`, `iso_latitude`, `iso_longitude`, and for quests `rw_mgmt_mode`, `rw_mgmt_service`, `rw_mgmt_default`, `rw_mrc`, `rw_nrc`, `rw_currency` (group `rackwalker`); contact roles Support / NOC, Account manager, Billing |
| Circuits' paper side | one provider account, one support contact assigned to the carrier, and on each circuit an install date, a commit rate, made-up charges, and an address on its A end inside one prefix (198.18.0.0/29) |

Idempotent: objects are looked up by natural key (slug, name, cable label) and only created when missing;
existing ones are patched back to the model. A second run reports `created: 0, patched: 0`. `seed.py` refuses a
NetBox that is not 4.7.x or that has sites but not ours.
