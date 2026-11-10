# Quests: what the game looks for in your NetBox, and why

This page is for a NetBox admin. It says what RackWalker reads, what it counts as missing, how it turns that into
questions, and what it writes back. The code is `proxy/rackwalker_proxy/estate.py` (the read), `findings.py` (the
rules), `quests.py` (the questions) and `questroutes.py` (the log, the check and the rewards).

A quest is never "fill in field X". The game first looks at the whole estate and works out what you cannot answer
from NetBox today ("how does this site reach the internet?"). It then asks you what you know, a little at a time,
with real names from your own NetBox as the choices. A quest line ends in one change to NetBox that you see before
it is written. It is complete only when NetBox, read again, shows the result: something that merely went away (a
device you deleted) completes nothing and pays nothing.

## A worked example

Say your NetBox has one site, Mill Lane, with two rooms and a rack in each, three switches, and a provider (Northlight
Telecom) with no circuit on record. Nothing is cabled. NetBox looks tidy, and it cannot answer a single question about
how anything connects. The game finds, in this order:

| The game says | It asks | It ends in |
|---|---|---|
| "Nobody can tell from NetBox how Mill Lane reaches the outside world." | Which device does the line to the outside plug into? What is the circuit ID on the bill? What kind of service? Which port? What cable? | a circuit, its end at Mill Lane, a cable; the reward quotes NetBox's own trace from the port to the circuit |
| "Comms Room and Store Room at Mill Lane have no link between them." | Which device in the Store Room has the link? What does it connect to in the Comms Room? Which ports? | a cable; you can trace from one room to the other |
| "ml-sw2 has 24 ports and nothing recorded on any of them." | What is it plugged into? Which port (the fibre cages and the highest-numbered ports are offered first)? | a cable |
| "NetBox does not say how to reach ml-sw1, ml-sw2 and ml-sw3 to manage them." | For each: one of its addresses, a typed one and its interface, "I don't know yet", or "It has none: nobody logs in to it" | each device's primary IP |
| "ml-sw1 and ml-sw2 in rack ML-C1 have no serial number on record." | One form for the rack; blank is "could not read it" | the serials you read |

Every answer you do not have is a field task with a checklist ("Find ml-sw2: rack ML-C1 U20 in Comms Room. Follow the
cable from its uplink port..."). When the lines are done, NetBox can answer what it could not: the trace from a switch
port reaches the carrier's circuit.

## What the game reads

One bounded read of NetBox with the proxy's own token (view permission only):

| Read | Used for |
|---|---|
| sites, locations, racks | where things are; GPS, facility and country of a site; a rack's room and floor position |
| devices with role, device type, platform, status, serial, asset tag, primary IP, rack and unit, virtual chassis | what each box is and where; which switches are one stack |
| interfaces, front and rear ports, power ports, power outlets | what can take a cable, and which of them have one |
| cables with both ends | the connectivity graph and the power graph |
| circuits, providers, circuit types, circuit terminations | the way out of a site, and links between sites |
| IP addresses, prefixes, each with its VRF | which interface has an address, and whether a prefix of the same VRF holds it |
| power feeds, platforms (with the manufacturer a platform is tied to) | choices offered in a question |

It also reads when NetBox last changed each site, rack and device (`last_updated`). A change a quest proposes to one
of them carries that time, so NetBox refuses it if someone has changed the object since the question was asked: a
line about an empty serial never writes over a serial a colleague recorded a minute ago.

Each list is read with a small field selection, page by page, up to a cap (20,000 devices, 200,000 ports, 100,000
cables, 100,000 addresses). The result is kept for 60 seconds (`RACKWALKER_ESTATE_TTL`).

**How often NetBox is read.** The whole estate is read at most once per 60 seconds, and never twice at the same time.
After a quest writes, only the sites that change reached are read again (their devices, ports, cables, racks and
addresses, plus the small estate-wide lists: sites, circuits and their ends, providers, platforms, prefixes) and put
into the kept estate. If something that was at those sites is not in that read (it was deleted, or moved to another
site), the whole estate is read instead. "Check NetBox" on a line reads that line's site, at most every 5 seconds; "Read NetBox again"
in the quest log reads the whole estate, at most every 15 seconds. Asked sooner, the game answers from what it has. A
change made in the room editor drops the kept estate, so the next look reads it whole.

**When the game could not look.** If a list hits its cap, or NetBox refuses it to the proxy's token, the rules that
need it say nothing: the game never calls something missing only because it could not look. The gap list and the
quest log name such lists in `incomplete`. While any list is incomplete, no site is shown above 99 %, no site's
Confusion is ready, nothing is "all in order", and no spot check is offered: the picture is not whole.

**The connectivity graph.** Every interface of a network device belongs to that device, so traffic passes through
it. An *end host* (a server, a wireless access point) is an end, not a way through: two switches that share only a
dual-homed server are not linked. The members of a virtual chassis (a switch stack) are one switch. A patch panel's
front port joins the rear port it maps to. A cable joins its two ends. A circuit joins its terminations. Two devices
are "connected" when a path of these joins them. Power ports, outlets and feeds are a separate graph.

**Which devices count.** Rules about connections, power and addresses look only at devices whose status is
`active` or `staged`. What a device is comes from its role's name and slug, read word by word (so "tor" is a role,
and is not found inside "storage"):

