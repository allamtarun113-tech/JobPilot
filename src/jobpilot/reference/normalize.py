"""One Job shape: normalized company / title / city, the board's own job id from any link, a canonical key."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, unquote, urlparse

_LEGAL = (
    r"(private limited|pvt\.? ltd\.?|pvt|ltd\.?|limited|inc\.?|llc|llp|corp\.?|corporation|co\.?|gmbh|plc)"
)
_CITY_ALIASES = {
    "bangalore": "bengaluru",
    "bengaluru": "bengaluru",
    "gurgaon": "gurugram",
    "gurugram": "gurugram",
    "bombay": "mumbai",
    "mumbai": "mumbai",
    "navi mumbai": "mumbai",
    "new delhi": "delhi",
    "delhi": "delhi",
    "delhi ncr": "delhi",
    "ncr": "delhi",
    "noida": "noida",
    "greater noida": "noida",
    "madras": "chennai",
    "chennai": "chennai",
    "calcutta": "kolkata",
    "kolkata": "kolkata",
    "hyderabad": "hyderabad",
    "secunderabad": "hyderabad",
    "pune": "pune",
    "ahmedabad": "ahmedabad",
    "kochi": "kochi",
    "cochin": "kochi",
    "trivandrum": "thiruvananthapuram",
    "thiruvananthapuram": "thiruvananthapuram",
    "jaipur": "jaipur",
    "chandigarh": "chandigarh",
    "indore": "indore",
    "coimbatore": "coimbatore",
    "visakhapatnam": "visakhapatnam",
    "vizag": "visakhapatnam",
    "palakkad": "palakkad",
    "mysore": "mysuru",
    "mysuru": "mysuru",
}
_REMOTE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.I)
_TITLE_WORDS = {
    "sr": "senior",
    "snr": "senior",
    "jr": "junior",
    "jnr": "junior",
    "dev": "developer",
    "devs": "developers",
    "eng": "engineer",
    "engg": "engineer",
    "mgr": "manager",
    "sde": "software development engineer",
    "swe": "software engineer",
    "ml": "machine learning",
    "ai": "ai",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "i": "1",
}


def company_norm(company: str) -> str:
    """Normalize a company name for dedup.

    Lower-cases, drops parentheticals and punctuation, and strips legal
    suffixes and a trailing "India" ("Acme Pvt. Ltd." -> "acme").
    """
    c = company.lower().replace("&", " and ")
    c = re.sub(r"\(.*?\)", " ", c)
    c = re.sub(r"[^a-z0-9 ]+", " ", c)
    # Twice, for names like "Acme India Pvt Ltd".
    for _ in range(2):
        c = re.sub(rf"\s+{_LEGAL}\s*$", "", c.strip())
        c = re.sub(r"\s+india\s*$", "", c.strip())
    return re.sub(r"\s+", " ", c).strip()


def city_norm(location: str | None) -> str | None:
    """The first known city in a location string ('Bangalore Urban, Karnataka, India' -> 'bengaluru')."""
    if not location:
        return None
    text = location.lower()
    # Longest alias first, so "navi mumbai" wins over "mumbai".
    for alias in sorted(_CITY_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return _CITY_ALIASES[alias]
    first = re.split(r"[,/|;(]", text)[0].strip()
    if not first or _REMOTE.fullmatch(first) or first in ("india", "hybrid", "on-site", "onsite"):
        return None  # "Remote" / "India" is not a city (remote is tracked separately)
    return first


def is_remote(*texts: str | None) -> bool:
    """Return True if any of the texts says remote / work from home."""
    return any(t and _REMOTE.search(t) for t in texts)


def title_norm(title: str) -> str:
    """Normalize a job title for dedup and matching.

    Drops bracketed notes and "- City" tails, and expands abbreviations
    ("Sr. ML Eng" -> "senior machine learning engineer").
    """
    t = title.lower()
    t = re.sub(r"[\(\[].*?[\)\]]", " ", t)  # "(Remote)", "[Hybrid]"
    t = re.split(r"\s+[-|–—@]\s+", t)[0]  # "Python Developer - Bengaluru"
    t = re.sub(r"[^a-z0-9+#. ]+", " ", t)
    words = [_TITLE_WORDS.get(w.strip("."), w.strip(".")) for w in t.split()]
    return " ".join(w for w in words if w)


# ---- the board's own job id, from any link (including tracking redirects) -------------------------

_BOARD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("linkedin", re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*?-)?(\d{6,})", re.I)),
    ("linkedin", re.compile(r"linkedin\.com/.*[?&](?:currentJobId|jobId)=(\d{6,})", re.I)),
    ("naukri", re.compile(r"naukri\.com/job-listings-[^?#]*?-(\d{9,})(?:[?#/]|$)", re.I)),
    ("naukri", re.compile(r"naukri\.com/.*[?&](?:jobId|jid)=(\d{9,})", re.I)),
    ("indeed", re.compile(r"indeed\.[a-z.]+/.*[?&](?:jk|vjk)=([0-9a-f]{16})", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io/([a-z0-9_-]+)/jobs/(\d+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)/([0-9a-f-]{36})", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)/([0-9a-f-]{36})", re.I)),
    ("glassdoor", re.compile(r"glassdoor\.[a-z.]+/.*[?&]jl=(\d+)", re.I)),
]


def _candidate_urls(url: str, depth: int = 0) -> list[str]:
    """The URL itself plus any URLs hidden in its query string (tracking redirects), decoded."""
    out = [unquote(url)]
    if depth < 3:
        for values in parse_qs(urlparse(url).query).values():
            for v in values:
                if v.startswith(("http://", "https://", "http%3A", "https%3A")):
                    out += _candidate_urls(unquote(v), depth + 1)
    return out


def board_job_id(url: str) -> tuple[str, str] | None:
    """('linkedin', '3912345678') for any link that identifies a posting on a known board, else None."""
    for candidate in _candidate_urls(url):
        for board, pattern in _BOARD_PATTERNS:
            m = pattern.search(candidate)
            if m:
                return board, ":".join(m.groups())
    return None


def canonical_key(board_id: tuple[str, str] | None, company: str, title: str, city: str | None) -> str:
    """Return the job's dedup key: the board id, else company|title|city."""
    if board_id:
        return f"{board_id[0]}:{board_id[1]}"
    return f"co:{company_norm(company)}|{title_norm(title)}|{city or 'any'}"


