# A few files of the device-type library, for the offline tests

Copied unchanged from [netbox-community/devicetype-library](https://github.com/netbox-community/devicetype-library)
(CC0 1.0) at the commit `packs/devices/community-library.json` pins, so each file's SHA-256 is the one in the game's
index. The tests point `RACKWALKER_LIBRARY_DIR` here and forbid fetching, so an import in a test never reaches the
internet. A model that is not in this folder is "not available offline" in a test, which is itself a case.