| A device is | when a word of its role is (or, for the longer words, starts with) |
|---|---|
| a *network device* | switch, router, firewall, core, access, distribution, gateway, wan, edge, spine, leaf, tor, network, aggregation, agg |
| a router | router, gateway, wan, edge, firewall |
| *passive* | patch, panel, pdu, power, cable, shelf, blank |
| wireless | ap, aps, wireless, wifi, wlan, or "access" together with "point" (never a network device: nothing uplinks to an access point) |
| an *end host* | wireless, or server, host, storage, hypervisor, compute, workstation, desktop, printer, camera, phone, nas, san |

A role these words do not know ("Kit", "Blue") still makes a network device when the device has eight or more
physical interfaces: a box with a row of ports is a switch, whatever its role is called. So the headline findings do
not depend on how you named your roles.

**A switch stack is one switch.** Its cables, its uplink and its management address are the stack's: a member with
nothing of its own plugged in is not "connected to nothing", a member is not "cut off" from its own stack, and the
stack is asked for one management address (on its first member), not one per member.

## Findings: the whole picture

A finding is something more than one field is needed to see. Each has a stable id
(`<kind>:<object type>:<object id>`), the objects that show it, and a value rank. The rank decides the order in
which quests are offered: **connectivity** (can it be reached, and how) before **identity** (which box is it) before
**layout** (where it stands). Inside a rank the order is the order of this table.

