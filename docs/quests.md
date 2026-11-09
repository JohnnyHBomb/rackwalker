# Quests: what the game looks for in your NetBox, and why

This page is for a NetBox admin. It says what RackWalker reads, what it counts as missing, how it turns that into
questions, and what it writes back. The code is `proxy/rackwalker_proxy/estate.py` (the read), `findings.py` (the
rules), `quests.py` (the questions) and `questroutes.py` (the log, the check and the rewards).

A quest is never "fill in field X". The game first looks at the whole estate and works out what you cannot answer
from NetBox today ("how does this site reach the internet?"). It then asks you what you know, a little at a time,
with real names from your own NetBox as the choices. A quest line ends in one change to NetBox that you see before
it is written. It is complete only when NetBox, read again, shows the result.

## What the game reads

One bounded read of NetBox with the proxy's own token (view permission only):

| Read | Used for |
|---|---|
| sites, locations, racks | where things are; GPS, facility and country of a site; a rack's room and floor position |
| devices with role, device type, platform, status, serial, asset tag, primary IP, rack and unit | what each box is and where |
| interfaces, front and rear ports, power ports, power outlets | what can take a cable, and which of them have one |
| cables with both ends | the connectivity graph and the power graph |
| circuits, providers, circuit types, circuit terminations | the way out of a site, and links between sites |
| IP addresses, prefixes | which interface has an address, and whether a prefix holds it |
| power feeds, platforms | choices offered in a question |

Each list is read with a small field selection, page by page, up to a cap (20,000 devices, 200,000 ports, 100,000
cables, 100,000 addresses). The result is kept for 60 seconds (`RACKWALKER_ESTATE_TTL`) and dropped at once by any
write made through the proxy. If a list hits its cap, or NetBox refuses it to the proxy's token, the rules that need
it say nothing: the game never calls something missing only because it could not look. The gap list and the quest
log name such lists in `incomplete`.

**The connectivity graph.** Every interface belongs to its device. A patch panel's front port joins the rear port
it maps to. A cable joins its two ends. A circuit joins its terminations. Two devices are "connected" when a path of
these joins them. Power ports, outlets and feeds are a separate graph.

**Which devices count.** Rules about connections, power and addresses look only at devices whose status is
`active` or `staged`. A device is a *network device* when its role's name or slug contains switch, router,
firewall, core, access, distribution, gateway, wan, edge, spine or leaf. It is *passive* when the role contains
patch, panel, pdu, power, cable-management, shelf or blank.

## Findings: the whole picture

A finding is something more than one field is needed to see. Each has a stable id
(`<kind>:<object type>:<object id>`), the objects that show it, and a value rank. The rank decides the order in
which quests are offered: **connectivity** (can it be reached, and how) before **identity** (which box is it) before
**layout** (where it stands). Inside a rank the order is the order of this table.

