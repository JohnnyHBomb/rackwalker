"""Packs: the data the game ships with to help set a NetBox up (docs/packs.md).

    providers   the common providers of a country, by name, with their AS numbers and public pages
    devices     the device types and module types of the community device-type library at one pinned commit, as a
                compact index; a full definition is fetched only when a player imports it, and checked by SHA-256
    serials     what a vendor's serial number looks like, one sourced rule at a time

Everything here is read from `packs/` (data only) and checked against the schemas in `packs/schema/` when it loads.
`python -m rackwalker_proxy.packs [dir]` validates a pack folder by hand; `make test` and CI do the same.

Nothing in this module reaches the internet except `definition()`, and only when a player previews or applies an
import. With `RACKWALKER_LIBRARY_DIR` (a checkout of the library at the pinned commit) nothing does.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sys
import unicodedata
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import logging

import httpx
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .errors import ApiError

log = logging.getLogger("rackwalker_proxy.packs")

KINDS = {"providers": "providers.schema.json", "devices": "devices.schema.json", "serials": "serials.schema.json"}
KIND_WORDS = {"national_carrier": "national carrier", "business_isp": "business ISP", "cable_fibre": "cable or fibre", "mobile": "mobile",
              "wholesale": "wholesale or backbone", "satellite": "satellite", "research_education": "research and education network"}
RIR_NAMES = {"ripe": ("RIPE NCC", "ripe-ncc"), "arin": ("ARIN", "arin"), "apnic": ("APNIC", "apnic"), "lacnic": ("LACNIC", "lacnic"),
             "afrinic": ("AFRINIC", "afrinic")}
# Words a company name carries that say nothing about which company it is.
COMPANY_WORDS = {"ltd", "limited", "plc", "inc", "llc", "corp", "corporation", "co", "bv", "nv", "gmbh", "ag", "kg", "sa", "sas", "sarl", "srl",
                 "spa", "ab", "as", "oy", "the", "group", "holdings"}
# Words a manufacturer's name carries in one NetBox and not in the next ("Juniper Networks" is Juniper).
MAKER_WORDS = {"networks", "network", "systems", "technologies", "technology", "communications", "computer", "computers", "electronics", "enterprise",
               "international"}
# Longer names of a manufacturer, as compared, and the name the device-type library files it under.
MAKER_ALIASES = {"hewlett packard enterprise": "hpe", "hewlett packard": "hp", "hp enterprise": "hpe", "dell emc": "dell", "dell technologies": "dell",
                 "ubnt": "ubiquiti", "apc by schneider electric": "apc"}
# Manufacturers the library files one product line under either of (an Aruba switch is listed under HPE, and under HP in older NetBoxes).
MAKER_SIBLINGS = ({"hp", "hpe"},)
# A model or maker name that only says "we do not know what this is".
PLACEHOLDER_WORDS = {"unknown", "generic", "placeholder", "tbd", "tbc", "todo", "other", "misc", "none", "na", "unidentified", "unspecified", "dummy",
                     "mystery", "temp", "temporary"}
SERIAL_MAX = 64        # a serial is compared up to this length; no label is longer
UNVERIFIED_TAG = {"name": "Unverified (RackWalker)", "slug": "rw-unverified", "color": "ff9800",
                  "description": "Recorded through RackWalker without a match in its packs: a model the device-type library does not hold, or a serial "
                                 "that does not look like the vendor's."}


def packs_dir() -> Path:
    return Path(os.environ.get("RACKWALKER_PACKS_DIR") or Path(__file__).resolve().parents[2] / "packs")


def _fold(text: str) -> str:
    """Lower case in any script, accents off ("é" is "e", "ß" is "ss")."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(ch))


def norm(name: str) -> str:
    """A name as it is compared: lower case, no punctuation, no company-form words ("BT Ltd." is "bt"). Letters of
    every script are kept: a name in Cyrillic or in kanji compares as itself, never as nothing."""
    words = re.findall(r"(?:[^\W_]|[&+])+", _fold(name).replace(".", ""))
    kept = [w for w in words if w not in COMPANY_WORDS]
    return " ".join(kept or words)


def tight(name: str) -> str:
    """A name with its spacing and its "and" signs taken out: "AT & T" and "AT&T", "Talk Talk" and "TalkTalk"."""
    return "".join(w for w in re.split(r"[ &]+", norm(name)) if w != "and")


def same_name(a: str | None, b: str | None) -> bool:
    """Two names that say the same thing. A name with nothing to compare (empty, only punctuation) is the same as nothing."""
    na, nb = norm(a or ""), norm(b or "")
    if not na or not nb:
        return False
    return na == nb or tight(a or "") == tight(b or "")


