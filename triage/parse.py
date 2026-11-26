"""Parse POLICY.md, money, dates, names and evidence markdown.

Pure functions. No case identifiers from the development set.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_SUFFIXES = {
    "ltd", "limited", "plc", "llp", "inc", "llc", "co", "company", "uk",
    "sal", "sarl", "fze", "srl", "bv", "gmbh", "sa", "ag", "nv", "oy",
    "sp", "zoo", "z", "oo",
}


def to_pence(value) -> int | None:
    if value is None or value is False:
        return None
    if isinstance(value, str):
        text = value.replace("£", "").replace(",", "").strip()
        if not text or text in {"-", "n/a", "null"}:
            return None
        try:
            quant = Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except Exception:
            return None
        return int(quant * 100)
    try:
        quant = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    return int(quant * 100)


def gbp(pence: int | None) -> str:
    if pence is None:
        return "an unstated amount"
    sign = "-" if pence < 0 else ""
    pence = abs(int(pence))
    pounds, pennies = divmod(pence, 100)
    return f"{sign}£{pounds:,}.{pennies:02d}"


def near(a: int | None, b: int | None, rel: float = 0.01, abs_p: int = 100) -> bool:
    if a is None or b is None:
        return False
    diff = abs(int(a) - int(b))
    scale = max(abs(int(a)), abs(int(b)), 1)
    return diff <= abs_p or (diff / scale) <= rel


def parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    head = text[:10]
    try:
        if len(head) >= 10 and head[4] == "-" and head[7] == "-":
            return date.fromisoformat(head)
    except ValueError:
        pass
    match = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if match:
        month = MONTHS.get(match.group(2).lower())
        if month:
            try:
                return date(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return None
    match = re.search(r"([A-Za-z]+)\s+(\d{4})", text)
    if match:
        month = MONTHS.get(match.group(1).lower())
        if month:
            return date(int(match.group(2)), month, 1)
    return None


def fmt_date(value) -> str:
    parsed = value if isinstance(value, date) else parse_date(value)
    if not parsed:
        return str(value or "")
    return f"{parsed.day} {MONTH_ABBR[parsed.month - 1]} {parsed.year}"


def tokens(name: str | None) -> list[str]:
    if not name:
        return []
    text = name.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return [tok for tok in text.split() if tok not in _SUFFIXES and tok not in {"the", "of", "and"}]


def names_match(left: str | None, right: str | None) -> bool:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return False
    if a == b:
        return True
    sa, sb = set(a), set(b)
    short, long = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(short) >= 2 and short <= long:
        return True
    return False


def human_join(items: list[str]) -> str:
    clean = [item for item in items if item]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    if len(clean) == 2:
        return f"{clean[0]} and {clean[1]}"
    return ", ".join(clean[:-1]) + " and " + clean[-1]


def money_amounts(text: str) -> list[int]:
    found = []
    for raw in re.findall(r"£\s*([\d,]+(?:\.\d{2})?)", text or ""):
        pence = to_pence(raw)
        if pence is not None:
            found.append(pence)
    return found


def bold_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"\*\*([^*:\n]+):\*\*\s*(.+)", text or ""):
        key = re.sub(r"\s+", " ", match.group(1)).strip().lower()
        fields[key] = match.group(2).strip()
    return fields


def markdown_tables(text: str) -> list[list[dict[str, str]]]:
    lines = (text or "").splitlines()
    tables: list[list[dict[str, str]]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("|") and index + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[index + 1].strip()):
            headers = [cell.strip().lower() for cell in line.strip("|").split("|")]
            index += 2
            rows: list[dict[str, str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                row = {}
                for pos, header in enumerate(headers):
                    row[header] = cells[pos] if pos < len(cells) else ""
                rows.append(row)
                index += 1
            tables.append(rows)
            continue
        index += 1
    return tables


def column_amounts(rows: list[dict[str, str]], *needles: str) -> list[int]:
    amounts: list[int] = []
    for row in rows:
        for key, value in row.items():
            if any(needle in key for needle in needles):
                for amount in money_amounts(value):
                    amounts.append(amount)
                if not money_amounts(value):
                    pence = to_pence(value)
                    if pence is not None and re.search(r"\d", value):
                        # bare numbers in money columns are pounds
                        if "£" not in value and pence < 100000000:
                            amounts.append(pence)
    return amounts


def parse_policy(text: str, institution: str) -> dict:
    """Read the limits that are written in POLICY.md. Qualitative rules stay in the engine."""
    countries: list[str] = []
    match = re.search(r"Higher-risk countries[^:]*:\s*(.+)", text or "")
    if match:
        countries = [part.strip().rstrip(".") for part in match.group(1).split(",") if part.strip()]
    invoice_above = None
    if re.search(r"Payments of £10,000 or more", text or "") or institution == "payments_fx":
        if institution == "payments_fx":
            invoice_above = 1_000_000  # £10,000.00
    own_multiple = 5
    multiple = re.search(r"at\s+(\d+)\s+times the declared", text or "", re.I)
    if multiple:
        own_multiple = int(multiple.group(1))
    return {
        "institution": institution,
        "high_risk": countries,
        "invoice_above": invoice_above,
        "own_multiple": own_multiple,
        "dormant_days": 180,
    }


def dob_parts(value: str | None) -> tuple[int | None, int | None, int | None]:
    """Return (year, month, day) with None where unknown."""
    if not value:
        return (None, None, None)
    text = str(value).strip()
    iso = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return (int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    long = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if long:
        month = MONTHS.get(long.group(2).lower())
        return (int(long.group(3)), month, int(long.group(1)))
    month_year = re.search(r"([A-Za-z]+)\s+(\d{4})", text)
    if month_year:
        month = MONTHS.get(month_year.group(1).lower())
        return (int(month_year.group(2)), month, None)
    year = re.search(r"(19|20)\d{2}", text)
    if year:
        return (int(year.group(0)), None, None)
    return (None, None, None)


def dob_match(left: str | None, right: str | None) -> str:
    """Return 'match', 'differ' or 'unknown'."""
    y1, m1, d1 = dob_parts(left)
    y2, m2, d2 = dob_parts(right)
    if y1 is None or y2 is None:
        return "unknown"
    if y1 != y2:
        return "differ"
    if m1 and m2 and m1 != m2:
        return "differ"
    if d1 and d2 and m1 and m2 and d1 != d2:
        return "differ"
    if (m1 and m2) or (d1 and d2):
        return "match"
    # same year only is not enough to prove identity
    return "unknown"


def dob_text(value: str | None) -> str:
    year, month, day = dob_parts(value)
    if year and month and day:
        return f"{day} {MONTH_ABBR[month - 1]} {year}"
    if year and month:
        return f"{MONTH_ABBR[month - 1]} {year}"
    if year:
        return str(year)
    return value or ""