| Kind | Rank | The game reports it when | Why it matters to you | The question it asks | The change it ends in |
|---|---|---|---|---|---|
| `site_no_way_out` | connectivity | a site has network devices, a provider or circuit is on record, and no circuit that is cabled to a device can be reached from the site | nobody can tell how the site reaches the internet | which device the carrier's line plugs into, whose line, which circuit (or its ID, typed from the bill), which port, what cable | a circuit if it is new, its termination at the site, a cable |
| `sites_not_linked` | connectivity | the estate has two or more sites with network devices, and no path joins this site to any other | nobody can tell how the sites reach each other | which device has the link, what it connects to, both ports, the cable. "No link, on purpose" is an answer | a cable (and a circuit termination, for a circuit) |
| `locations_not_linked` | connectivity | two rooms of one site each hold network devices, and no path joins them | if the link between the rooms fails, nobody knows which cable to check | the same, between the two rooms | a cable |
| `site_island` | connectivity | cabled network devices of a site form two or more groups with no path between them (in rooms that the main group also uses) | a group of devices is cut off on paper from the rest of the site | what a device of the smaller group connects to in the main group | a cable |
| `switch_no_uplink` | connectivity | a switch has cables, but none of them leads to another network device or a circuit | when the switch goes quiet, nobody can tell what sits above it | what it uplinks to, both ports, the cable | a cable |
| `circuit_unterminated` | connectivity | a circuit has no termination that is cabled to anything | a circuit you pay for ends nowhere | which site it comes into, which device, which port | a termination if needed, a cable |
| `provider_unused` | connectivity | a provider has no circuit at all (and no site is waiting for a way out) | a carrier is on record with nothing of theirs recorded | which site their line comes into, then as for `site_no_way_out`. "We no longer use them" is an answer | a circuit, its termination, a cable |
| `device_unconnected` | connectivity | a live device that is not passive has physical interfaces and no cable on any of them | if it drops off the network, nobody can tell which port to check | "sw-north-1 has 24 ports and nothing recorded on any of them. What does it uplink to?" then both ports and the cable | a cable |
| `router_no_address` | connectivity | a router or firewall has no IP address on any interface | NetBox cannot say on which address it routes | how you reach it: an address it already has, or one you type, and its interface | an IP address on the interface, set as primary |
| `interface_no_cable` | connectivity | see "Expected cables" below | a port is marked as in use, with no record of its far end | what is on the other end, the far port, the cable. "The port is not in use" is an answer | a cable |
| `device_no_power` | connectivity | devices with power ports have no power cable, while other devices at the site do (one finding per rack) | if a power strip trips, nobody can tell what goes dark | which power strip each device is plugged into, and which outlet | a power cable per device |
| `device_unreachable` | connectivity | live devices that are not passive have no primary IP (one finding per site) | when something breaks, the first question is how to log in | for each device (at most four per quest): one of its addresses, or a typed one and its interface | the primary IP of each device you knew |
| `address_no_prefix` | connectivity | addresses on a site's interfaces lie in no prefix (one finding per implied subnet) | addresses with no prefix cannot be planned around | "their mask says the subnet is 192.0.2.0/24. Is that right?" or the right one, typed | a prefix |
| `rack_audit` | identity | devices in one rack lack a serial, an asset tag or a platform | a device with no serial cannot be matched to a support contract | one form for the rack, top to bottom; leave blank what you cannot read | the fields you filled in, per device |
| `shelf_audit` | identity | the same, for devices that are in no rack (per room) | spare kit with no serial is kit nobody can prove you own | one form for the shelf | the fields you filled in |
| `site_record` | identity | a site lacks GPS, a facility name or a country | it cannot be put on a map or named to a carrier | one form: latitude and longitude, facility, country code | the site fields you filled in |
| `rack_devices_unpositioned` | layout | devices are in a rack with no rack unit | nobody can be sent to the right unit | one form: the lowest unit of each device | each device's unit |
| `room_layout` | layout | racks have no room, or no place on the floor plan (`iso_x`, `iso_y`) | someone new cannot find the rack | one form: the room, and the floor tile (or drag the rack in the room view) | each rack's room and floor position |

