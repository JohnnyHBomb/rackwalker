# Packs: what the game brings to an empty NetBox

RackWalker is a companion for setting NetBox up, not only a game on top of one that is already full. It ships three
kinds of data, called packs, and offers them through play:

| Pack | What it holds | Where it is offered |
|---|---|---|
| **Providers** (`packs/providers/`) | the common providers of a country, by the name they trade under, with their AS numbers and public pages | "Whose line is it?" in the way-out quest, and the carrier stage of setting a site up |
| **Devices** (`packs/devices/`) | an index of every device type and module type of the NetBox community device-type library at one pinned commit | "What is this box?": the first boxes of a site, a device with a placeholder model, a module in an empty bay |
| **Serials** (`packs/serials/`) | what a vendor's serial number looks like, one sourced rule at a time | every question that takes a serial |

A pack is data only. Adding a country is adding one JSON file. Nothing in a pack is written to your NetBox until you
pick it in a quest, see the change in a preview, and apply it. [`docs/quests.md`](quests.md) describes the quests.

`GET /api/packs` (and "The packs this game ships with" under Menu > Device catalogue) lists every pack with its
version, the date it was read, what it is complete for, what it lacks, and its sources.

## Every pack says how far to trust it

Each pack file carries:

| Field | Meaning |
|---|---|
| `id`, `version` | `providers-nl`, `1.0.0`. The version rises with every change to the file |
| `read` | the day the pack was last checked against its sources |
| `coverage` | what it is complete for and what is partial, in a sentence: "Complete for the national fixed-line carriers ...; business ISPs are partial" |
| `partial` | `true` for a thin pack. A partial pack must say what it lacks |
| `missing` | what is not in it |
| `sources` | for each source: its name, URL, the date read, what its licence or terms allow, how it was used, and the result |

A source is used in one of three ways, and the pack says which:

| `use` | Meaning |
|---|---|
| `copied` | facts were taken from it (a name, an AS number, a web address) |
| `cross-check` | it was read to confirm what the pack says; nothing was taken from it |
| `cited` | it is named as the place to look; its terms are unclear or forbid reuse, so nothing was taken from it |

Names are used only as facts. A pack holds no phone number, no mail address, no person and no logo; the validator
refuses a provider pack that contains a phone number or a mail address.

## Provider packs

First sweep: `gb`, `nl`, `us`, `de`, `fr`, `ie`, and `international` (the backbone names met on a transit bill in
any country). Each provider has:

| Field | |
|---|---|
| `name` | as the provider trades ("BT", not the registered company name) |
| `slug` | the NetBox slug it gets |
| `kind` | `national_carrier`, `business_isp`, `cable_fibre`, `mobile`, `wholesale`, `satellite` or `research_education` (a national research and education network) |
| `aka` | other names on a bill or a hand-off box: former names, the business brand, the group. Used for search and to match a provider NetBox already holds |
| `asns` | AS numbers, each with the regional registry that holds it (`ripe`, `arin`, `apnic`, `lacnic`, `afrinic`). NetBox needs the registry to make an ASN object |
| `website`, `support_url` | public pages, or `null`. A status page is listed only where it answered a check on the read date |
| `notes` | one line a site admin would want ("Wholesale only: seldom the name on the bill") |
| `wikidata` | optional: the provider's own Wikidata item (`Q...`), a stable cross-reference for a name that changes. Recorded only where the item states one of the entry's AS numbers; a parent group's item is not used |

How the first sweep was made, on the read date in each file:

* **Which providers.** The national carriers, the mobile network operators, the main cable and fibre operators, the
  main wholesale access networks, the largest business ISPs, and satellite where it is a common backup. The candidate
  names were cross-checked against Wikipedia's pages on each country's providers (CC BY-SA 4.0: facts only, no text
  copied; each pack records how many names were found on the page). The national regulator of each country is cited
  as the authority for operators the pack lacks; no regulator's list was copied, and each pack records what the
  regulator's terms say.
* **AS numbers.** Each number was confirmed in two ways: the registered holder is the named provider (RIPEstat, read
  only: its terms forbid redistribution, so nothing is copied), and the registry that holds it (the registries'
  joint delegated statistics, which are published for public use). A number whose registered holder was another
  company was removed, and `missing` says for which providers no number was confirmed. PeeringDB's acceptable use
  policy forbids reproducing its data outside approved operational use: nothing was taken from it.
* **One network, two names.** An AS number is used once per pack, but two packs may list it under two trading names
  (Sky in the United Kingdom and Sky Ireland). Such an entry says so in its `notes`: in NetBox both resolve to one
  provider, because a provider that holds the AS number is matched before a new one is made.
* **Wikidata ids.** Wikidata is CC0. An id is recorded only after the item was read and found to state one of the
  entry's AS numbers (property P3797).
