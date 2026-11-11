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
import unicodedata

# The 249 officially assigned ISO 3166-1 alpha-2 codes.
ISO_CODES = frozenset((
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ "
    "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO "
    "FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE "
    "JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO "
    "MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW "
    "PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM "
    "TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW "
).split())

# Two-letter codes that are not countries.
NOT_COUNTRIES = {"EU", "UN", "XX", "ZZ"}

# Country names (and common short or older names) -> code, as `fold` leaves them: lower case, accents off, no
# punctuation. Every country with a provider pack has its English name here, so a region called "Bulgaria" or a typed
# "Hong Kong" reads as that country; a few names a country uses for itself in Latin script are known too.
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
    "bulgaria": "BG", "croatia": "HR", "cyprus": "CY", "estonia": "EE", "hungary": "HU", "latvia": "LV",
    "lithuania": "LT", "malta": "MT", "romania": "RO", "slovakia": "SK", "slovenia": "SI", "turkiye": "TR",
    "turkey": "TR", "hong kong": "HK", "taiwan": "TW", "indonesia": "ID", "malaysia": "MY", "philippines": "PH",
    "the philippines": "PH", "thailand": "TH", "vietnam": "VN", "viet nam": "VN",
    "republic of korea": "KR", "espana": "ES", "italia": "IT", "osterreich": "AT", "schweiz": "CH", "suisse": "CH",
    "belgie": "BE", "belgique": "BE", "danmark": "DK", "norge": "NO", "sverige": "SE", "suomi": "FI",
    "polska": "PL", "brasil": "BR",
}
# The name a country is shown under, and the name of the region the game makes for it, where the first name above
# in title case is not it.
SHOWN: dict[str, str] = {"TR": "Türkiye"}


def fold(s: str) -> str:
    """A country name as it is compared: lower case, accents off ("España" is "espana"), letters and spaces only."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z ]", "", plain).strip()


def name_of(code: str) -> str | None:
    """The English name of a country the game knows by name, or None."""
    code = code.upper()
    return SHOWN.get(code) or next((n.title() for n, c in NAMES.items() if c == code), None)


def code_of(text: str | None) -> str | None:
    """A country code from a region slug or name or an address line, or None."""
    if not text:
        return None
    t = text.strip()
    if fold(t) in NAMES:  # first, so "UK" reads as GB rather than as a code
        return NAMES[fold(t)]
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