| Kind | Rank | The game reports it when | Why it matters to you | The question it asks | The change it ends in |
|---|---|---|---|---|---|
| `site_no_way_out` | connectivity | a site has network devices, a provider or circuit is on record, and no finished circuit that is cabled to a device can be reached from the site (a circuit with an end recorded at a site and no cable on it is not finished). Not reported while a circuit end is on record at this site with no cable: `circuit_unterminated` asks that, once | nobody can tell how the site reaches the internet | which device the carrier's line plugs into, whose line (asked even with one provider on record), which circuit (or its ID, typed from the bill), which port, what cable. "No line out, on purpose" is an answer | a circuit if it is new, its termination at the site, a cable |
| `sites_not_linked` | connectivity | the estate has two or more sites with network devices, and no path joins this site to any other | nobody can tell how the sites reach each other | which device has the link, what it connects to, both ports, the cable. "No link, on purpose" is an answer | a cable (and a circuit termination, for a circuit) |
| `locations_not_linked` | connectivity | two rooms of one site each hold network devices, and no path joins them | if the link between the rooms fails, nobody knows which cable to check | the same, between the two rooms | a cable |
| `site_island` | connectivity | cabled network devices of a site form two or more groups with no path between them (unless every room of the smaller group is already reported by `locations_not_linked`) | a group of devices is cut off on paper from the rest of the site | what a device of the smaller group connects to in the main group | a cable |
| `switch_no_uplink` | connectivity | a switch has cables, but none of them leads to another network device or a circuit | when the switch goes quiet, nobody can tell what sits above it | what it uplinks to, both ports, the cable | a cable |
| `circuit_unterminated` | connectivity | a circuit has no termination that is cabled to anything, or has an end recorded at a site with no cable on it | a circuit you pay for ends nowhere | which site it comes into, which device, which port | a termination if needed, a cable |
| `provider_unused` | connectivity | a provider has no circuit at all (and no site is waiting for a way out) | a carrier is on record with nothing of theirs recorded | which site their line comes into, then as for `site_no_way_out`. "We no longer use them" is an answer | a circuit, its termination, a cable |
| `device_unconnected` | connectivity | a live device that is not passive has physical interfaces and no cable on any of them | if it drops off the network, nobody can tell which port to check | "What is sw-north-1 (rack N1 U20 in Comms Room) plugged into?" then both ports and the cable | a cable |
| `router_no_address` | connectivity | a router or firewall has no IP address on any interface | NetBox cannot say on which address it routes | how you reach it: an address it already has, or one you type, and its interface | an IP address on the interface, set as primary |
| `interface_no_cable` | connectivity | see "Expected cables" below (one finding per device, for all of its such ports) | ports are marked as in use, with no record of their far ends | for several ports, first what they are: "desk and wall-outlet leads, not kept in NetBox" answers all of them at once. Else, a few ports at a time: what is on the other end, the far port, the cable. "The port is not in use" is an answer per port | a cable per port you knew |
| `device_no_power` | connectivity | devices with power ports have no power cable, while other devices at the site do (one finding per rack) | if a power strip trips, nobody can tell what goes dark | which power strip each device is plugged into, and which outlet | a power cable per device |
| `device_unreachable` | connectivity | live devices that are not passive have no primary IP (one finding per site) | when something breaks, the first question is how to log in | for each device (at most four per quest): one of its addresses, or a typed one and its interface. "It has none: nobody logs in to it" is an answer per device | the primary IP of each device you knew |
| `address_no_prefix` | connectivity | addresses on a site's interfaces lie in no prefix of their own VRF (one finding per implied subnet and VRF; all host addresses, /32 or /128, of one VRF are one finding) | addresses with no prefix cannot be planned around | "their mask says the subnet is 192.0.2.0/24. Is that right?" or the right one, typed. For host addresses the range is asked for, never guessed. For an address in a VRF the game does not write: it says which prefix to add to which VRF, and "Check NetBox" completes the line | a prefix (global table only) |
| `rack_audit` | identity | devices in one rack lack a serial or an asset tag | a device with no serial cannot be matched to a support contract | one form for the rack, top to bottom; leave blank what you cannot read | the fields you filled in, per device |
| `shelf_audit` | identity | the same, for devices that are in no rack (per room) | the same | one form for the devices outside the racks of that room | the fields you filled in |
| `platform_survey` | identity | devices of one model at a site have no platform (one finding per model, at most 20 devices each) | when an advisory or an upgrade comes out, nobody can list what runs it | "4 EX-A2400 devices have no platform on record: ... What do they run?" One answer covers them all; "They do not all run the same" asks about each. Only platforms NetBox allows for the model's manufacturer are offered | the platform of each device |
| `site_record` | identity | a site lacks GPS, a facility name or a country | it cannot be put on a map or named to a carrier | one form: latitude and longitude, facility, country code | the site fields you filled in |
| `rack_devices_unpositioned` | layout | devices are in a rack with no rack unit | nobody can be sent to the right unit | one form: the lowest unit of each device | each device's unit |
| `room_layout` | layout | racks have no room (only at a site that has rooms on record), or no place on the floor plan (`iso_x`, `iso_y`) | someone new cannot find the rack | one form: the room, and the floor tile (or drag the rack in the room view) | each rack's room and floor position |