* **Web addresses.** Each address was requested on the read date. One that did not answer (a site that refuses
  automated requests, a certificate that did not validate) is left empty rather than guessed.

What is thin, by design: the United States and Ireland packs are marked `partial`. The US has many hundreds of
regional telephone companies, co-operatives, municipal networks and wireless ISPs that are not listed; Ireland's
fixed-wireless and smaller business ISPs are thin, and no AS number was confirmed for its wholesale fibre networks.
"My provider is not listed" is always an answer: the name is recorded as you type it.

### How a provider reaches NetBox

Choosing a provider proposes one op, `create_provider` (name, slug, AS numbers). On apply, the proxy:

1. looks for a provider NetBox already holds with the same name or slug, with one of the AS numbers, or under a name
   the pack says the provider also goes by. If there is one, it is used as it is and **nothing is created**;
2. otherwise finds or creates each AS number as an `ipam.ASN` object (NetBox 4.7 requires a registry for an ASN, so
   the registry is found or created as an `ipam.RIR`; one NetBox already holds is found under either of its names,
   "RIPE" or "RIPE NCC", slug `ripe` or `ripe-ncc`), then creates the provider with those ASNs.

In the way-out quest the provider is created in the same previewed change as its circuit (`create_circuit` may name
`"@provider"`), and a NetBox with no circuit type yet gets one the same way (`create_circuit_type`).

### Adding or correcting a provider pack

1. Copy an existing file to `packs/providers/<code>.json` (the ISO 3166-1 alpha-2 code in lower case), and set
   `country`, `id` (`providers-<code>`), `title` and `version`.
2. List the providers a site admin in that country meets, most common first: the first six that NetBox does not hold
   yet are offered as choices, the rest are found by search.
3. For every source: read its terms first. Record what they allow in `terms`. Take facts only from a source whose
   terms allow it (`copied`); otherwise mark it `cross-check` or `cited`.
4. Confirm each AS number against the registry, and record the registry. Leave out what you cannot confirm and say
   so in `missing`.
5. Say what the pack is complete for in `coverage`. If the country's information is sparse, set `partial: true`.
6. Run `make packs-check` (or `make test`).

To correct an entry, change it, raise `version`, and set `read` to the day you checked it.

## The device pack and the device-type library

`packs/devices/community-library.json` pins the
[NetBox community device-type library](https://github.com/netbox-community/devicetype-library) (CC0 1.0, verified
in `LICENSE.txt` at the pinned commit) to one exact commit. It never follows a branch: moving the pin is a change to
this repository that someone makes and reviews.

**What ships, and why.** The library's device and module definitions are about 68 MB of YAML in 16,448 files. The
game ships a compact index instead (`community-library.index.json.gz`, 979,034 bytes): one row per type with the
manufacturer, model, part number, slug, height, the number of interface, console, power, front, rear, module-bay and
device-bay templates, the file it came from, and that file's SHA-256. That is all a search needs, and it adds about
1 MB to the repository instead of 68 MB.

**Where a full definition comes from.** Only when you import a model does the proxy need the whole definition. It
takes it from:

1. a local copy of the library, when `RACKWALKER_LIBRARY_DIR` names one (a checkout at the pinned commit); else
2. the pinned commit over HTTPS (`raw.githubusercontent.com/.../<commit>/device-types/<manufacturer>/<file>`).

Either way the file is used only if its SHA-256 is the one in the index. `RACKWALKER_LIBRARY_FETCH=0` forbids the
fetch.

**Offline.** The game plays offline. Searching the index needs no network. An import does: without a local copy and
without the internet, the preview of an import fails with `library_unavailable` and says why; nothing is written, and
the rest of the game is as it was. `GET /api/packs` says in `library.import_from` whether imports come from `local`,
`https` or `none`.

### What an import writes

`add_device_type` with `library_file` creates the manufacturer if NetBox lacks it, the device type (model, slug, part
number, height, depth, airflow, weight), and its templates: console ports, console server ports, power ports, power
outlets (each tied to its power port), interfaces (type, management-only, PoE), rear ports, front ports with the
library's `port-mappings` (which rear port, and which position of it, each front port leads to, so a cable trace
goes through a patch panel), module bays and device bays. The preview shows how many mappings come with the model. If NetBox refuses a template, the device type is
removed again, so a later import starts clean. A device type NetBox already holds (same manufacturer and model) is
matched and used; it is never created twice. `add_module_type` does the same for a module type. A module the
library does not hold can be recorded by hand (manufacturer, model, part number), like a device type: it gets no
templates and is marked unverified.

An interface that is a member of a bridge keeps that membership, and the type keeps the library's comments. A
manufacturer NetBox already holds under a longer name ("Juniper Networks") is the one the type goes under. A part
number of the library that stands for several ("7X0x") is left empty. Inventory-item templates, images and rack types
of the library, and the profile and attributes of a module type, are not imported.