# ---- JD fingerprints --------------------------------------------------------------------------------


def jd_hash(text: str) -> str:
    """Return a SHA-256 of the JD text, ignoring case and whitespace."""
    return hashlib.sha256(re.sub(r"\s+", " ", text.strip().lower()).encode()).hexdigest()


def simhash(text: str) -> int:
    """64-bit SimHash over word 3-shingles, returned as a signed 64-bit int (fits a Postgres BIGINT)."""
    words = re.findall(r"[a-z0-9+#]+", text.lower())
    shingles = [" ".join(words[i : i + 3]) for i in range(max(1, len(words) - 2))]
    weights = [0] * 64
    for sh in shingles:
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            # Each shingle votes on every bit; bits with more votes
            # become 1.
            weights[bit] += 1 if h >> bit & 1 else -1
    value = sum(1 << bit for bit in range(64) if weights[bit] > 0)
    # Shift into the signed range of a Postgres BIGINT.
    return value - (1 << 64) if value >= 1 << 63 else value


def material_jd_change(old_text: str, new_text: str, old_was_snippet: bool) -> bool:
    """A newly found JD is material when it replaces a snippet with real (>=200 chars), different text."""
    return bool(old_was_snippet and len(new_text.strip()) >= 200 and jd_hash(old_text) != jd_hash(new_text))


def hamming(a: int, b: int) -> int:
    """Return how many of the 64 bits differ between two SimHashes."""
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")
