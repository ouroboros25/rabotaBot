"""Country normalisation.

Sources disagree wildly on how they express eligibility: Himalayas ships country
names ("United States"), Lever ships ISO-2, RemoteOK ships free text ("Worldwide",
"EMEA"). Comparing those to the user's ISO-2 list without normalising produces
the worst possible failure mode: a silent false GEO_FENCED that hides jobs the
user was actually eligible for, with no error anywhere.
"""
from __future__ import annotations

import re

# Values that mean "no country restriction at all".
WORLDWIDE = frozenset({
    "worldwide", "anywhere", "global", "remote", "any", "international",
    "remote worldwide", "anywhere in the world", "fully remote", "everywhere",
    "no restriction", "no restrictions", "location independent",
})

# Regional shorthands expanded to the countries that matter for this profile.
REGIONS: dict[str, set[str]] = {
    "emea": {"GB", "DE", "FR", "NL", "ES", "IT", "PL", "UA", "PT", "SE", "NO", "DK",
             "FI", "IE", "CH", "AT", "BE", "CZ", "RO", "BG", "GR", "HU", "IL", "AE", "ZA"},
    "europe": {"GB", "DE", "FR", "NL", "ES", "IT", "PL", "UA", "PT", "SE", "NO", "DK",
               "FI", "IE", "CH", "AT", "BE", "CZ", "RO", "BG", "GR", "HU", "HR", "SK",
               "SI", "LT", "LV", "EE", "RS"},
    "eu": {"DE", "FR", "NL", "ES", "IT", "PL", "PT", "SE", "DK", "FI", "IE", "AT",
           "BE", "CZ", "RO", "BG", "GR", "HU", "HR", "SK", "SI", "LT", "LV", "EE"},
    "cee": {"PL", "UA", "CZ", "SK", "HU", "RO", "BG", "LT", "LV", "EE", "RS", "HR"},
    "apac": {"AU", "NZ", "SG", "JP", "IN", "PH", "ID", "MY", "TH", "VN", "KR", "CN"},
    "latam": {"BR", "MX", "AR", "CO", "CL", "PE", "UY", "CR"},
    "americas": {"US", "CA", "BR", "MX", "AR", "CO", "CL"},
    "north america": {"US", "CA", "MX"},
    "uk": {"GB"},
}

_NAME_TO_ISO: dict[str, str] = {
    "united states": "US", "united states of america": "US", "usa": "US", "u.s.": "US",
    "us": "US", "america": "US",
    "united kingdom": "GB", "great britain": "GB", "england": "GB", "scotland": "GB",
    "wales": "GB", "northern ireland": "GB", "gb": "GB", "uk": "GB",
    "canada": "CA", "ca": "CA",
    "ukraine": "UA", "ua": "UA",
    "poland": "PL", "polska": "PL", "pl": "PL",
    "germany": "DE", "deutschland": "DE", "de": "DE",
    "france": "FR", "fr": "FR",
    "netherlands": "NL", "the netherlands": "NL", "holland": "NL", "nl": "NL",
    "spain": "ES", "es": "ES", "italy": "IT", "it": "IT",
    "portugal": "PT", "pt": "PT", "ireland": "IE", "ie": "IE",
    "sweden": "SE", "se": "SE", "norway": "NO", "no": "NO",
    "denmark": "DK", "dk": "DK", "finland": "FI", "fi": "FI",
    "switzerland": "CH", "ch": "CH", "austria": "AT", "at": "AT",
    "belgium": "BE", "be": "BE", "czech republic": "CZ", "czechia": "CZ", "cz": "CZ",
    "romania": "RO", "ro": "RO", "bulgaria": "BG", "bg": "BG",
    "greece": "GR", "gr": "GR", "hungary": "HU", "hu": "HU",
    "croatia": "HR", "hr": "HR", "slovakia": "SK", "sk": "SK",
    "slovenia": "SI", "si": "SI", "lithuania": "LT", "lt": "LT",
    "latvia": "LV", "lv": "LV", "estonia": "EE", "ee": "EE",
    "serbia": "RS", "rs": "RS", "turkey": "TR", "tr": "TR",
    "israel": "IL", "il": "IL", "united arab emirates": "AE", "uae": "AE", "ae": "AE",
    "india": "IN", "in": "IN", "australia": "AU", "au": "AU",
    "new zealand": "NZ", "nz": "NZ", "singapore": "SG", "sg": "SG",
    "japan": "JP", "jp": "JP", "south korea": "KR", "kr": "KR",
    "brazil": "BR", "br": "BR", "mexico": "MX", "mx": "MX",
    "argentina": "AR", "ar": "AR", "colombia": "CO", "co": "CO",
    "chile": "CL", "cl": "CL", "south africa": "ZA", "za": "ZA",
    "philippines": "PH", "ph": "PH", "indonesia": "ID", "id": "ID",
    "malaysia": "MY", "my": "MY", "vietnam": "VN", "vn": "VN",
    "thailand": "TH", "th": "TH", "china": "CN", "cn": "CN",
    "nigeria": "NG", "ng": "NG", "kenya": "KE", "ke": "KE", "egypt": "EG", "eg": "EG",
}

_CLEAN = re.compile(r"[^a-z\s.]")


def normalize_one(value: str | None) -> set[str] | None:
    """Return ISO-2 codes, an empty set for "worldwide", or None if unparseable.

    The distinction matters: empty set means "explicitly unrestricted", None means
    "we do not know", and only the first should be treated as good news.
    """
    if not value:
        return None
    raw = _CLEAN.sub("", str(value).strip().lower()).strip()
    if not raw:
        return None
    if raw in WORLDWIDE:
        return set()
    if raw in REGIONS:
        return set(REGIONS[raw])
    if raw in _NAME_TO_ISO:
        return {_NAME_TO_ISO[raw]}
    # "Remote, United States" / "United States (Remote)"
    for part in re.split(r"[,/;()]| or ", raw):
        part = part.strip()
        if part in _NAME_TO_ISO:
            return {_NAME_TO_ISO[part]}
        if part in REGIONS:
            return set(REGIONS[part])
    return None


def normalize_list(values) -> tuple[list[str] | None, bool]:
    """Normalise a list of location strings.

    Returns (iso_codes, worldwide). ``worldwide`` is True when at least one entry
    explicitly says "anywhere", which overrides everything else.
    """
    if not values:
        return None, False
    codes: set[str] = set()
    worldwide = False
    unparsed = 0
    for value in values:
        result = normalize_one(value)
        if result is None:
            unparsed += 1
            continue
        if not result:
            worldwide = True
        codes |= result
    if worldwide:
        return None, True
    if not codes:
        # Nothing parsed: better to know nothing than to gate on a guess.
        return None, False
    return sorted(codes), False


def eligible(allowed_codes: list[str] | None, user_codes: list[str] | None) -> bool | None:
    """None = unknown, True = eligible, False = provably fenced out."""
    if not allowed_codes:
        return None
    if not user_codes:
        return None
    return bool({c.upper() for c in allowed_codes} & {c.upper() for c in user_codes})