A finding about a group (an island of switches, a set of addresses) is named after the group's first member and keeps
one check per member. When the first member goes away, the same unfixed problem has a new id; a line you had begun
goes with it, with your answers.

A quest opens with the finding's own sentence, so the first thing a quest giver says is what is missing and where,
with the names NetBox has ("Comms Room and Store Room at Mill Lane have no link between them."), then why it matters.

A site with none of these gets a short, honest "All in order": the game does not invent work. The only thing its
quest giver then offers is a **spot check** of one device at a time ("is sw-quay-1 still in rack PI-Q1 U10, serial
...?"). "Yes" closes it with nothing written and no XP; "no" is a field task: correct the record, then say it matches.
A spot check exists only at a site with nothing open; asked for anywhere else it is refused.

Not in this version: a VLAN check (a prefix with no VLAN behind it), and console ports.

## Gaps: single empty fields

The gap scanner (`GET /api/quests/gaps/<site id>`, paged, in a fixed order) lists single empty fields. A gap's id is
`<object type>:<object id>:<field>`, so it stays the same from one scan to the next.

| Gap id | Listed when |
|---|---|
| `device:<id>:serial` | the device has no serial number and is not passive (patch panels, shelves and blanking plates often carry none) |
| `device:<id>:asset_tag` | the device has no asset tag, **and at least one device in NetBox has one** (if nobody uses asset tags, their absence is not a gap) |
| `device:<id>:platform` | the device has no platform, it is not passive, **and NetBox has at least one platform** |
| `device:<id>:primary_ip` | a live device that is not passive has no primary IP (a stack: when no member has one, asked of its first member) |
| `rack:<id>:location` | the rack is in no location (room), at a site that has locations on record |
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
* it has a description, **or** it carries an IP address and is not a management-only port; **and**
* it has no cable and is not marked as connected (`mark_connected`).

An address on a management-only port does not ask for a cable. It says how the device is reached, not that a lead in
that port goes somewhere NetBox should know: management networks are often left out of NetBox on purpose, and the
line "how do you reach it" has just put that address there. Describe the port ("to the out-of-band switch") and the
game asks where it goes.

At most three such gaps are listed per device and 50 per site in the gap list; the rest are counted (`unlisted`) and
still count against the site's completeness. The quest line of a device covers all of its ports, listed or not, so a
switch with twelve described desk ports is one line with one answer, and the site can still reach 100 %. A device with
no cable at all is the finding `device_unconnected` instead.

## How a quest line asks

* **Real names.** Choices are built from your NetBox, the likeliest first. For an uplink: the same room before other
  rooms; for a switch or a server, switches before routers (the busiest first, where uplinks gather); for a router or
  firewall, and for a link to another site, routers and firewalls first; then circuits that can still land at the
  site; then patch panels in the same rack. For a port, the free ports of the device: fibre cages and fast ports
  before copper access ports, the highest-numbered first, a management port last. For a management address, the
  management port, then VLAN and loopback interfaces, then the physical ports.
* **Nothing in NetBox is out of reach.** Six choices are shown. When there are more, "Another device (site, port,
  interface, ...) that is in NetBox" takes its name, or the start of it; several matches are named back so you can
  type more. This includes devices the short list leaves out on purpose (a server as the far end of a port).
