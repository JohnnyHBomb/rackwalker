# Packs

Data the game ships with to help set a NetBox up: the common providers of a country, the device types and module types
of the community device-type library, and what a vendor's serial number looks like. Data only: a new country is one
JSON file in `providers/` (and one line for its name in the proxy's country table, where the game does not know it yet).

23 of the 49 country packs are marked `partial`: each says in `coverage` and `missing` what it lacks and why.
A thin pack that says so is preferred to a full one that guesses.

| Folder | What | Schema |
|---|---|---|
| `providers/` | one pack per country (49 of them: `at.json` ... `vn.json`, named by ISO 3166-1 alpha-2 code) and `international.json` | `schema/providers.schema.json` |
| `devices/` | a device pack (the pinned library commit) and its built index | `schema/devices.schema.json` |
| `serials/` | serial-number rules per vendor, each with its source | `schema/serials.schema.json` |
| `tools/` | `build_device_index.py`, run by hand when the pin moves | |

How to add or correct a pack, what the checks are, and what each source's terms allow: [`docs/packs.md`](../docs/packs.md).
`make test` validates every pack (`python -m rackwalker_proxy.packs` does the same by hand).