### Rebuilding the index

```sh
git clone https://github.com/netbox-community/devicetype-library /somewhere/devicetype-library
git -C /somewhere/devicetype-library checkout <the commit to pin>
# set library.commit, library.commit_date and library.raw_base in packs/devices/community-library.json, then:
.venv/bin/python packs/tools/build_device_index.py packs/devices/community-library.json /somewhere/devicetype-library
make test
```

The script refuses a checkout that is not at the pinned commit, writes the index byte for byte reproducibly, and
updates the pack's `index` block (size, SHA-256, counts). The validator then checks that the index is the file the
pack names. Copy the few files under `proxy/tests/library/` again from the new commit, so the offline tests still
match the index.

A smaller device pack is the same file with `manufacturers` set to a list of manufacturer names instead of `"*"`.

## Truthfulness: part numbers, serials, and "unverified"

The game does not take a value just because it was typed.

**Part numbers.** A part number is checked against the index for that manufacturer and model. The library lists one
part number per model, and a real model is sold under several (a region, a licence bundle, the machine type of a
family), so only a known contradiction is refused:

| The index says | Then |
|---|---|
| the same part number, or it lists a pattern that the number fits ("7X0x" for 7X06) | the model is **verified** |
| that number is the part number of **another** model of the maker | **refused**, naming both: "The device-type library knows EX2300-48P as the part number of the Juniper EX2300-48P, not of the EX2300-24P." This applies to a part number you type, and to a device type already in NetBox that a quest would build on |
| another number for that model, which it lists for no model | **left as it is**: the model is confirmed, the number is neither confirmed nor called wrong, and the catalogue says so. A device is built on such a type without a word |
| it does not hold the model | **accepted**, recorded by hand, and marked unverified (unless the part number you typed is that of a model it does hold: then that is the model) |

A manufacturer is the library's under any of its usual names ("Juniper Networks", "Cisco Systems", "Dell Inc."; an
Aruba switch under "HP" or "HPE"), and a device type that carries the part number in its model field
("WS-C2960X-24PS-L") is the library's model of that part number. Such a type is used as it stands, never made a second
time, and the catalogue says what the library calls it. Two models that differ by a plus sign ("DS218" and "DS218+")
are two models.

A library part number that stands for several ("7X0x", "825.4524.xx", a trailing "#") is compared against as a
pattern. It is never offered as what a label says, and never written to NetBox: an import of such a model leaves the
part number empty.