* **Circuits and sites.** For an uplink or the way out, a circuit whose other end is at another of your sites is not
  offered (it is that site's line). For the link between sites those circuits come first and say where their other
  end is, and devices of other sites carry their site's name.
* **"Not in NetBox yet."** The game cannot add a device, a carrier or a platform for you. It remembers what you
  said, tells you what to add in NetBox, and offers it as a choice once it is there. A name NetBox does have is not
  accepted here: that device can be picked.
* **Said before you walk.** What a line needs from NetBox first is said before the question it blocks: with no
  circuit type in NetBox, the way-out line says so before it asks you to read the circuit ID off the bill.
* **"I don't know yet"** is a full answer on every question (in a form: leave the field blank). The line is parked as
  a field task with a short checklist of what to look for on site. It stays in the quest log, costs nothing, does
  not stop other quests, and asks the same question again when you come back.
* **"That is how it is."** Every kind of line has this answer (a switch that stands alone on purpose, a separate
  network, a site with no line out, a device nobody logs in to, a port not in use, kit that carries no label, a rack
  that is on no floor plan), except a router's address: a router routes on some address, and that is found out.
  Nothing is written; the game stops asking, and your completeness counts every check of that line as answered. It
  pays no XP. If the line later gains something it never covered (a new device in the rack), it is asked about that.
* **"Change my last answer."** The preview takes back one answer at a time; the earlier ones stay. "Answer again from
  the start" clears the line.
* **Never a guess.** Every value in the proposed change is one you picked or typed. The label maker's suggested
  asset tag is shown next to the field and stays out of it until you take it. A cable type you are not sure of is
  left empty. The one convention the game applies is the side of a new circuit termination (A, then Z); the preview
  shows it.
* **An answer that no longer fits** (the port got a cable in the meantime) is asked again.

At most three lines are open per site at a time, the most valuable first, and of different kinds where the site has
more than one kind of thing to find out (so a site with twelve unpowered racks does not offer the same quest three
times). Parked lines wait beside them.

## What is written, and who may write

A ready line proposes a change set of ordinary change ops: `connect_cable`, `create_circuit`,
`create_circuit_termination`, `assign_ip`, `create_prefix`, `update_fields`, `place_device`, `move_rack`
(`contracts/api.md`). The game previews it against NetBox, and writes it only when you press Apply.

* **Dry run is the default.** Unless the proxy runs with `WRITE_ENABLED=1`, nothing is written. The line is marked
  "practised", says so, and pays no XP.
* **Your token decides first.** You sign in with one of your own NetBox tokens. If NetBox marks that token read-only
  (`write_enabled` off), your session plays in dry-run and no NetBox write is made for it: a read-only token does
  not gain write rights by passing through the game. The session ends when the token expires or is deleted or
  disabled in NetBox (checked before every write). A legacy v1 token is treated as read-only.
* **Your own NetBox permissions decide.** The proxy writes with its own token, so before every write it reads *your*
  NetBox object permissions from NetBox (your own and those of your groups; fresh for every write, so a permission
  taken away is gone at the next write) and refuses the change unless they cover every
  object type and action it needs: for example `add` on `dcim.cable` for a cable. A permission with constraints does
  not count, because the game cannot evaluate a constraint; make that change in NetBox itself. Without the
  permission the line runs as practice, and you are told before you start, not after your last answer: the quest log
  says what your NetBox user may not write, and the preview of a line says "practice run" when its own change needs
  a permission you lack. The proxy's token never widens what you may
  change. `/api/health` says in `netbox.permission_check` whether the proxy can read users and permissions at all.
* **The changelog names you.** Each object the quest writes carries the message
  `rackwalker (<your username>): quest: <title>`.

## When a line is complete

After the write the proxy reads NetBox again and analyses it again. A line is complete when that analysis makes the
line's checks and they pass. What that means, per kind:

| Line | It is done when a fresh read of NetBox shows |
|---|---|
| the way out of a site | a circuit that is cabled to a device, on a path from one of the site's network devices |
| two sites, two rooms, a cut-off group | a path of cables (through patch panels and circuits) that joins them |
| a device's or a switch's uplink | a cable on the device; for a switch, a path to another network device or a circuit |
| a circuit that ends nowhere, a provider with nothing | an end of the circuit with a cable; a circuit of that provider |
| a router's address | an address on one of its interfaces |
| how to reach the kit | a primary IP on each device (or your "it has none") |
| what powers a rack | a cable on a power port of each device |
| stray addresses | a prefix that holds each address |
| a port in use with no cable | a cable on that port, or `mark_connected` |
| an audit, a platform, a site record | each field you were asked for is no longer empty |
| rack units, the floor plan | a unit on each device; a room and `iso_x`/`iso_y` on each rack |

**Gone is not done.** A check the fresh analysis no longer makes confirms nothing: the device was deleted, switched
to `offline`, or moved; the list could not be read. A line left with only such checks is closed as "no longer
applies" and forgotten, with no XP and no tool (if the device comes back with the same gap, the question is simply
asked again). While a list is unreadable, a line that depends on it stays as it is. A write NetBox did not take
(the object changed or went since the question was asked, or NetBox refused the value) confirms and pays nothing;
the game says NetBox's reason and reads NetBox again before the next question.