A site with none of these gets a short, honest "All in order": the game does not invent work. The only thing its
quest giver then offers is a **spot check** of one device at a time ("is sw-quay-1 still in rack PI-Q1 U10, serial
...?"). "Yes" closes it with nothing written and no XP; "no" is a field task: correct the record, then say it matches.

Not in this version: a VLAN check (a prefix with no VLAN behind it), and console ports.

## Gaps: single empty fields

The gap scanner (`GET /api/quests/gaps/<site id>`, paged, in a fixed order) lists single empty fields. A gap's id is
`<object type>:<object id>:<field>`, so it stays the same from one scan to the next.

| Gap id | Listed when |
|---|---|
| `device:<id>:serial` | the device has no serial number |
| `device:<id>:asset_tag` | the device has no asset tag, **and at least one device in NetBox has one** (if nobody uses asset tags, their absence is not a gap) |
| `device:<id>:platform` | the device has no platform, it is not passive, **and NetBox has at least one platform** |
| `device:<id>:primary_ip` | a live device that is not passive has no primary IP |
| `rack:<id>:location` | the rack is in no location (room) |
| `rack:<id>:iso` | the rack has no `iso_x` or no `iso_y` |
| `interface:<id>:cable` | see "Expected cables" |
| `site:<id>:gps`, `:facility`, `:country` | the site has no latitude or longitude, no facility, no country |

Gaps are never offered one at a time. They are bundled into the errands above: one rack, one shelf, one site, one
room. A quest takes at most four devices or racks, so it stays a few minutes long.

### Expected cables

A switch with 24 ports and three cables does not have 21 gaps: an unused port is normal. An interface is expected to
have a cable only when NetBox already says it is in use:

* it is enabled and physical (not virtual, bridge, LAG or wireless), on a live device that is in a rack and already
  has at least one cable; **and**
* it carries an IP address, **or** it has a description; **and**
* it has no cable and is not marked as connected (`mark_connected`).

At most 50 such gaps are listed per site; the rest are counted (`unlisted`) and still count against the site's
completeness. A device with no cable at all is the finding `device_unconnected` instead.

## How a quest line asks

* **Real names.** Choices are built from your NetBox: for an uplink, the routers and firewalls of the site first,
  then its switches (the busiest first, the same room before other rooms), then circuits that end nowhere yet, then
  patch panels in the same rack. For a port, the free ports of the device: a short list of the likeliest, or you
  type the port's name as printed.
* **"Something not listed."** The game cannot add a device or a carrier for you. It remembers what you said, tells
  you what to add in NetBox, and offers it as a choice once it is there.
* **"I don't know yet"** is a full answer on every question (in a form: leave the field blank). The line is parked as
  a field task with a short checklist of what to look for on site. It stays in the quest log, costs nothing, does
  not stop other quests, and asks the same question again when you come back.
* **"That is how it is."** Where "nothing" can be true (a switch that stands alone on purpose, two sites with no
  link), you can say so. Nothing is written; the game stops asking, and your completeness counts it as answered.
* **Never a guess.** Every value in the proposed change is one you picked or typed. The label maker's suggested
  asset tag is shown next to the field and stays out of it until you take it. A cable type you are not sure of is
  left empty. The one convention the game applies is the side of a new circuit termination (A, then Z); the preview
  shows it.
* **An answer that no longer fits** (the port got a cable in the meantime) is asked again.

At most three lines are open per site at a time, the most valuable first. Parked lines wait beside them.

## What is written, and who may write

A ready line proposes a change set of ordinary change ops: `connect_cable`, `create_circuit`,
`create_circuit_termination`, `assign_ip`, `create_prefix`, `update_fields`, `place_device`, `move_rack`
(`contracts/api.md`). The game previews it against NetBox, and writes it only when you press Apply.

* **Dry run is the default.** Unless the proxy runs with `WRITE_ENABLED=1`, nothing is written. The line is marked
  "practised", says so, and pays no XP.
* **Your own NetBox permissions decide.** The proxy writes with its own token, so before every write it reads *your*
  NetBox object permissions (your own and those of your groups) and refuses the change unless they cover every
  object type and action it needs: for example `add` on `dcim.cable` for a cable. A permission with constraints does
  not count, because the game cannot evaluate a constraint; make that change in NetBox itself. Without the
  permission you get a clear message and the line runs as practice. The proxy's token never widens what you may
  change. `/api/health` says in `netbox.permission_check` whether the proxy can read users and permissions at all.
* **The changelog names you.** Each object the quest writes carries the message
  `rackwalker (<your username>): quest: <title>`.

## When a line is complete

After the write the proxy reads NetBox again and analyses it again. A line is complete when its finding is gone
(for an errand: when its gaps are gone). After a cable is written, the reward also quotes NetBox's own cable trace
from the port you named. The game then says what you can do now that you could not do before, for example "You can
now trace Harbour Office to the carrier's hand-off".

A line you had begun and then fixed in NetBox itself completes too: the quest log shows it as ready with nothing to
write, and "Check NetBox" (`POST /api/game/quests/verify`) does the same read without a write.

The game keeps its read of NetBox for a minute. "Read NetBox again" in the quest log reads it at once, for something
you have just changed in NetBox itself.

If something a quest fixed is later removed in NetBox, nothing is reopened and nothing is taken away. The quest log
counts it ("since undone in NetBox"), and the site's completeness shows the truth.

## XP, tools and potions

* XP comes only from what NetBox confirms: `rank XP x share of the line's checks confirmed x repeat factor`.
  Rank XP is 120 for connectivity, 50 for identity, 30 for layout. An audit with six labels of which NetBox confirms
  two pays a third; the rest pays when it is confirmed. The same kind of fix at the same site pays 100 %, 80 %,
  60 %, then 40 %: no grinding.
* Practice and dry runs pay nothing. Nothing is ever taken away: no penalties, no streaks, no timers.
* **Cable tracer**: yours after the first connectivity line NetBox confirms. Inside a building, tracing a cable
  needs it.
* **Label maker**: yours after the first identity line NetBox confirms. In an audit it suggests the next free asset
  tag of the site's own pattern (the most common prefix of the site's tags, and the next number).
* A potion comes with each new level (every 250 XP). Potions are for the fights that come later.

## Site completeness and the site's Confusion

Every rule counts a check for each object it looked at. A site's completeness is checks passed out of checks made.
At 100 % the site's "Confusion" is ready: a marker by the building on the overworld and a line in the quest log.
It is optional and only acknowledged in this version; the fight comes later.

## The practice island

A made-up site ("Practice Island": three switches, an internet provider, two rooms with a rack each, nothing
connected) that the proxy serves itself. It is reached from the menu at any time, or by the airfield pad. Nothing on
it is read from or written to NetBox: your practice changes are kept in the game's own store, and the island can be
reset from the quest log. It has a first quest of every kind, and both tools on loan. It pays no XP.
