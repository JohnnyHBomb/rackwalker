# World of Confusion: RackWalker

The world is full of confusion: unlabelled racks, unknown serials, cables to nowhere.
You are the RackWalker, and your job is to bring order to it.

RackWalker is a locally hosted, browser-based 8/16-bit open-world game that plays on top of your own
[NetBox](https://github.com/netbox-community/netbox) instance. NetBox is the game's database:
- sites become buildings on a GPS-scaled overworld;
- rooms and racks become isometric levels;
- missing data becomes quests.

Fill a gap in NetBox, verified against the real data, to earn XP, potions and tools.

Status: early development. Not yet playable.

## Planned features
- An overworld you can walk around, with sites placed by GPS. You fly between distant regions from nearby
  airports.
- Enter a building to switch to an isometric room / rack view. You can drag and drop racks and devices,
  and trace cables.
- Quest givers whose quests come from what your NetBox is missing.
- NPCs who quiz you on CLI commands with multiple-choice questions.
- Short, optional survivors-style fights against waves of gremlins, with the occasional boss.
- A character designer, four visual themes, and chiptune music generated in the browser.

Every change to NetBox is shown as a preview before it is applied, and NetBox's changelog is the audit
trail. Game progress is stored by the game, never in NetBox.

## Licence
Apache-2.0. NetBox is a trademark of NetBox Labs; this project is not affiliated with it.
