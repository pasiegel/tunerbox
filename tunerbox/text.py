"""Channel-name normalization shared by matching and EPG lookups."""
import re
import unicodedata

# Country/provider prefixes like "US: ", "USA | ", "[US] ", "US - "
_PREFIX = re.compile(r"^\s*[\[\(|]?\s*(us|usa|u\.s\.|en|am|na)\s*[\]\)|:\-]+\s*", re.I)
_BRACKETS = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
_QUALITY = re.compile(
    r"\b(hd|fhd|uhd|sd|hq|4k|8k|hevc|h\.?26[45]|1080[pi]?|720p|576[pi]?|480p|360p|60fps|50fps|"
    r"backup|alt|feed|raw|vip|multi|audio|live)\b", re.I)
_FOREIGN = re.compile(r"^\s*[\[\(|]?\s*(uk|ca|au|nz|ie|de|fr|es|it|nl|mx|br|ar|in|pk|pt|pl|tr|ru|ar|latino|lat)\s*[\]\)|:\-]+", re.I)
_COUNTRY = re.compile(
    r"[\(\[]\s*(uk|gb|ca|canada|au|australia|nz|ie|in|india|indonesia|mx|mexico|de|fr|es|it|nl|br|ph|"
    r"philippines|africa|arabia|asia|europe|latam|intl|international)\s*[\)\]]|"
    r"\b(indonesia|india|canada|australia|philippines|arabia|africa|asia|europe|pakistan|turkiye)\b|"
    r"^\s*sky\b", re.I)
_SPANISH = re.compile(r"\b(espanol|español|latino|latin|spanish|deportes|en vivo|mexico|méxico)\b", re.I)
_WEST = re.compile(r"\(\s*(west|pacific|mountain)\s*\)|\bwest\s*(hd)?\s*$|\bpacific\b|\bpst\b", re.I)

COMMON_WORDS = {"network", "channel", "television", "tv", "the", "usa", "us", "east", "cable", "hd"}


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def norm(name: str) -> str:
    """'US: Fox News Channel HD (1080p)' -> 'fox news channel'"""
    if not name:
        return ""
    s = name
    for _ in range(2):
        s = _PREFIX.sub("", s)
    s = _BRACKETS.sub(" ", s)
    s = ascii_fold(s).lower()
    s = s.replace("&", " and ").replace("+", " plus ").replace("!", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _QUALITY.sub(" ", s)
    s = re.sub(r"\beast\s*$", " ", s)
    s = re.sub(r"\b([a-z]{2,})(\d)\b", r"\1 \2", s)  # espn2 -> espn 2
    return re.sub(r"\s+", " ", s).strip()


def core(name_norm: str) -> str:
    """Drop filler words: 'fox news channel' -> 'fox news'."""
    words = [w for w in name_norm.split() if w not in COMMON_WORDS]
    return " ".join(words) or name_norm


def tvg_stem(tvg_id: str) -> str:
    """'Fox.News.Channel.HD.us2' -> 'fox news channel'"""
    if not tvg_id:
        return ""
    s = re.sub(r"@.*$", "", tvg_id)
    s = re.sub(r"\.(us|usa|us\d|us_locals\d|ca\d?|uk\d?|com|tv)$", "", s, flags=re.I)
    return norm(s.replace(".", " ").replace("_", " "))


def is_foreign(name: str) -> bool:
    s = ascii_fold(name or "")        # "(México)" -> "(Mexico)"
    return bool(_FOREIGN.match(s) or _COUNTRY.search(s))


def is_spanish(name: str) -> bool:
    return bool(_SPANISH.search(ascii_fold(name or "")))


def is_west(name: str) -> bool:
    return bool(_WEST.search(name or ""))


def parse_callsign(cs: str):
    """'WOODDT2' -> ('WOOD', 2); 'WXMI' -> ('WXMI', 1); 'CNN' -> None"""
    if not cs:
        return None
    s = re.sub(r"[^A-Z0-9]", "", cs.upper())
    m = re.match(r"^([KW][A-Z]{2,3}?)(TV|DT|CD|CA|LD|LP)?(\d{1,2})?$", s)
    if not m:
        return None
    base, _suffix, sub = m.groups()
    if len(base) < 3:
        return None
    return base, int(sub) if sub else 1


def find_callsigns(text: str):
    """Find US broadcast callsigns mentioned in a stream name or tvg-id.
    Returns set of (BASE, sub)."""
    out = set()
    if not text:
        return out
    up = ascii_fold(text).upper()
    for m in re.finditer(r"(?<![A-Z0-9])([KW][A-Z]{2,3})(?:[-_ .]?(TV|DT|CD|CA|LD|LP))?(?:[-_ .]?(\d{1,2}))?(?![A-Z])", up):
        base, suffix, sub = m.groups()
        # A bare 4-letter word like "WORD" or "WEST" is too ambiguous unless it
        # looks like a callsign context (suffix, dot-id, or all caps in original)
        if base in {"WEST", "WILD", "WORLD", "WOW", "WHAT", "WITH", "WAR", "WEB", "KIDS", "KIND", "KING",
                    "WAVE", "WIRE", "WISH", "WAY", "WOW", "KNOW", "WAR", "WIN", "WINE", "WORK", "WEEK"}:
            continue
        original = text[m.start():m.start() + len(base)] if len(text) >= m.start() + len(base) else ""
        if not suffix and original != base and "." not in text:
            continue  # "Wood" in normal-case prose, not a callsign
        out.add((base, int(sub) if sub else 1))
    return out