**A part number NetBox already holds.** Where a device type in use is a model the library holds and NetBox has no
part number for it, or has the part number of another model, the line "The part number of ..." sends you to the label
of one box of that model. If the label carries the library's number, `set_part_number` writes it (in the library's
spelling) and the model becomes verified. If it carries something else, nothing is written: the line says which model
that number belongs to when the library knows (then the device's type is what is wrong), and otherwise where to
record it yourself. "Leave it" closes the line with no XP.

**A model NetBox holds with no port on it.** A device type made by hand, with no interface, console, power or bay
template at all, would give a new box no ports. When such a type is the library's model, the box's own change set
fills it first: `add_device_type` adds the library's templates to the type NetBox holds (the preview says "NetBox
holds the device type ... with no ports: they are added from the device-type library"). Nothing the type has is
changed, a type with any template of its own is left exactly as it is, and devices made before keep what they have.

**Serial numbers.** `packs/serials/network-vendors.json` lists the eight network vendors with the most device types
in the library, and APC for its UPS units. A rule is written only when a public page of the vendor's own states it; the rule quotes that page.

| Vendor | Rule | Source |
|---|---|---|
| Cisco (not Meraki, which the library files under Cisco and which has serials of its own) | three characters for the place it was made, two digits for the year, two digits for the week (01 to 53), then four characters | Cisco Field Notice FN63942, which gives the pattern `LLLYYWWSSSS` |
| Cisco | the same with five characters at the end | Cisco's product-recycling page, which gives the pattern `FFFYYWWSSSSS` |
| Dell (PowerEdge, OptiPlex, Latitude, Precision only) | a Service Tag of seven letters and digits | Dell knowledge base article 000196860. Storage and switches carry other serials, so the rule is not applied to them |
| Fortinet (FortiGate only) | a FortiGate serial starts with `FG` | Fortinet Community technical tip on product abbreviations, whose example is a FortiGate. No rule for FortiSwitch: no page states one |
| APC (UPS models only) | two characters, then two digits for the year and two for the week the unit was made, then the rest | Schneider Electric's page on APC UPS factory warranty. The page speaks of UPS units, so the rule is not applied to APC's power strips |
| HPE (Aruba), Juniper, Arista, Extreme Networks, Ubiquiti | none | no page of the vendor's own that states a format was found (each entry says what was looked at) |

The rules fail soft:

| The serial | Then |
|---|---|
| is the serial of another device in service | **refused**: a serial number belongs to one device. A record that has left service (decommissioning, inventory, offline, failed) does not hold its serial for ever: the box that is put back to use may carry it |
| fits a rule | taken, and it earns the bonus |
| breaks every rule that applies | the game asks once: "Are you sure? 12345 does not look like a Cisco serial: ..." You can confirm it (it is recorded, and the device is marked unverified) or leave it out |
| belongs to a vendor with no rule | taken as typed; no bonus, and nothing is marked |

**The mark.** Unverified things carry the NetBox tag `rw-unverified` ("Unverified (RackWalker)"): a device type
recorded by hand, and a device whose serial you confirmed although it breaks its vendor's rule. A tag was chosen over
a custom field because the game can create it on first use, in a NetBox that has no custom field of ours yet, and
because NetBox can filter any list by it. Remove the tag in NetBox when you have checked the thing yourself.

**No XP bonus for the unverified.** A quest line pays its own XP when NetBox confirms the change. On top of that,
values that pass a check earn a bonus, decided from NetBox as it reads after the write: 20 XP for each box of a model
the library confirms, 10 XP for each serial that fits its vendor's rule. Anything marked unverified earns no bonus,
and the reward says so: a box whose serial you confirmed against its vendor's rule earns none for its model either.
Each apply pays for what that apply confirmed, never for a line's earlier passes.

## Changing the model of an existing device

"What is this box?" on a device with a placeholder model ends in `set_device_type`. NetBox allows a device's type to
be changed, but it neither removes nor adds ports when that happens, so the game allows it only where nothing
recorded is lost:

| Case | Outcome |
|---|---|
| the device has no ports or bays at all | **allowed**: the type is changed and every component of the model is added |
| every component the device has exists in the model under the same name and kind | **allowed**: the type is changed, the components the device lacks are added, and nothing it has is touched (cables, addresses and modules stay) |
| a component of the device is not in the model | **refused**, naming up to four and saying which carry a cable or a module: "qh-box has 1 component that Juniper EX2300-24P does not have: interface Gi0/49 (cabled)." |
| the new model is taller and the units above the device are taken, or it would stick out of the rack | **refused**, naming the unit and the device in it |

The check is made against NetBox when the change is previewed and again when it is applied, not against what the
game last read. A refusal writes nothing.

## The validator

`make packs-check` (part of `make test`, which CI runs) validates every file under `packs/` without touching the
network. It checks each file against its JSON Schema in `packs/schema/`, and what a schema cannot say:

* an id is used once; a slug, a name and an AS number are used once per provider pack;
* the file name matches the country and the id;
* a partial pack says what it lacks; one source is marked as the one the facts came from;
* no phone number and no mail address anywhere in a provider pack;
* the device index is the file the pack names (size and SHA-256), was built from the pinned commit, holds the number
  of rows the pack says, and every row is whole (no file name leaves the library's folder);
* `raw_base` is the raw-file address of the library's own repository at the pinned commit, and nothing else;
* every serial rule compiles (its `models` expression too) and quotes its source; a vendor with no rule says why; no
  rule repeats a group that itself repeats (such a pattern can take the regex engine minutes on a serial that almost
  fits, and every player waits with it).

A pack folder that does not validate is not half-used: the game offers nothing from it, and `GET /api/packs` lists
the errors (in plain words: where the pack folder is on the machine stays in the proxy's log).

An import is bounded: a definition is read once and kept by its SHA-256 while the proxy runs, and one change set
imports at most eight models from the library.

## The change ops behind it (contract 2.5.0)

| Op | Creates or changes | Needs of the player's NetBox user |
|---|---|---|
| `create_site` | a site, and the region of its country when missing | add site, add region |
| `create_provider` | a provider with its ASNs (and their registry when missing) | add provider, add ASN, add RIR |
| `create_circuit_type` | a circuit type | add circuit type |
| `add_device_type` | a device type with its manufacturer and templates | add device type, manufacturer and each template kind (by hand: also add tag) |
| `add_module_type` | a module type with its templates | add module type, manufacturer and each template kind (by hand: also add tag) |
| `create_device` | a device, and its role when missing | add device, add device role |
| `set_device_type` | a device's type, and the components it lacks | change device, add each component kind |
| `install_module` | a module in an empty bay | add module |
| `set_part_number` | a device type's part number, to the library's | change device type |
| `mark_unverified` | the tag on a device or device type | add tag, change the object |

As with every op, the proxy checks the player's own NetBox permissions before it writes, and a write-disabled proxy
or a read-only token plays the same lines as practice.
