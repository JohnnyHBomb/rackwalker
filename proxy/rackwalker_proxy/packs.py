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
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .errors import ApiError

KINDS = {"providers": "providers.schema.json", "devices": "devices.schema.json", "serials": "serials.schema.json"}
KIND_WORDS = {"national_carrier": "national carrier", "business_isp": "business ISP", "cable_fibre": "cable or fibre", "mobile": "mobile",
              "wholesale": "wholesale or backbone", "satellite": "satellite", "research_education": "research and education network"}
RIR_NAMES = {"ripe": ("RIPE NCC", "ripe-ncc"), "arin": ("ARIN", "arin"), "apnic": ("APNIC", "apnic"), "lacnic": ("LACNIC", "lacnic"),
             "afrinic": ("AFRINIC", "afrinic")}
# Words a company name carries that say nothing about which company it is.
COMPANY_WORDS = {"ltd", "limited", "plc", "inc", "llc", "corp", "corporation", "co", "bv", "nv", "gmbh", "ag", "kg", "sa", "sas", "sarl", "srl",
                 "spa", "ab", "as", "oy", "the", "group", "holdings"}
# A model or maker name that only says "we do not know what this is".
PLACEHOLDER_WORDS = {"unknown", "generic", "placeholder", "tbd", "tbc", "todo", "other", "misc", "none", "na", "unidentified", "unspecified", "dummy",
                     "mystery", "temp", "temporary"}
UNVERIFIED_TAG = {"name": "Unverified (RackWalker)", "slug": "rw-unverified", "color": "ff9800",
                  "description": "Recorded through RackWalker without a match in its packs: a model the device-type library does not hold, or a serial "
                                 "that does not look like the vendor's."}


def packs_dir() -> Path:
    return Path(os.environ.get("RACKWALKER_PACKS_DIR") or Path(__file__).resolve().parents[2] / "packs")


def norm(name: str) -> str:
    """A name as it is compared: lower case, no punctuation, no company-form words ("BT Ltd." is "bt")."""
    words = re.findall(r"[a-z0-9&+]+", name.lower().replace(".", "").replace("ü", "u").replace("ö", "o").replace("ä", "a").replace("é", "e").replace("ó", "o").replace("ÿ", "y"))
    kept = [w for w in words if w not in COMPANY_WORDS]
    return " ".join(kept or words)


def slugify(name: str) -> str:
    """A NetBox slug for a name: every word is kept ("Harbour Wireless Co-op" is harbour-wireless-co-op)."""
    plain = name.lower().replace("ü", "u").replace("ö", "o").replace("ä", "a").replace("é", "e").replace("ó", "o").replace("ÿ", "y").replace("ß", "ss")
    return re.sub(r"[^a-z0-9]+", "-", plain.replace("&", " and ").replace("+", " plus ")).strip("-")[:100] or "x"


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
        return [f"schema: {err}"]
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
        out.append(f"{root}: no packs found")
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
    if not lib["raw_base"].endswith(f"/{lib['commit']}/"):
        return ["raw_base does not point at the pinned commit"]
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
        t = norm(text)
        digits = re.fullmatch(r"(?:as)?\s*(\d{1,10})", text.strip().lower())
        hits: list[tuple[int, int, dict[str, Any]]] = []
        for n, p in enumerate(self.all_providers()):
            names = [norm(p["name"]), *(norm(a) for a in p["aka"]), p["slug"].replace("-", " ")]
            if digits:
                score = 0 if any(a["asn"] == int(digits.group(1)) for a in p["asns"]) else None
            elif len(t) < 2:
                score = None
            elif t in names:
                score = 0
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
        m = norm(manufacturer or "")
        return next((v for pack in self.serials for v in pack["vendors"] if m and m in {norm(x) for x in v["manufacturers"]}), None)

    def serial_check(self, manufacturer: str | None, model: str | None, serial: str) -> dict[str, Any]:
        """How a serial stands against the vendor's sourced rules: `ok` (it fits one), `suspect` (rules apply and it
        fits none: asked about, never refused), or `no_rule` (nothing to check against)."""
        vendor = self.serial_vendor(manufacturer)
        rules = [r for r in (vendor["rules"] if vendor else []) if r["models"] is None or re.match(r["models"], model or "", re.I)]
        if not vendor or not rules:
            return {"status": "no_rule", "vendor": vendor["vendor"] if vendor else None, "says": None, "source": None}
        fit = next((r for r in rules if re.fullmatch(r["pattern"], serial.strip(), re.I)), None)
        rule = fit or rules[0]
        return {"status": "ok" if fit else "suspect", "vendor": vendor["vendor"], "says": rule["says"], "source": rule["source"]["name"]}


