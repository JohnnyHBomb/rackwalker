# Packs

Data the game ships with to help set a NetBox up: the common providers of a country, the device types and module types
of the community device-type library, and what a vendor's serial number looks like. Data only: a new country is one
JSON file in `providers/`, and nothing else.

| Folder | What | Schema |
|---|---|---|
| `providers/` | one pack per country (`gb.json`, ...) and `international.json` | `schema/providers.schema.json` |
| `devices/` | a device pack (the pinned library commit) and its built index | `schema/devices.schema.json` |
| `serials/` | serial-number rules per vendor, each with its source | `schema/serials.schema.json` |
| `tools/` | `build_device_index.py`, run by hand when the pin moves | |

How to add or correct a pack, what the checks are, and what each source's terms allow: [`docs/packs.md`](../docs/packs.md).
`make test` validates every pack (`python -m rackwalker_proxy.packs` does the same by hand).
