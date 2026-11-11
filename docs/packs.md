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

There is a pack for 49 countries, and `international` (the backbone names met on a transit bill in any country).
The first sweep made `gb`, `nl`, `us`, `de`, `fr` and `ie`; the second sweep added the rest of the European Union,
the other large economies of Europe and the Americas, and the large economies of Asia and the Pacific
([the list is below](#the-second-sweep)). Each provider has:

| Field | |
|---|---|
| `name` | as the provider trades ("BT", not the registered company name). Where it trades in another script, the name is in that script ("ソフトバンク") and the Latin form is among `aka` |
| `slug` | the NetBox slug it gets |
| `kind` | `national_carrier` (a network operator with fixed and mobile lines across the country), `business_isp`, `cable_fibre`, `mobile`, `wholesale`, `satellite` or `research_education` (a national research and education network). There is no kind for a retail ISP that runs on another operator's network: such a name (Scarlet, Tiscali, Bahnhof) is filed as `business_isp` |
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

What is thin, by design: of the first sweep, the United States and Ireland packs are marked `partial`. The US has
many hundreds of regional telephone companies, co-operatives, municipal networks and wireless ISPs that are not
listed; Ireland's fixed-wireless and smaller business ISPs are thin, and no AS number was confirmed for its wholesale
fibre networks. The table below says the same for each partial pack of the second sweep. "My provider is not listed"
is always an answer: the name is recorded as you type it.

### The second sweep

The second sweep added 43 countries, read on 2026-11-11: the rest of the European Union, Switzerland, Norway and
Türkiye, Canada, Mexico and Brazil, and the large economies of Asia and the Pacific. 21 of them are marked `partial`.

| Pack | Country | Providers | Partial, and why |
|---|---|---|---|
| `at` | Austria | 12 | **partial**: the many regional utility and municipal fibre networks and smaller cable operators are not listed; the regulator's directory was not read |
| `au` | Australia | 13 | no |
| `be` | Belgium | 16 | no |
| `bg` | Bulgaria | 9 | **partial**: several hundred local cable and LAN providers are not listed and business ISPs are thin; the regulator's register is in Bulgarian and was not read; no AS number for Yettel Bulgaria |
| `br` | Brazil | 20 | **partial**: Brazil has thousands of regional ISPs that are not listed; four websites did not answer |
| `ca` | Canada | 16 | no |
| `ch` | Switzerland | 13 | no |
| `cn` | China (mainland) | 6 | **partial**: only the national backbones of the state carriers and the two academic networks: several hundred provincial AS numbers, regional cable and resellers are not listed, and the licence register could not be read |
| `cy` | Cyprus | 6 | **partial**: smaller ISPs are not listed (the register is a search form and was not read), nor are the operators in the north of the island |
| `cz` | Czechia | 11 | no |
| `dk` | Denmark | 17 | no |
| `ee` | Estonia | 9 | **partial**: wholesale networks and business ISPs are thin; the regulator's register was not read; no AS number for Tele2 Eesti |
| `es` | Spain | 19 | no |
| `fi` | Finland | 15 | no |
| `gr` | Greece | 9 | **partial**: fibre newcomers, regional operators and business ISPs are thin; the regulator's register was not found on the pages read; only two names are also given in Greek script |
| `hk` | Hong Kong | 13 | no |
| `hr` | Croatia | 6 | **partial**: cable and fibre operators and business ISPs are thin; the regulator's register is in Croatian and was not read as a list |
| `hu` | Hungary | 9 | no |
| `id` | Indonesia | 13 | **partial**: many hundreds of regional ISPs are not listed; the ministry's site refused an automated read |
| `in` | India | 21 | **partial**: many hundreds of regional cable and fibre operators hold a licence and are not listed; three websites did not answer |
| `it` | Italy | 17 | no |
| `jp` | Japan | 22 | no |
| `kr` | South Korea | 13 | no |
| `lt` | Lithuania | 12 | **partial**: business ISPs and regional operators are thin; the regulator's list was not read; no AS number for Tele2 Lietuva |
| `lu` | Luxembourg | 10 | **partial**: the regulator's list of notified operators was not found on the pages read; the communes' cable networks are not listed |
| `lv` | Latvia | 8 | **partial**: cable, regional and business ISPs are thin; the regulator's register was not read |
| `mt` | Malta | 5 | **partial**: the three network operators and one business ISP: the regulator's register was not found on the pages read |
| `mx` | Mexico | 13 | **partial**: business ISPs and regional cable operators are thin; the state operator's pages did not answer; six websites did not answer |
| `my` | Malaysia | 11 | no |
| `no` | Norway | 9 | no |
| `nz` | New Zealand | 13 | no |
| `ph` | Philippines | 10 | **partial**: provincial cable operators and regional ISPs are not listed; the regulator's site refused an automated read |
| `pl` | Poland | 17 | no |
| `pt` | Portugal | 12 | no |
| `ro` | Romania | 7 | **partial**: several hundred local networks are not listed and business ISPs are thin; the regulator's site failed the secure connection |
| `se` | Sweden | 14 | no |
| `sg` | Singapore | 10 | no |
| `si` | Slovenia | 7 | **partial**: local cable operators, wholesale networks and business ISPs are thin; the regulator's registers are in Slovenian and were not read as a list |
| `sk` | Slovakia | 10 | no |
| `th` | Thailand | 10 | **partial**: names are in Latin script only (no Thai-script name was confirmed against a source that may be copied); the regulator's site refused an automated read and is in Thai |
| `tr` | Türkiye | 11 | **partial**: business ISPs and regional operators are thin; the regulator's list is an application that needs a browser and could not be read |
| `tw` | Taiwan | 11 | no |
| `vn` | Vietnam | 11 | **partial**: the long Vietnamese company names are not given (none was confirmed against a source that may be copied); the regulator's site did not answer |

It was made by the method of the first sweep, with these differences:

* **Which providers.** Only names a site admin in that country would plausibly see on a bill or a hand-off box:
  the national carriers, the mobile network operators, the main cable and fibre operators, the main business ISPs,
  the wholesale networks, the national research network. A pack is not filled up to a number: Malta has five entries
  and Croatia six, because that is what was confirmed.
* **Where information is sparse, the pack is smaller and says so.** A regulator's register that could not be read
  as a list (a search form, an application that needs a browser, a site that refuses automated requests, a language
  the sweep could not check a name in) is recorded as `cited` with the reason, and `missing` says what the pack
  lacks because of it. No regulator's list was copied.
* **AS numbers.** As before, each number was confirmed on the read date against a registry service: the registered
  holder is the named provider (RIPEstat, and for some packs the RIPE Database or APNIC's RDAP record; all read
  only), and the registry that holds it comes from the registries' joint delegated statistics. A number that could
  not be confirmed is left out and named in `missing`. A number that is registered to the provider and was not
  announced on the read date is left out too (CYNET, LuxConnect).
* **Names in another script.** The Japan, South Korea, Taiwan, mainland China and Hong Kong packs give a provider
  the name it trades under at home ("NTT東日本", "LG유플러스", "中華電信"), with the Latin form and former names in `aka`.
  The Greece and Bulgaria packs use the Latin trading name; the Greek or Cyrillic one is in `aka` only for the two
  entries of each pack whose own pages gave it, and `missing` says so. The Thailand and Vietnam packs have Latin
  names only, and say so in `missing`. Every slug is in Latin letters. The search finds
  a provider by any of its names in any script, with or without accents, in full-width or half-width letters.
* **Web addresses.** An address counts as answering only when it gave a page of the provider's own. One that
  answered with a refusal, a bot check or a page-not-found page is left empty like one that did not answer, and
  `missing` names it. The page is read as well as its status: an address that answers can still be another site
  of the same name.
* **Ownership changes** up to the read date are in `aka` and `notes` (One Magyarország, formerly Vodafone Hungary;
  Norlys, formerly Telia Danmark; Telemach Hrvatska, formerly Tele2 Hrvatska; Nio, formerly Oi Fibra). Where the
  owner and the owned network are both entries of a pack (DIGI and NOWO in Portugal, China Mobile Hong Kong and
  HKBN, AIS and 3BB in Thailand), each stays an entry with its own AS numbers and says so in `notes`: the other's
  trading name is not put in `aka`, because a name in `aka` makes the two one provider in NetBox.

**One group, many countries.** A group that trades in several countries (Vodafone, Orange, Telia, Telenor, Tele2,
A1, Deutsche Telekom's subsidiaries, DIGI) has an entry in each country's pack under the name it trades by there
("Vodafone Portugal", "Orange Polska", "Magyar Telekom") with the AS numbers of that country's network; no AS number
is shared between them, and each has a slug of its own (`globalconnect-dk`, `globalconnect-se`). A network that is
the same everywhere (Colt, Starlink, euNetworks, Zayo) is the same entry, with the same slug and the same AS numbers,
in each pack that lists it and in the international pack. The group's backbone is an entry of the international pack
under its own name (Vodafone Carrier Services, Orange International Carriers, Arelion, Deutsche Telekom Global
Carrier). Where a domestic network and the backbone trade under one name (Tata Communications: AS4755 in India,
AS6453 abroad), they are two entries with two slugs and the country's entry says so in `notes`. The validator refuses
an AS number that two packs list under two registries, and a slug that two packs give different AS numbers.

**One name, two networks, one NetBox.** NetBox keeps one provider per name. Where two countries' networks trade
under the very same name ("Telia" in Norway and in Sweden, "Telenet" in Belgium and in Latvia, "GlobalConnect" in
four countries), the first one picked becomes the NetBox provider, with its slug and AS numbers. A later line in the
other country is put on that provider, shown as "(in your NetBox)"; the second network's AS numbers are not added to
it, and the game does not say so. Add them in NetBox if you keep both networks under the one name, or rename the
first provider ("Telia Norge") and pick the second from the search.

### How a provider reaches NetBox

Choosing a provider proposes one op, `create_provider` (name, slug, AS numbers). On apply, the proxy:

1. looks for a provider NetBox already holds with the same name or slug, with one of the AS numbers, or under a name
   the pack says the provider also goes by. If there is one, it is used as it is and **nothing is created**. A
   provider under one of the entry's other names is not taken for it when it is plainly another network: Norlys of
   Denmark also goes by "Telia", and a "Telia" that NetBox holds with Norway's AS numbers is not Norlys. It is
   another network when both carry AS numbers and share none, or when it is another country's pack entry of that
   name. A provider made by hand under the other name, with no AS number, is still taken for the entry;
2. otherwise finds or creates each AS number as an `ipam.ASN` object (NetBox 4.7 requires a registry for an ASN, so
   the registry is found or created as an `ipam.RIR`; one NetBox already holds is found under either of its names,
   "RIPE" or "RIPE NCC", slug `ripe` or `ripe-ncc`), then creates the provider with those ASNs.

In the way-out quest the provider is created in the same previewed change as its circuit (`create_circuit` may name
`"@provider"`), and a NetBox with no circuit type yet gets one the same way (`create_circuit_type`).

### How the game finds a provider among fifty packs

* **The country of a site** is read from the custom field `iso_country`, from the site's region (its slug or name as
  a two-letter code, or its name as a country's name) or from the last line of its address. The game knows the
  English name of every country that has a pack, and names a country uses for itself, in its own script too
  ("Türkiye", "España", "Deutschland", "日本", "대한민국", "Ελλάδα", "България"); accents do not count. Only the site's
  own region is read, not the regions above it: a site in a city region under a country region ("Thailand" >
  "Bangkok") has no country until `iso_country` or the last line of its address names one, and is then offered no
  pack first (the search still finds every provider). The first-site quest asks for the country as a typed name or
  code: with fifty countries there is no short list that suits every player.
* **The choices** of "Whose line is it?" are the providers NetBox holds (up to eight, by name), then names of the
  country's pack that NetBox does not hold: the first six in a NetBox with no provider, fewer as NetBox holds more,
  and never fewer than two. In an estate that already holds many providers of other countries the pack's names come
  last; the search puts the site's country first. The list does not grow with the pack: the rest is reached by the
  search.
* **The search** looks in every pack, by name, by another name, by slug or by AS number, and shows at most six: the
  site's own country first, then the international pack, then other countries with their code ("Telia (NO)").
  An AS number that several packs list is one line. Two countries' networks of one name ("Telenet" in Belgium and in
  Latvia) are two lines. Two or three Latin letters are looked for at the start of a word only; a longer text, and any
  text in a script written without spaces, is looked for anywhere in a name.
* **A typed name** ("My provider is not listed") is looked up in this order: the entry of the site's own country
  with that name; the one entry of that country that also goes by it ("Vodafone" at a British site is Vodafone UK,
  "Oi Fibra" at a Brazilian one is Nio; the provider is recorded under the entry's name, and the preview says so);
  a provider NetBox holds under that name; an entry of the international pack; a name that only one pack lists. A
  name that several countries use for different networks, or that two networks of the site's country go by
  ("MasOrange"), is recorded as typed, with no AS number: pick the entry from the search to say which one it is.

### Adding or correcting a provider pack

1. Copy an existing file to `packs/providers/<code>.json` (the ISO 3166-1 alpha-2 code in lower case), and set
   `country`, `id` (`providers-<code>`), `title` and `version`. If the game does not know the country by name yet,
   add its English name to `NAMES` in `proxy/rackwalker_proxy/country.py` (a test names the country that lacks one).
2. List the providers a site admin in that country meets, most common first: the first six that NetBox does not hold
   yet are offered as choices, the rest are found by search.
3. For every source: read its terms first. Record what they allow in `terms`. Take facts only from a source whose
   terms allow it (`copied`); otherwise mark it `cross-check` or `cited`.
4. Confirm each AS number against the registry, and record the registry. Leave out what you cannot confirm and say
   so in `missing`. A network another pack already lists keeps that pack's slug and AS numbers; a group's network in
   this country gets its own entry under the name it trades by here.
5. Say what the pack is complete for in `coverage`. If the country's information is sparse, set `partial: true`
   and ship the smaller pack: do not fill it with names you could not confirm.
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
* an AS number that several packs list is under the same registry in each, and a slug that several packs use has
  the same AS numbers in each;
* the file name matches the country and the id, and the country is an assigned ISO 3166-1 alpha-2 code;
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
| `create_provider` | a provider with its ASNs (and their registry when missing; an ASN NetBox already holds is used) and, from a pack, what kind of network it is as its description | add provider, add ASN, add RIR |
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