def maker_key(name: str | None) -> str:
    """A manufacturer's name as it is compared: "Juniper Networks", "juniper" and "Juniper Inc." are one maker."""
    key = norm(name or "")
    if key in MAKER_ALIASES:
        return MAKER_ALIASES[key]
    words = key.split()
    while len(words) > 1 and words[-1] in MAKER_WORDS:
        words.pop()
    key = " ".join(words)
    return MAKER_ALIASES.get(key, key)


def maker_keys(name: str | None) -> list[str]:
    """The maker's own key first, then the makers the library may file the same kit under."""
    key = maker_key(name)
    return [key] + sorted(k for group in MAKER_SIBLINGS if key in group for k in group if k != key)


def slugify(name: str) -> str:
    """A NetBox slug for a name: every word is kept ("Harbour Wireless Co-op" is harbour-wireless-co-op). A name with
    no Latin letter or digit gets a slug of its own from the name, so two such names never share one."""
    plain = _fold(name)
    slug = re.sub(r"[^a-z0-9]+", "-", plain.replace("&", " and ").replace("+", " plus ")).strip("-")[:100]
    return slug or f"n-{zlib.crc32(name.strip().encode()):08x}"


def is_placeholder(text: str | None) -> bool:
    """A name that stands for "nobody knew": empty, only punctuation ("?"), or made of the words above. A model that is
    only a number (Cisco 1721, Brocade 300) is a real name."""
    words = re.findall(r"[a-z]+", (text or "").lower())
    if not words:
        return not re.search(r"[0-9]", text or "")
    return words[0] in PLACEHOLDER_WORDS or all(w in PLACEHOLDER_WORDS | {"device", "model", "type", "box", "manufacturer", "vendor"} for w in words)


# ---------------------------------------------------------------- validation
def _validators(root: Path) -> dict[str, Draft202012Validator]:
    schemas = {name: json.loads((root / "schema" / name).read_text()) for name in KINDS.values()}
    registry = Registry().with_resources([(name, Resource.from_contents(s)) for name, s in schemas.items()])
    return {kind: Draft202012Validator({"$ref": name}, registry=registry) for kind, name in KINDS.items()}