def same_provider(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two entries (of packs, or one of NetBox: `name`, `slug`, `asns` as numbers or as {asn}) are the same provider
    when their names or slugs are the same, or they share an AS number."""
    def asns(p: dict[str, Any]) -> set[int]:
        return {x["asn"] if isinstance(x, dict) else int(x) for x in p.get("asns") or []}
    if norm(a["name"]) == norm(b["name"]) or (a.get("slug") and a.get("slug") == b.get("slug")):
        return True
    return bool(asns(a) & asns(b))


def match_provider(entry: dict[str, Any], existing: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The provider NetBox already holds that a pack entry (or a typed name) means, so it is never made twice: the
    same name or slug first, then a shared AS number, then a name the pack says the provider also goes by."""
    for test in (lambda x: norm(x["name"]) == norm(entry["name"]) or (x.get("slug") and x.get("slug") == entry.get("slug")),
                 lambda x: same_provider(entry, x),
                 lambda x: norm(x["name"]) in {norm(a) for a in entry.get("aka") or []}):
        hit = next((x for x in existing if test(x)), None)
        if hit is not None:
            return hit
    return None


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
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class DeviceIndex:
    """The device types and module types of the pinned library commit, searchable by what is printed on a label."""

    def __init__(self, pack: dict[str, Any] | None, data: dict[str, Any] | None):
        self.pack = pack
        self.manufacturers: list[str] = (data or {}).get("manufacturers", [])
        self.rows: dict[str, list[dict[str, Any]]] = {"device": [], "module": []}
        for kind, key, cols in (("device", "device_types", "device_columns"), ("module", "module_types", "module_columns")):
            names = (data or {}).get(cols, [])
            for n, r in enumerate((data or {}).get(key, [])):
                row = dict(zip(names, r))
                row.update(kind=kind, n=n, manufacturer=self.manufacturers[row["manufacturer"]])
                row["_m"], row["_model"], row["_part"] = _words(row["manufacturer"]), _words(row["model"]), _words(row["part_number"])
                row["_hay"] = f"{row['_m']} {row['_model']} {row['_part']}"
                row["_tight"] = (row["_model"].replace(" ", ""), row["_part"].replace(" ", ""))
                self.rows[kind].append(row)
        self._by_model = {(k, norm(r["manufacturer"]), r["_model"]): r for k, rows in self.rows.items() for r in rows}

    def __bool__(self) -> bool:
        return bool(self.rows["device"] or self.rows["module"])

    def get(self, kind: str, n: int) -> dict[str, Any] | None:
        rows = self.rows.get(kind, [])
        return rows[n] if 0 <= n < len(rows) else None

    def by_file(self, kind: str, file: str) -> dict[str, Any] | None:
        return next((r for r in self.rows.get(kind, []) if r["file"] == file), None)

    def lookup(self, manufacturer: str | None, model: str | None, kind: str = "device") -> dict[str, Any] | None:
        """The library's entry for exactly this maker and model (as compared: case and punctuation do not count)."""
        return self._by_model.get((kind, norm(manufacturer or ""), _words(model or "")))

    def by_part(self, manufacturer: str | None, part_number: str | None, kind: str = "device") -> dict[str, Any] | None:
        """The model of that maker which carries this part number (spaces and dashes do not count), if the library holds one."""
        maker, tight = norm(manufacturer or ""), _words(part_number or "").replace(" ", "")
        return next((r for r in self.rows.get(kind, []) if tight and r["_tight"][1] == tight and norm(r["manufacturer"]) == maker), None)

    def makers(self, text: str) -> list[str]:
        t = norm(text)
        return [m for m in self.manufacturers if norm(m) == t] or [m for m in self.manufacturers if t and norm(m).startswith(t)]

    def search(self, text: str, kind: str = "device", limit: int = 6, manufacturer: str | None = None) -> list[dict[str, Any]]:
        """The entries a typed model, part number or label text could mean, best first: an exact model or part number,
        then one that starts with it, then one that holds it, then every word found somewhere in maker, model and part
        number. Spaces and dashes on a label do not count ("EX 2300-24P" finds EX2300-24P)."""
        q = _words(text)
        if len(q) < 2:
            return []
        tight, words = q.replace(" ", ""), q.split()
        maker = norm(manufacturer) if manufacturer else None
        hits: list[tuple[int, int, int]] = []
        for r in self.rows.get(kind, []):
            if maker and norm(r["manufacturer"]) != maker:
                continue
            model, part = r["_tight"]
            if tight in (model, part) or q in (r["_model"], r["_part"]):
                score = 0
            elif model.startswith(tight) or (part and part.startswith(tight)):
                score = 1
            elif tight in model or (part and tight in part):
                score = 2
            elif all(w in r["_hay"] for w in words):
                # the maker's name with the model ("juniper ex2300"): closer when the rest is the start of the model
                rest = "".join(w for w in words if w not in r["_m"].split())
                score = 3 if rest and (model.startswith(rest) or part.startswith(rest)) else 4
            else:
                continue
            hits.append((score, len(r["model"]), r["n"]))
        return [self.rows[kind][n] for _, _, n in sorted(hits)[:limit]]

    def part_check(self, manufacturer: str | None, model: str | None, part_number: str | None, kind: str = "device") -> dict[str, Any]:
        """A part number against the library's for that maker and model: `verified` (the same), `mismatch` (the model
        is known and the library holds another: `expected` says which), `known` (the model is known and either side
        has no part number to compare) or `unknown` (the library does not hold the model)."""
        row = self.lookup(manufacturer, model, kind)
        if row is None:
            return {"status": "unknown", "expected": None}
        given = (part_number or "").strip()
        if not given or not row["part_number"]:
            return {"status": "known", "expected": row["part_number"] or None}
        same = _words(given).replace(" ", "") == row["_tight"][1]
        return {"status": "verified" if same else "mismatch", "expected": row["part_number"]}

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


async def definition(row: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
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
    url = pack["library"]["raw_base"] + "/".join(httpx.URL(f"http://x/{part}").raw_path.decode()[1:] for part in rel.split("/"))
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
    if not errs:
        p = _load(str(root))
        n = sum(len(x["providers"]) for x in p.providers)
        print(f"{root}: {len(p.providers)} provider packs ({n} providers), {len(p.devices)} device pack(s), {len(p.serials)} serial rule set(s): all valid")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