An errand is confirmed part by part: what NetBox shows is done and paid, and the line goes on with the rest only.
Your answers are never thrown away by a check: a dry run, a refused write and a "Check NetBox" that finds nothing new
leave the line exactly where it stood. After a cable is written, the reward also quotes NetBox's own cable trace
from the port you named. The game then says what you can do now that you could not do before, for example "You can
now see in NetBox where the line to the outside comes into Harbour Office, and trace it".

A line you had begun and then fixed in NetBox itself completes too: the quest log shows it as ready with nothing to
write, and "Check NetBox" (`POST /api/game/quests/verify`) does the same read without a write.

The game keeps its read of NetBox for a minute. "Read NetBox again" in the quest log reads it at once, for something
you have just changed in NetBox itself.

If something a quest fixed is later removed in NetBox, nothing is reopened and nothing is taken away. The quest log
counts it ("since undone in NetBox"), and the site's completeness shows the truth. Putting it back pays nothing
either: each check pays once per player, however often it is undone and redone, whichever line confirms it, and
however many requests ask at the same moment. A finished errand that gains
something it never covered (a new device with no serial in a rack you audited) is offered again, for the new object.

## XP, tools and potions

* XP comes only from what NetBox confirms: `line XP x share of one pass confirmed x repeat factor`.
  Line XP is 120 for connectivity, 50 for identity, 30 for layout (and 50 for confirming a subnet, which is one
  answer and no walk). A long line is played a few objects at a time (four), and one such pass is worth the line's
  XP: an audit of three labels of which NetBox confirms one pays a third, and recording how to reach four of a
  hundred devices pays the same as four of four. The same kind of fix at the same site pays 100 %, 80 %, 60 %, then
  40 %: no grinding.
* Practice and dry runs pay nothing. Nothing is ever taken away: no penalties, no streaks, no timers.
* **Cable tracer**: yours after the first connectivity line NetBox confirms. Inside a building, tracing a cable
  waits for it, but only while you can earn it: your changes would be written (not a dry-run proxy, not a read-only
  token, no missing permission) and NetBox has a connectivity finding to work on. Everywhere else tracing is open,
  as in the plain viewer.
* **Label maker**: yours after the first identity line NetBox confirms. In an audit it suggests the next free asset
  tag of the site's own pattern (the most common prefix of the site's tags, and the next number).
* A potion comes with each new level (every 250 XP). Potions are for the fights that come later.

## Site completeness and the site's Confusion

Every rule counts a check for each object it looked at. A site's completeness is checks passed out of checks made.
At 100 % a site that has devices has its "Confusion" ready (a site with nothing in it has nothing to guard): a marker
by the building on the overworld and a line in the quest log.
It is optional and only acknowledged in this version; the fight comes later.

## The practice island

A made-up site ("Practice Island": three switches, an internet provider, two rooms with a rack each, nothing
connected) that the proxy serves itself. Ilatea offers it in her first line ("Let me practise first"); it is also
reached from the menu at any time, or by the airfield pad. Nothing on
it is read from or written to NetBox: your practice changes are kept in the game's own store, and the island can be
reset from the quest log. It has a first quest of every kind, and both tools on loan. It pays no XP.