_PHONE = re.compile(r"(?<![\w./:-])\+?\d[\d ()-]{7,}\d(?![\w./-])")
_DATE = re.compile(r"20\d\d-[01]\d-[0-3]\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in _strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def validate(root: Path | None = None) -> list[str]:
    """Everything wrong with the packs under `root`, one line each ([] = all good). Checks the schema of every file,
    and what a schema cannot say: ids used once, a slug and an AS number once per pack, the file name against the
    country, a thin pack that says what it lacks, no phone number or mail address anywhere in a provider pack, a
    device index that is the file its pack names (size, SHA-256, counts, every row whole), and serial rules that
    compile and carry their source."""
    root = root or packs_dir()
    out: list[str] = []
    try:
        validators = _validators(root)
    except (OSError, ValueError) as err:
        # the folder's path stays in the log: GET /api/packs hands these lines to anyone who asks
        log.error("pack schemas under %s could not be read: %s", root, err)
        return [f"schema: the pack schemas could not be read ({type(err).__name__}); the proxy log names the file"]
    ids: dict[str, str] = {}
    countries: dict[str | None, str] = {}
    found = 0
    for kind in KINDS:
        for path in sorted((root / kind).glob("*.json")):
            rel = f"{kind}/{path.name}"
            found += 1
            try:
                doc = json.loads(path.read_text())
            except ValueError as err:
                out.append(f"{rel}: not JSON ({err})")
                continue
            errs = sorted(validators[kind].iter_errors(doc), key=lambda e: list(e.absolute_path))
            out += [f"{rel}: {'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errs[:20]]
            if errs:
                continue
            if doc["id"] in ids:
                out.append(f"{rel}: id {doc['id']} is already used by {ids[doc['id']]}")
            ids[doc["id"]] = rel
            if doc["partial"] and not doc["missing"]:
                out.append(f"{rel}: a partial pack says what it lacks (`missing` is empty)")
            if kind == "providers":
                out += [f"{rel}: {m}" for m in _check_providers(doc, path, countries)]
            elif kind == "devices":
                out += [f"{rel}: {m}" for m in _check_devices(doc, path)]
            else:
                out += [f"{rel}: {m}" for m in _check_serials(doc)]
    if not found:
        log.error("no packs found under %s", root)
        out.append("no packs found in the pack folder")
    return out


def _check_providers(doc: dict[str, Any], path: Path, countries: dict[str | None, str]) -> list[str]:
    out = []
    want = "international" if doc["country"] is None else doc["country"].lower()
    if path.stem != want or doc["id"] != f"providers-{want}":
        out.append(f"a pack for {doc['country'] or 'no one country'} is the file {want}.json with the id providers-{want}")
    if doc["country"] in countries:
        out.append(f"{countries[doc['country']]} already holds the pack of this country")
    countries[doc["country"]] = path.name
    slugs: set[str] = set()
    names: set[str] = set()
    asns: dict[int, str] = {}
    for p in doc["providers"]:
        if p["slug"] in slugs:
            out.append(f"slug {p['slug']} is used twice")
        if norm(p["name"]) in names:
            out.append(f"the name {p['name']} is used twice")
        slugs.add(p["slug"])
        names.add(norm(p["name"]))
        for a in p["asns"]:
            if a["asn"] in asns:
                out.append(f"AS{a['asn']} is on {asns[a['asn']]} and on {p['name']}: one AS number belongs to one provider of a pack")
            asns[a["asn"]] = p["name"]
    for s in _strings(doc):
        bare = _DATE.sub("", s)
        if _PHONE.search(bare):
            out.append(f"a phone number does not belong in a pack: {s[:60]!r}")
        if _EMAIL.search(s):
            out.append(f"a mail address does not belong in a pack: {s[:60]!r}")
    if not any(s["use"] == "copied" for s in doc["sources"]):
        out.append("no source is marked as the one the facts were taken from (use: copied)")
    return out


def _check_devices(doc: dict[str, Any], path: Path) -> list[str]:
    lib, idx = doc["library"], doc["index"]
    # one place only: the raw files of that repository at that commit, never another repository or a branch
    if lib["raw_base"] != raw_base(lib):
        return ["raw_base does not point at the pinned commit of the library's own repository"]
    file = path.parent / idx["file"]
    if not file.is_file():
        return [f"the index {idx['file']} is missing: run packs/tools/build_device_index.py"]
    raw = file.read_bytes()
    if len(raw) != idx["bytes"] or hashlib.sha256(raw).hexdigest() != idx["sha256"]:
        return [f"the index {idx['file']} is not the file the pack names (size or SHA-256 differs): build it again"]
    try:
        data = json.loads(gzip.decompress(raw))
    except (OSError, ValueError) as err:
        return [f"the index does not read: {err}"]
    out = []
    if data.get("commit") != lib["commit"] or data.get("of") != doc["id"]:
        out.append("the index was built from another commit or for another pack")
    for key, cols, n in (("device_types", "device_columns", idx["device_types"]), ("module_types", "module_columns", idx["module_types"])):
        rows, width = data.get(key) or [], len(data.get(cols) or [])
        if len(rows) != n:
            out.append(f"the index holds {len(rows)} {key}, the pack says {n}")
        bad = [r for r in rows if len(r) != width or not isinstance(r[0], int) or not 0 <= r[0] < len(data["manufacturers"])
               or not re.fullmatch(r"[0-9a-f]{64}", str(r[-1])) or not str(r[1]).strip() or ".." in str(r[-2]) or str(r[-2]).startswith("/")]
        if bad:
            out.append(f"{len(bad)} rows of {key} are not whole, for example {bad[0][:3]}")
    if len(data.get("manufacturers") or []) != idx["manufacturers"]:
        out.append("the count of manufacturers differs from the index")
    return out


def raw_base(lib: dict[str, Any]) -> str:
    """Where the full definitions are fetched from, derived from the repository and the commit a pack names."""
    return f"https://raw.githubusercontent.com/{lib['repo'].removeprefix('https://github.com/')}/{lib['commit']}/"


def _nested_repeat(pattern: str) -> bool:
    """A repeat inside a repeat ("(a+)+"): on a serial that almost fits, such a pattern can take the regex engine
    longer than anyone waits, and the whole proxy waits with it. A serial rule never needs one."""
    from re import _constants as sc
    from re import _parser as sp       # the parser the re module itself uses

    def walk(items: Any, inside: bool) -> bool:
        for op, arg in items:
            if op in (sc.MAX_REPEAT, sc.MIN_REPEAT, sc.POSSESSIVE_REPEAT):
                lo, hi, body = arg
                wide = hi is sc.MAXREPEAT or hi > 1
                if (inside and wide) or walk(body, inside or wide):
                    return True
            elif op is sc.SUBPATTERN:
                if walk(arg[3], inside):
                    return True
            elif op is sc.BRANCH:
                if any(walk(b, inside) for b in arg[1]):
                    return True
            elif op in (sc.ASSERT, sc.ASSERT_NOT):
                if walk(arg[1], inside):
                    return True
        return False
    return walk(sp.parse(pattern), False)


def _check_serials(doc: dict[str, Any]) -> list[str]:
    out = []
    seen: set[str] = set()
    for v in doc["vendors"]:
        if not v["rules"] and not v["no_rule_reason"]:
            out.append(f"{v['vendor']}: a vendor with no rule says why (`no_rule_reason`)")
        for m in v["manufacturers"]:
            if norm(m) in seen:
                out.append(f"manufacturer {m} is listed under two vendors")
            seen.add(norm(m))
        for r in v["rules"]:
            for key in ("pattern", "models"):
                try:
                    re.compile(r[key] or "")
                except re.error as err:
                    out.append(f"{v['vendor']}: rule {r['id']}: {key} is not a regular expression ({err})")
                    continue
                if _nested_repeat(r[key] or ""):
                    out.append(f"{v['vendor']}: rule {r['id']}: {key} repeats a group that itself repeats, which can stall on a long serial: write it without")
    return out


# ---------------------------------------------------------------- loading
@dataclass
class Packs:
    providers: list[dict[str, Any]] = field(default_factory=list)       # provider packs, as their files hold them
    devices: list[dict[str, Any]] = field(default_factory=list)
    serials: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    root: Path | None = None

    # -- providers --
    def countries(self) -> list[str]:
        return sorted(p["country"] for p in self.providers if p["country"])

    def provider_pack(self, country: str | None) -> dict[str, Any] | None:
        return next((p for p in self.providers if p["country"] == country), None)

    def providers_of(self, country: str | None) -> list[dict[str, Any]]:
        """The providers offered at a site of `country`: its own pack, in the pack's order (the names a site admin
        meets first come first). Each entry carries the id of its pack."""
        pack = self.provider_pack(country) if country else None
        return [{**p, "pack": pack["id"]} for p in pack["providers"]] if pack else []

    def all_providers(self) -> list[dict[str, Any]]:
        return [{**p, "pack": pack["id"], "country": pack["country"]} for pack in self.providers for p in pack["providers"]]

    def provider(self, pack_id: str, slug: str) -> dict[str, Any] | None:
        pack = next((p for p in self.providers if p["id"] == pack_id), None)
        entry = next((p for p in pack["providers"] if p["slug"] == slug), None) if pack else None
        return {**entry, "pack": pack["id"], "country": pack["country"]} if entry and pack else None

    def search_providers(self, text: str, country: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
        """Providers of every pack that a typed name could mean: by name, by another name they go by, by slug, or by AS
        number ("AS2856", "2856"). The site's own country first; one entry per provider (a backbone name that several
        packs list is offered once)."""
        t, tt = norm(text), tight(text)
        digits = re.fullmatch(r"(?:as)?\s*(\d{1,10})", text.strip().lower())
        hits: list[tuple[int, int, dict[str, Any]]] = []
        for n, p in enumerate(self.all_providers()):
            names = [norm(p["name"]), *(norm(a) for a in p["aka"]), p["slug"].replace("-", " ")]
            if digits and any(a["asn"] == int(digits.group(1)) for a in p["asns"]):
                score = 0
            elif t and (t in names or tt in {tight(x) for x in names}):
                score = 0           # a whole name, however it is spaced; also a name that is one digit ("3")
            elif len(t) < 2 or digits:
                score = None
            elif any(x.startswith(t) for x in names):
                score = 1
            elif any(t in x for x in names):
                score = 2
            elif all(any(w in x for x in names) for w in t.split()):
                score = 3
            else:
                score = None
            if score is not None:
                hits.append((score + (0 if p["country"] == country else 10 if p["country"] is None else 5), n, p))
        out: list[dict[str, Any]] = []
        for _, _, p in sorted(hits, key=lambda h: h[:2]):
            if not any(same_provider(p, q) for q in out):
                out.append(p)
        return out[:limit]

    # -- devices --
    def device_pack(self) -> dict[str, Any] | None:
        return self.devices[0] if self.devices else None

    def index(self) -> "DeviceIndex":
        return _index(str(self.root or packs_dir()))

    # -- serials --
    def serial_vendor(self, manufacturer: str | None) -> dict[str, Any] | None:
        m = maker_key(manufacturer)
        return next((v for pack in self.serials for v in pack["vendors"] if m and m in {maker_key(x) for x in v["manufacturers"]}), None)

    def serial_check(self, manufacturer: str | None, model: str | None, serial: str) -> dict[str, Any]:
        """How a serial stands against the vendor's sourced rules: `ok` (it fits one), `suspect` (rules apply and it
        fits none: asked about, never refused), or `no_rule` (nothing to check against)."""
        vendor = self.serial_vendor(manufacturer)
        serial = serial.strip()[:SERIAL_MAX]
        rules = [r for r in (vendor["rules"] if vendor else []) if r["models"] is None or re.match(r["models"], model or "", re.I)]
        if not vendor or not rules:
            return {"status": "no_rule", "vendor": vendor["vendor"] if vendor else None, "says": None, "source": None}
        fit = next((r for r in rules if re.fullmatch(r["pattern"], serial, re.I)), None)
        rule = fit or rules[0]
        return {"status": "ok" if fit else "suspect", "vendor": vendor["vendor"], "says": rule["says"], "source": rule["source"]["name"]}


def _asns(p: dict[str, Any]) -> set[int]:
    return {x["asn"] if isinstance(x, dict) else int(x) for x in p.get("asns") or []}


def _same_slug(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return bool(a.get("slug")) and a.get("slug") == b.get("slug")


def same_provider(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two entries (of packs, or one of NetBox: `name`, `slug`, `asns` as numbers or as {asn}) are the same provider
    when their names or slugs are the same, or they share an AS number."""
    return same_name(a["name"], b["name"]) or _same_slug(a, b) or bool(_asns(a) & _asns(b))


def provider_matches(entry: dict[str, Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The providers NetBox already holds that a pack entry (or a typed name) could mean, nearest first: the same name
    or slug; then a name the pack says the provider also goes by; then a shared AS number. One network that NetBox
    holds under two names (two providers with one AS number) gives two answers, the one named like the entry first."""
    aka = [a for a in entry.get("aka") or []]
    out: list[dict[str, Any]] = []
    for test in (lambda x: same_name(x["name"], entry["name"]) or _same_slug(x, entry),
                 lambda x: any(same_name(x["name"], a) for a in aka),
                 lambda x: bool(_asns(entry) & _asns(x))):
        out += [x for x in existing if test(x) and not any(x is o for o in out)]
    return out


def match_provider(entry: dict[str, Any], existing: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The provider NetBox already holds that a pack entry (or a typed name) means, so it is never made twice."""
    return next(iter(provider_matches(entry, existing)), None)


def provider_named(entry: dict[str, Any], existing: list[dict[str, Any]]) -> bool:
    """True when the nearest match is one by name (its own or another it goes by), not only by a shared AS number."""
    hit = match_provider(entry, existing)
    return hit is not None and (same_name(hit["name"], entry["name"]) or _same_slug(hit, entry)
                                or any(same_name(hit["name"], a) for a in entry.get("aka") or []))


@lru_cache(maxsize=4)
def _load(root: str) -> Packs:
    base = Path(root)
    packs = Packs(root=base, errors=validate(base))
    if packs.errors:
        return packs    # a pack folder that does not validate is not half-used: the game says so and offers nothing from it
    for kind in KINDS:
        for path in sorted((base / kind).glob("*.json")):
            getattr(packs, kind).append(json.loads(path.read_text()))
    # the country packs in the order of their codes, the international one last
    packs.providers.sort(key=lambda p: (p["country"] is None, p["country"] or ""))
    return packs


def load() -> Packs:
    return _load(str(packs_dir()))


# ---------------------------------------------------------------- the device index
def _words(text: str) -> str:
    """A model or part number as it is compared: case, spaces and dashes do not count; a plus sign does (a DS218 is
    not a DS218+)."""
    return re.sub(r"[^a-z0-9+]+", " ", text.lower()).strip()


def _tight(text: str | None) -> str:
    return _words(text or "").replace(" ", "")


# A part number of the library that stands for several: a lower-case x where a character varies ("7X0x",
# "825.4524.xx"), a trailing # or *, or alternatives written out ("-NL / -28 / -08").
_WILD_X = re.compile(r"(?:(?<=[A-Z0-9])|(?<=[.\-/ ]))x+(?=$|[.\-/ ])")


def part_pattern(part_number: str | None) -> re.Pattern[str] | None:
    """The pattern a library part number stands for when it is not one number, else None. A pattern is compared
    against, and never offered or written as if it were what a label says."""
    part = (part_number or "").strip()
    if not part or not (_WILD_X.search(part) or re.search(r"[#*?]| / |, | or ", part)):
        return None
    if re.search(r" / |, | or |\?", part):
        return re.compile(r"(?!)")            # alternatives in words: nothing is compared against them
    out, pos = "", 0
    for m in re.finditer(r"x+(?=$|[.\-/ ])|[#*]", part):
        if m.group().startswith("x") and not _WILD_X.match(part, m.start()):
            continue
        out += re.escape(_tight(part[pos:m.start()])) + ("[a-z0-9]" * len(m.group()) if m.group().startswith("x") else "[a-z0-9+]*")
        pos = m.end()
    return re.compile(out + re.escape(_tight(part[pos:])))


# Words on a carton or in a catalogue that are not part of the model's name in the library.
NOISE_WORDS = {"series", "switch", "router", "firewall", "server", "appliance", "chassis", "module", "the", "a", "instant", "on", "poe", "rev", "ver"}
# How a label abbreviates a product line, and how the library writes it.
QUERY_ALIASES = ((re.compile(r"^fgt(?=[ 0-9])"), "fg "), (re.compile(r"^fsw(?=[ 0-9])"), "fs "))


class DeviceIndex:
    """The device types and module types of the pinned library commit, searchable by what is printed on a label."""

    def __init__(self, pack: dict[str, Any] | None, data: dict[str, Any] | None):
        self.pack = pack
        self.manufacturers: list[str] = (data or {}).get("manufacturers", [])
        self.rows: dict[str, list[dict[str, Any]]] = {"device": [], "module": []}
        self._by_model: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self._by_part: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        self._by_file: dict[tuple[str, str], dict[str, Any]] = {}
        for kind, key, cols in (("device", "device_types", "device_columns"), ("module", "module_types", "module_columns")):
            names = (data or {}).get(cols, [])
            for n, r in enumerate((data or {}).get(key, [])):
                row = dict(zip(names, r))
                row.update(kind=kind, n=n, manufacturer=self.manufacturers[row["manufacturer"]])
                row["_m"], row["_model"], row["_part"] = _words(row["manufacturer"]), _words(row["model"]), _words(row["part_number"])
                row["_maker"] = maker_key(row["manufacturer"])
                row["_hay"] = f"{row['_m']} {row['_model']} {row['_part']}"
                row["_tight"] = (row["_model"].replace(" ", ""), row["_part"].replace(" ", ""))
                row["_wild"] = part_pattern(row["part_number"])
                self.rows[kind].append(row)
                self._by_model.setdefault((kind, row["_maker"], row["_model"]), []).append(row)
                self._by_file[(kind, row["file"])] = row
                if row["_tight"][1] and row["_wild"] is None:
                    self._by_part.setdefault((kind, row["_maker"], row["_tight"][1]), []).append(row)

    def __bool__(self) -> bool:
        return bool(self.rows["device"] or self.rows["module"])

    def get(self, kind: str, n: int) -> dict[str, Any] | None:
        rows = self.rows.get(kind, [])
        return rows[n] if 0 <= n < len(rows) else None

    def by_file(self, kind: str, file: str) -> dict[str, Any] | None:
        return self._by_file.get((kind, file))

    def lookup(self, manufacturer: str | None, model: str | None, kind: str = "device") -> dict[str, Any] | None:
        """The library's entry for this maker and model as a NetBox may hold them: the maker under any of its names
        ("Juniper Networks", "Cisco Systems"), the model by its name (case and dashes do not count, a plus sign does)
        or, where NetBox has the part number in the model field ("WS-C2960X-24PS-L"), by that."""
        key = _words(model or "")
        if not key:
            return None
        for maker in maker_keys(manufacturer):
            rows = self._by_model.get((kind, maker, key), [])
            if rows:
                return next((r for r in rows if r["model"].lower() == (model or "").strip().lower()), rows[0])
        for maker in maker_keys(manufacturer):
            rows = self._by_part.get((kind, maker, key.replace(" ", "")), [])
            if len(rows) == 1:
                return rows[0]
        return None

    def same(self, row: dict[str, Any], manufacturer: str | None, model: str | None) -> bool:
        """Is this maker and model of NetBox the library's `row`?"""
        return self.lookup(manufacturer, model, row["kind"]) is row

    def by_part(self, manufacturer: str | None, part_number: str | None, kind: str = "device") -> dict[str, Any] | None:
        """The model of that maker which carries this part number (spaces and dashes do not count), if the library holds one."""
        t = _tight(part_number)
        return next((rows[0] for maker in maker_keys(manufacturer) for rows in [self._by_part.get((kind, maker, t), [])] if t and rows), None)

    def makers(self, text: str) -> list[str]:
        t = maker_key(text)
        return [m for m in self.manufacturers if maker_key(m) == t] or [m for m in self.manufacturers if t and maker_key(m).startswith(t)]

    def search(self, text: str, kind: str = "device", limit: int = 6, manufacturer: str | None = None) -> list[dict[str, Any]]:
        """The entries a typed model, part number or label text could mean, best first: an exact model or part number,
        then one that starts with it, then one with a word that starts with it, then one that holds it, then every word
        found somewhere in maker, model and part number. Spaces and dashes on a label do not count ("EX 2300-24P" finds
        EX2300-24P). When that finds nothing, what a label adds is let go: an order suffix ("JL256A#ABB"), a region or
        revision after the number ("-EU", "V05"), words like "Series" or "switch"."""
        q = _words(text.split("#")[0])
        for pattern, to in QUERY_ALIASES:
            q = _words(pattern.sub(to, q))
        if len(q) < 2:
            return []
        tight, words = q.replace(" ", ""), q.split()
        maker = maker_key(manufacturer) if manufacturer else None
        rows = [r for r in self.rows.get(kind, []) if not maker or r["_maker"] == maker]
        hits: list[tuple[int, int, int]] = []
        for r in rows:
            model, part = r["_tight"]
            if tight in (model, part) or q in (r["_model"], r["_part"]):
                score = 0
            elif model.startswith(tight) or (part and part.startswith(tight)):
                score = 1
            elif len(words) == 1 and any(w.startswith(q) for w in (r["_model"] + " " + r["_part"]).split()):
                score = 2           # "3750" is the Catalyst 3750 before it is the middle of another number
            elif tight in model or (part and tight in part):
                score = 3
            elif all(w in r["_hay"] for w in words):
                # the maker's name with the model ("juniper ex2300"): closer when the rest is the start of the model
                rest = "".join(w for w in words if w not in r["_m"].split())
                score = 4 if rest and (model.startswith(rest) or part.startswith(rest)) else 5
            else:
                continue
            hits.append((score, len(r["model"]), r["n"]))
        if not hits:
            # the label says more than the library's name: the longest model or part number the typed text holds whole
            for r in rows:
                best = max((len(x) for x in r["_tight"] if len(x) >= 5 and x in tight), default=0)
                if best:
                    hits.append((-best, len(r["model"]), r["n"]))
        if not hits:
            kept = [w for w in words if w not in NOISE_WORDS]
            if kept and len(kept) < len(words) and len("".join(kept)) >= 2:
                return self.search(" ".join(kept), kind, limit, manufacturer)
        return [self.rows[kind][n] for _, _, n in sorted(hits)[:limit]]

    def part_check(self, manufacturer: str | None, model: str | None, part_number: str | None, kind: str = "device") -> dict[str, Any]:
        """A part number against the library's for that maker and model:

            verified   the same number, or one the library's pattern stands for
            mismatch   the model is known and the number is the library's part number of ANOTHER model of that maker:
                       a known contradiction (`expected` is this model's number, `other` the model the number belongs to)
            differs    the model is known and the number is not the one the library lists. The library lists one number
                       per model; a real model is sold under several (a region, a licence bundle, a machine type), so
                       this says only that nothing confirms it
            known      the model is known and either side has no part number to compare
            unknown    the library does not hold the model

        `expected` is the library's number when it is one number: a pattern is never handed back as a value."""
        row = self.lookup(manufacturer, model, kind)
        if row is None:
            return {"status": "unknown", "expected": None, "other": None}
        expected = row["part_number"] if row["part_number"] and row["_wild"] is None else None
        given = _tight(part_number)
        if not given or not row["part_number"]:
            return {"status": "known", "expected": expected, "other": None}
        if (row["_wild"].fullmatch(given) if row["_wild"] is not None else given == row["_tight"][1]):
            return {"status": "verified", "expected": expected, "other": None}
        other = self.by_part(row["manufacturer"], part_number, kind)
        if other is not None and other is not row:
            return {"status": "mismatch", "expected": expected, "other": other["model"]}
        return {"status": "differs", "expected": expected, "other": None}

    def label(self, row: dict[str, Any]) -> str:
        if row["kind"] == "module":
            bits = [f"{row['interfaces']} ports"] if row["interfaces"] else []
        else:
            bits = [f"{row['u_height']:g}U"] + ([f"{row['interfaces']} ports"] if row["interfaces"] else [])
        return f"{row['manufacturer']} {row['model']}" + (f" ({', '.join(bits)})" if bits else "")


@lru_cache(maxsize=4)
def _index(root: str) -> DeviceIndex:
    packs = _load(root)
    pack = packs.device_pack()
    if pack is None:
        return DeviceIndex(None, None)
    return DeviceIndex(pack, json.loads(gzip.decompress((Path(root) / "devices" / pack["index"]["file"]).read_bytes())))


# ---------------------------------------------------------------- full definitions (an import)
FOLDER = {"device": "device-types", "module": "module-types"}


def library_dir() -> Path | None:
    d = os.environ.get("RACKWALKER_LIBRARY_DIR")
    return Path(d) if d else None


def fetch_allowed() -> bool:
    return os.environ.get("RACKWALKER_LIBRARY_FETCH", "1") != "0"


class LibraryUnavailable(Exception):
    """A full definition could not be had: no local copy holds it and the pinned commit could not be reached."""


def _checked(raw: bytes, row: dict[str, Any], where: str) -> dict[str, Any]:
    import yaml

    if hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise LibraryUnavailable(f"The definition of {row['manufacturer']} {row['model']} from {where} is not the file the game's index was "
                                 "built from (its SHA-256 differs), so it was not used.")
    doc = yaml.safe_load(raw)
    if not isinstance(doc, dict) or str(doc.get("model")) != row["model"]:
        raise LibraryUnavailable(f"The definition of {row['manufacturer']} {row['model']} does not read as a {row['kind']} type.")
    return doc


# Definitions already read and checked in this process, by their SHA-256: a second preview of the same import reads
# nothing again, from disk or from the internet.
_DEFINITIONS: dict[str, dict[str, Any]] = {}
_DEFINITIONS_MAX = 256


async def definition(row: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    if row["sha256"] in _DEFINITIONS:
        return _DEFINITIONS[row["sha256"]]
    doc = await _definition(row, transport)
    if len(_DEFINITIONS) >= _DEFINITIONS_MAX:
        _DEFINITIONS.pop(next(iter(_DEFINITIONS)))
    _DEFINITIONS[row["sha256"]] = doc
    return doc


async def _definition(row: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The full definition of an index row, from the local copy if one is configured, else from the pinned commit over
    HTTPS; either way it is used only if its SHA-256 is the one in the index. Raises LibraryUnavailable with words for
    the player when it cannot be had (offline): the game plays on, only the import waits."""
    pack = load().device_pack()
    rel = f"{FOLDER[row['kind']]}/{row['file']}"
    local = library_dir()
    if local is not None:
        path = (local / rel).resolve()
        if local.resolve() in path.parents and path.is_file():
            return _checked(path.read_bytes(), row, "the local copy of the library")
    if pack is None or not fetch_allowed():
        raise LibraryUnavailable("Importing a model needs its full definition from the device-type library, and this game has no local copy "
                                 "of it and may not fetch it (RACKWALKER_LIBRARY_DIR is unset or lacks the file; RACKWALKER_LIBRARY_FETCH=0).")
    url = raw_base(pack["library"]) + "/".join(httpx.URL(f"http://x/{part}").raw_path.decode()[1:] for part in rel.split("/"))
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport, follow_redirects=False) as cl:
            r = await cl.get(url)
    except httpx.HTTPError as err:
        raise LibraryUnavailable("The device-type library could not be reached, so the model cannot be imported right now "
                                 f"({type(err).__name__}). The game plays on without it; try the import again when this machine is online, "
                                 "or set RACKWALKER_LIBRARY_DIR to a local copy.") from None
    if r.status_code != 200:
        raise LibraryUnavailable(f"The device-type library answered {r.status_code} for {row['manufacturer']} {row['model']}: the model "
                                 "cannot be imported right now.")
    return _checked(r.content, row, "the pinned commit")


def unavailable(err: LibraryUnavailable) -> ApiError:
    return ApiError("library_unavailable", str(err), None)


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else packs_dir()
    errs = validate(root)
    for e in errs:
        print(e)
    if errs:
        print(f"{root}: {len(errs)} problem{'s' if len(errs) != 1 else ''}")
    if not errs:
        p = _load(str(root))
        n = sum(len(x["providers"]) for x in p.providers)
        print(f"{root}: {len(p.providers)} provider packs ({n} providers), {len(p.devices)} device pack(s), {len(p.serials)} serial rule set(s): all valid")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
