"""A site's country (ISO 3166-1 alpha-2), for the game's landmark and villager sets.

NetBox has no country field on a site. The proxy reads it, in this order:
  1. the custom field `iso_country` on the site, when it holds a two-letter code;
  2. the site's region: its slug or its name when that is a two-letter code (`gb`, `NL`), or its name when it is a
     country name this table knows (`United Kingdom`, `Netherlands`);
  3. the last line of the site's physical address, read the same way (`... Utrecht, Netherlands`).
Anything else gives no country (null), and the game shows its neutral set. Only the site's own region is read, not
its parents: give the country region to the site, or name the country on the address's last line.
"""
from __future__ import annotations

import re

# Two-letter codes that are not countries.
NOT_COUNTRIES = {"EU", "UN", "XX", "ZZ"}

# Country names (and common short or older names) -> code. Lower case, punctuation stripped.
NAMES: dict[str, str] = {
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB", "united kingdom of great britain and northern ireland": "GB",
    "netherlands": "NL", "the netherlands": "NL", "holland": "NL", "nederland": "NL",
    "ireland": "IE", "france": "FR", "germany": "DE", "deutschland": "DE", "belgium": "BE", "luxembourg": "LU",
    "spain": "ES", "portugal": "PT", "italy": "IT", "switzerland": "CH", "austria": "AT", "denmark": "DK",
    "norway": "NO", "sweden": "SE", "finland": "FI", "iceland": "IS", "poland": "PL", "czechia": "CZ",
    "czech republic": "CZ", "greece": "GR", "united states": "US", "united states of america": "US", "usa": "US",
    "canada": "CA", "mexico": "MX", "brazil": "BR", "argentina": "AR", "japan": "JP", "china": "CN", "india": "IN",
    "australia": "AU", "new zealand": "NZ", "south africa": "ZA", "singapore": "SG", "south korea": "KR",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def code_of(text: str | None) -> str | None:
    """A country code from a region slug or name or an address line, or None."""
    if not text:
        return None
    t = text.strip()
    if _norm(t) in NAMES:  # first, so "UK" reads as GB rather than as a code
        return NAMES[_norm(t)]
    if re.fullmatch(r"[A-Za-z]{2}", t):
        c = t.upper()
        return None if c in NOT_COUNTRIES else c
    return None


def site_country(custom_field: object, region_slug: str | None, region_name: str | None,
                 physical_address: str | None) -> str | None:
    if isinstance(custom_field, str):
        c = code_of(custom_field) if len(custom_field.strip()) == 2 else None
        if c:
            return c
    for t in (region_slug, region_name):
        c = code_of(t)
        if c:
            return c
    lines = [ln.strip() for ln in (physical_address or "").replace(",", "\n").splitlines() if ln.strip()]
    return code_of(lines[-1]) if lines else None
