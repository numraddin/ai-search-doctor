"""Date and contact-signal extraction helpers (pure functions, no network)."""
import re
from datetime import date

_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MN = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MDY = re.compile(rf"\b{_MN}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MN}\.?,?\s+(\d{{4}})\b", re.I)
_MY = re.compile(rf"\b{_MN}\.?\s+(\d{{4}})\b", re.I)
_LABEL = re.compile(r"(?:last\s+)?(?:updated|modified|published|posted|reviewed|revised)(?:\s+on)?\b[^0-9A-Za-z]{0,6}", re.I)
_COPY = re.compile(r"(?:©|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w/])\+?\d[\d\s().-]{7,17}\d(?![\w/])")
ADDRESS = re.compile(r"\b\d{1,5}\s+(?:[A-Z][\w.]*\s+){1,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct|Place|Pl|Square|Plaza)\b\.?")


def _valid(y, m, d):
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    today = date.today()
    return dt if 1995 <= dt.year and (dt - today).days <= 366 else None


def parse_any(s: str):
    """First recognizable date in `s`, or None."""
    s = s or ""
    if m := _ISO.search(s):
        return _valid(int(m[1]), int(m[2]), int(m[3]))
    if m := _MDY.search(s):
        return _valid(int(m[3]), _MONTHS[m[1].lower()], int(m[2]))
    if m := _DMY.search(s):
        return _valid(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))
    if m := _MY.search(s):
        return _valid(int(m[2]), _MONTHS[m[1].lower()], 1)
    return None


def labeled_dates(text: str) -> list[tuple[date, str]]:
    """Dates that sit right after words like 'Last updated' / 'Published'. Returns (date, snippet)."""
    out = []
    for m in _LABEL.finditer(text or ""):
        window = text[m.start(): m.end() + 40]
        d = parse_any(text[m.end(): m.end() + 40])
        if d:
            out.append((d, re.sub(r"\s+", " ", window).strip()))
    return out


def copyright_year(text: str):
    years = [int(y) for y in _COPY.findall(text or "") if 1995 <= int(y) <= date.today().year + 1]
    return max(years) if years else None


_NOT_PHONE = re.compile(r"\d{4}[-./]\d{2}[-./]\d{2}|\d{4}\s*[-–]\s*\d{4}|\d{1,2}[./]\d{1,2}[./]\d{2,4}")


def find_phones(text: str) -> list[str]:
    """Phone-number-like strings (7 to 15 digits), ignoring dates and year ranges."""
    out = []
    for m in PHONE.finditer(text or ""):
        s = m.group(0).strip()
        if 7 <= len(re.sub(r"\D", "", s)) <= 15 and not _NOT_PHONE.fullmatch(s):
            out.append(s)
    return out
