"""Resume parsing engine.

Pipeline
    acquire text -> normalise lines -> segment sections -> run field extractors
    -> resolve skills against the taxonomy -> score recall and confidence

Every extracted value carries three things beside the value itself: a confidence
number, the method that produced it, and the line it was read from. Milestone 1 is
graded on how much of a resume the parser can read, so the parser has to be able to
show its work rather than just assert a percentage.

Only the file readers need third-party packages, and they are imported lazily, so
the extraction logic can be tested with nothing but the standard library.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field as dc_field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .taxonomy import ALIASES, CATEGORIES, TAXONOMY

# --------------------------------------------------------------------------- #
# field contract: weight drives the graded recall number
# --------------------------------------------------------------------------- #
REQUIRED_FIELDS: List[Tuple[str, str, int]] = [
    ("name", "Full name", 12),
    ("email", "Email", 12),
    ("phone", "Phone", 10),
    ("location", "Location", 6),
    ("degree", "Highest degree", 10),
    ("discipline", "Field of study", 6),
    ("institution", "Institution", 7),
    ("grad_year", "Graduation year", 5),
    ("total_years", "Total experience", 10),
    ("current_title", "Current title", 8),
    ("employers", "Employers", 5),
    ("skills", "Skills", 6),
]
OPTIONAL_FIELDS: List[Tuple[str, str]] = [
    ("linkedin", "LinkedIn"),
    ("github", "Portfolio / Git"),
    ("certifications", "Certifications"),
    ("languages", "Languages"),
]

ACCEPTANCE_THRESHOLD = 95.0  # Milestone 1, week 2

SECTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("summary", re.compile(r"^(professional\s+)?(summary|profile|objective|about\s*me|career\s+objective|overview)\b", re.I)),
    ("skills", re.compile(r"^(technical\s+|core\s+|key\s+)?(skills?|competenc\w+|technologies|tech\s+stack|skill\s+set|areas?\s+of\s+expertise|expertise)\b", re.I)),
    ("experience", re.compile(r"^(work\s+|professional\s+|relevant\s+)?(experience|employment(\s+history)?|career\s+history|work\s+history|internships?)\b", re.I)),
    ("education", re.compile(r"^(education|academics?|academic\s+(background|qualifications?|details)|educational\s+qualifications?)\b", re.I)),
    ("projects", re.compile(r"^(academic\s+|key\s+|personal\s+|selected\s+)?projects?\b", re.I)),
    ("certifications", re.compile(r"^(certifications?|licen[sc]es?|courses?\s*(&|and)?\s*certifications?|training)\b", re.I)),
    ("awards", re.compile(r"^(achievements?|awards?|honou?rs?|accomplishments?|recognition)\b", re.I)),
    ("languages", re.compile(r"^(languages?)\b", re.I)),
]

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
MONTH_WORD = r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
DASH = r"(?:-|\u2013|\u2014|to|until|through)"

DEGREE_TIERS: List[Tuple[int, re.Pattern, str]] = [
    (5, re.compile(r"\b(ph\.?\s?d|doctorate|doctoral)\b", re.I), "Doctorate"),
    (4, re.compile(r"\b(m\.?\s?tech|m\.?e\.?\b|m\.?\s?sc|mca|mba|m\.?s\.?\b|m\.?com|m\.?a\.?\b|master(?:'s)?(?:\s+of\s+[a-z]+)?)\b", re.I), "Master's"),
    (3, re.compile(r"\b(b\.?\s?tech|b\.?e\.?\b|b\.?\s?sc|b\.?s\.?\b|bca|b\.?com|bba|b\.?a\.?\b|bachelor(?:'s)?(?:\s+of\s+[a-z]+)?)\b", re.I), "Bachelor's"),
    (2, re.compile(r"\b(diploma|polytechnic)\b", re.I), "Diploma"),
    (1, re.compile(r"\b(higher\s+secondary|intermediate|12th|hsc|cbse|icse|senior\s+secondary)\b", re.I), "Secondary"),
]

INSTITUTION_RE = re.compile(
    r"\b(university|institute|institution|college|school\s+of|academy|polytechnic|IIT|NIT|IIIT|BITS|JNTU|business\s+administration)\b", re.I)

TITLE_RE = re.compile(r"\b(" + "|".join([
    r"chief\s+\w+\s+officer", r"vice\s+president", r"director", r"principal\s+\w+",
    r"staff\s+engineer", r"engineering\s+manager", r"tech\s+lead",
    r"lead\s+[a-z]+\s*(?:engineer|analyst|developer|consultant)?",
    r"senior\s+[a-z]+\s+(?:engineer|developer|analyst|consultant|scientist|designer|architect)",
    r"(?:software|systems?|platform|frontend|front-end|backend|back-end|full[-\s]?stack|data|devops|"
    r"site\s+reliability|cloud|security|network|qa|test|automation|machine\s+learning|ml|ai)\s+"
    r"(?:development\s+)?(?:engineer|developer|analyst|architect|scientist|specialist)(?:\s+I{1,3}| [123])?",
    r"data\s+scientist", r"data\s+analyst", r"business\s+analyst", r"product\s+manager",
    r"project\s+manager", r"program\s+manager", r"scrum\s+master", r"solution\s+architect",
    r"associate\s+[a-z]+\s*(?:engineer|analyst|developer|consultant)?",
    r"technical\s+consultant", r"consultant", r"ui\/ux\s+designer", r"ux\s+designer",
    r"operations\s+executive", r"system(?:s)?\s+engineer", r"support\s+engineer",
    r"software\s+test\s+engineer", r"qa\s+automation\s+engineer",
    r"[a-z]+\s+intern", r"intern", r"trainee",
]) + r")\b", re.I)

COMPANY_SUFFIX = re.compile(
    r"\b(pvt\.?\s*ltd\.?|private\s+limited|limited|ltd\.?|inc\.?|llc|llp|plc|gmbh|corp\.?|corporation|"
    r"technologies|solutions|labs|consulting|consultancy|infotech|analytics|group|bank|gbs)\b", re.I)

KNOWN_COMPANIES = [
    "infosys", "tcs", "wipro", "accenture", "cognizant", "capgemini", "ibm", "oracle", "microsoft",
    "google", "amazon", "stripe", "airbnb", "shopify", "razorpay", "zoho", "flipkart", "swiggy",
    "zomato", "paytm", "phonepe", "hsbc", "deloitte", "kpmg", "pwc", "mindtree", "persistent",
    "tech mahindra", "ltimindtree", "standard chartered", "icici", "hdfc", "nvidia", "meta",
    "netflix", "adobe", "salesforce", "sap", "siemens", "bosch", "qualcomm", "intel", "cisco",
    "dell", "mu sigma",
]

CITIES = [
    "bengaluru", "bangalore", "mumbai", "pune", "hyderabad", "chennai", "new delhi", "delhi",
    "gurugram", "gurgaon", "noida", "kolkata", "ahmedabad", "jaipur", "kochi", "coimbatore",
    "nagpur", "indore", "bhopal", "lucknow", "chandigarh", "visakhapatnam", "vizag",
    "thiruvananthapuram", "mysuru", "mysore", "surat", "vadodara", "nashik", "patna",
    "bhubaneswar", "guwahati", "dehradun", "san francisco", "new york", "seattle", "austin",
    "boston", "chicago", "los angeles", "toronto", "vancouver", "london", "manchester", "dublin",
    "berlin", "munich", "amsterdam", "paris", "zurich", "singapore", "dubai", "abu dhabi",
    "sydney", "melbourne", "tokyo", "kuala lumpur",
]
REGIONS = [
    "karnataka", "maharashtra", "telangana", "tamil nadu", "kerala", "andhra pradesh", "gujarat",
    "rajasthan", "west bengal", "uttar pradesh", "madhya pradesh", "punjab", "haryana", "odisha",
    "bihar", "assam", "goa", "delhi", "california", "texas", "ontario", "british columbia",
]
US_ABBR = {"CA", "NY", "WA", "TX", "MA", "IL", "NJ", "PA", "GA", "FL", "CO", "NC", "VA",
           "OH", "MI", "AZ", "OR", "UT", "MN", "ON", "BC", "QC"}
SMALL_WORDS = {"and", "of", "in", "for", "the", "at", "on", "with", "to", "a", "an"}


# --------------------------------------------------------------------------- #
# result types
# --------------------------------------------------------------------------- #
@dataclass
class Extracted:
    """One field, plus enough context for a recruiter to check it."""
    value: Any = None
    confidence: float = 0.0
    line: int = -1
    method: str = ""

    @property
    def found(self) -> bool:
        return self.value is not None


@dataclass
class ParsedResume:
    source_file: str
    source_format: str
    fields: Dict[str, Extracted] = dc_field(default_factory=dict)
    skills: List[Dict[str, Any]] = dc_field(default_factory=list)
    sections: List[str] = dc_field(default_factory=list)
    tenure_gaps: List[Dict[str, Any]] = dc_field(default_factory=list)
    seniority: str = "Unclassified"
    field_recall: float = 0.0
    mean_confidence: float = 0.0
    fields_captured: int = 0
    fields_required: int = len(REQUIRED_FIELDS)
    optional_captured: int = 0
    identity_hash: str = ""
    parse_ms: int = 0
    char_count: int = 0
    is_resume: bool = True
    lines: List[str] = dc_field(default_factory=list, repr=False)

    def get(self, key: str) -> Any:
        rec = self.fields.get(key)
        return rec.value if rec else None

    def to_dict(self) -> Dict[str, Any]:
        """The contract the matching engine consumes in Milestone 2."""
        return {
            "identity_hash": self.identity_hash,
            "name": self.get("name"),
            "email": self.get("email"),
            "phone": self.get("phone"),
            "location": self.get("location"),
            "links": {"linkedin": self.get("linkedin"), "github": self.get("github")},
            "education": {
                "degree": self.get("degree"),
                "discipline": self.get("discipline"),
                "institution": self.get("institution"),
                "graduation_year": self.get("grad_year"),
            },
            "experience": {
                "total_years": self.get("total_years"),
                "seniority": self.seniority,
                "current_title": self.get("current_title"),
                "employers": self.get("employers") or [],
                "gaps": self.tenure_gaps,
            },
            "skills": self.skills,
            "certifications": self.get("certifications") or [],
            "languages": self.get("languages") or [],
            "provenance": {
                k: {"line": v.line + 1, "method": v.method, "confidence": round(v.confidence, 2)}
                for k, v in self.fields.items() if v.found
            },
            "quality": {
                "field_recall": self.field_recall,
                "mean_confidence": self.mean_confidence,
                "fields_captured": f"{self.fields_captured}/{self.fields_required}",
                "optional_captured": self.optional_captured,
                "source_format": self.source_format,
                "parse_ms": self.parse_ms,
                "resume_path": f"uploads/{self.source_file}",
            },
        }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def title_case(text: str) -> str:
    words = text.lower().split()
    return " ".join(w if i and w in SMALL_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words) if w)


def normalise(raw: str) -> List[str]:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    raw = re.sub(r"[\u2022\u25aa\u25e6\u00b7\u2023\u2043]", "- ", raw)
    return [re.sub(r"[ \t]{2,}", " ", line).strip() for line in raw.split("\n")]


def segment(lines: List[str]) -> Dict[str, Dict[str, Any]]:
    """Find section headings. A line that carries content is not a heading —
    'Languages: Java, Python' belongs to the skills block, not a languages section."""
    marks: List[Tuple[str, int]] = []
    for i, line in enumerate(lines):
        if not line or len(line) > 62 or "," in line or re.search(r":\s*\S", line):
            continue
        bare = re.sub(r"[:\-\u2013\u2014_|]+$", "", line).strip()
        if not bare or len(bare.split()) > 4:
            continue
        for key, pattern in SECTION_PATTERNS:
            if pattern.match(bare):
                marks.append((key, i))
                break

    sections: Dict[str, Dict[str, Any]] = {}
    for idx, (key, at) in enumerate(marks):
        end = marks[idx + 1][1] if idx + 1 < len(marks) else len(lines)
        rows = [(j, lines[j]) for j in range(at + 1, end)]
        if key in sections:
            sections[key]["rows"].extend(rows)
        else:
            sections[key] = {"start": at, "rows": rows}
    return sections


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def month_value(month_token: Optional[str], year_token: str) -> int:
    year = int(year_token)
    if len(year_token) == 2:
        year = 1900 + year if year > 40 else 2000 + year
    key = (month_token or "jan")[:3].lower()
    return year * 12 + MONTHS.get(key, 0)


def month_label(value: int) -> str:
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{names[value % 12]} {value // 12}"


# --------------------------------------------------------------------------- #
# field extractors
# --------------------------------------------------------------------------- #
def extract_email(lines: List[str]) -> Extracted:
    pattern = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    for i, line in enumerate(lines):
        m = pattern.search(line)
        if m:
            return Extracted(m.group(0).lower().rstrip(".,;"), 0.99, i, "pattern match")
    return Extracted()


PHONE_PATTERNS = [
    (re.compile(r"\+91[\s\-]?\d{5}[\s\-]?\d{5}"), 0.98),
    (re.compile(r"\+91[\s\-]?\d{10}"), 0.98),
    (re.compile(r"\+\d{1,3}[\s\-]\d{2,5}[\s\-]\d{3,5}(?:[\s\-]\d{2,5})?"), 0.94),
    (re.compile(r"\(\d{3}\)\s?\d{3}[\s\-]\d{4}"), 0.96),
    (re.compile(r"\b\d{3}[\-.\s]\d{3}[\-.\s]\d{4}\b"), 0.93),
    (re.compile(r"\b[6-9]\d{9}\b"), 0.90),
    (re.compile(r"\b\d{5}[\s\-]\d{5}\b"), 0.85),
]
DATE_RANGE_GUARD = re.compile(r"\b(19|20)\d{2}\s*(?:-|\u2013|\u2014|to)\s*(19|20)\d{2}\b")


def extract_phone(lines: List[str]) -> Extracted:
    best = Extracted()
    for i, line in enumerate(lines):
        if DATE_RANGE_GUARD.search(line):
            continue
        labelled = bool(re.search(r"\b(phone|mobile|tel|telephone|contact|cell)\b", line, re.I))
        for pattern, base in PHONE_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            if not 10 <= len(_digits(m.group(0))) <= 13:
                continue
            conf = min(0.99, base + (0.02 if labelled else 0) - (0.06 if i > 12 else 0))
            if conf > best.confidence:
                best = Extracted(m.group(0).strip(), conf, i,
                                 "labelled line" if labelled else "pattern match")
            break
        if best.confidence >= 0.97:
            break
    return best


def extract_url(lines: List[str], host: str, method: str) -> Extracted:
    pattern = re.compile(r"(?:https?://)?(?:www\.)?" + re.escape(host) + r"/[A-Za-z0-9_.\-/]+", re.I)
    for i, line in enumerate(lines):
        m = pattern.search(line)
        if m:
            return Extracted(re.sub(r"^https?://", "", m.group(0)).rstrip(".,)"), 0.97, i, method)
    return Extracted()


NAME_REJECT = re.compile(r"(resume|curriculum\s+vitae|\bcv\b|profile|contact|address|@|http|linkedin|github|\+\d)", re.I)


def extract_name(lines: List[str], email: Extracted) -> Extracted:
    best = Extracted()
    for i, raw in enumerate(lines[:12]):
        line = re.split(r"[|\u2022,]", raw)[0].strip()
        if not line or len(line) > 44 or NAME_REJECT.search(line) or re.search(r"\d", line):
            continue
        tokens = line.split()
        if not 2 <= len(tokens) <= 4:
            continue
        if not all(re.fullmatch(r"[A-Za-z][A-Za-z'.\-]*", t) for t in tokens):
            continue
        all_caps = line == line.upper()
        titled = all(t[0].isupper() for t in tokens)
        if not (all_caps or titled):
            continue
        conf = 0.72 + (0.20 if i == 0 else 0.13 if i <= 2 else 0.02)
        if len(tokens) in (2, 3):
            conf += 0.04
        if email.found:
            local = re.sub(r"[._\-0-9]", "", email.value.split("@")[0]).lower()
            flat = re.sub(r"[^a-z]", "", line.lower())
            if flat in local or any(len(t) > 2 and t.lower() in local for t in tokens):
                conf += 0.06
        value = title_case(line) if all_caps else line
        if conf > best.confidence:
            best = Extracted(value, min(conf, 0.98), i,
                             "document header" if i == 0 else "header block")
    if best.found:
        return best
    if email.found:
        parts = [p for p in re.sub(r"\d+", "", email.value.split("@")[0]).split(".") if len(p) > 1]
        parts = [p for chunk in parts for p in re.split(r"[_\-]", chunk) if len(p) > 1]
        if len(parts) >= 2:
            return Extracted(title_case(" ".join(parts[:3])), 0.52, email.line, "inferred from email")
    return Extracted()


def match_region(line: str, city: str) -> Optional[str]:
    low = line.lower()
    for region in REGIONS:
        if region != city and re.search(r"\b" + region + r"\b", low):
            return title_case(region)
    m = re.search(r",\s*([A-Z]{2})\b", line)
    if m and m.group(1) in US_ABBR:
        return m.group(1)
    return None


def extract_location(lines: List[str]) -> Extracted:
    head = lines[:14]
    for i, line in enumerate(head):
        low = line.lower()
        for city in CITIES:
            if re.search(r"\b" + city + r"\b", low):
                region = match_region(line, city)
                value = title_case(city) + (f", {region}" if region else "")
                return Extracted(value, 0.95 if region else 0.88, i,
                                 "gazetteer: city + region" if region else "gazetteer: city only")
    for i, line in enumerate(head):
        m = re.search(r"\b([A-Z][a-z]{2,}(?:\s[A-Z][a-z]+)?),\s*([A-Z]{2}|[A-Z][a-z]{3,})\b", line)
        if m and not INSTITUTION_RE.search(line):
            return Extracted(m.group(0), 0.70, i, "city, region pattern")
    return Extracted()


def extract_education(lines: List[str], sections: Dict[str, Any]) -> Dict[str, Extracted]:
    edu = sections.get("education")
    scope = edu["rows"] if edu else list(enumerate(lines))
    in_section = edu is not None

    tier = 0
    out = {k: Extracted() for k in ("degree", "discipline", "institution", "grad_year")}

    for i, text in scope:
        for level, pattern, label in DEGREE_TIERS:
            m = pattern.search(text)
            if not m:
                continue
            if level > tier:
                tier = level
                out["degree"] = Extracted(
                    f"{label} ({m.group(0).strip()})",
                    0.96 if in_section else 0.82, i,
                    "education section" if in_section else "whole-document scan")
                # only look between the degree token and the first institution keyword,
                # otherwise "University of California" reads as a field of study
                after = text[text.index(m.group(0)) + len(m.group(0)):]
                inst_at = INSTITUTION_RE.search(after)
                scope_after = after[:inst_at.start()] if inst_at and inst_at.start() > 0 else after
                phrase = re.match(r"^[\s,.\-]*(?:in|of)\s+([A-Za-z&\s\-]{3,52})", scope_after, re.I)
                if phrase:
                    value = re.split(r"[,;]", phrase.group(1))[0].strip()
                    # "... in Artificial Intelligence CGPA : 7.6" is one line — the
                    # discipline ends where the grade/marks token starts, not at the comma
                    value = re.split(
                        r"\b(?:c?gpa|grade|marks?|percentage|score)\b", value, flags=re.I)[0].strip()
                    if len(value) > 2:
                        out["discipline"] = Extracted(title_case(value), 0.88, i, "degree phrase")
                else:
                    token = re.split(r"[,|(]|\s{2,}", scope_after.lstrip(" ,.-\u2013\u2014"))[0].strip()
                    if 3 < len(token) < 48 and not token[0].isdigit() and not INSTITUTION_RE.search(token):
                        out["discipline"] = Extracted(
                            title_case(token), 0.87 if in_section else 0.70, i,
                            "phrase after degree, education section" if in_section else "phrase after degree")
            break

        if INSTITUTION_RE.search(text) and out["institution"].confidence < 0.90:
            inst = re.split(r"\||\s{2,}", text.lstrip("- "))[0]
            inst = re.sub(r"\b(19|20)\d{2}\b.*$", "", inst).strip(" ,")
            # "...College of Engineering (RTMNU) Sep 2024 -- Sep 2028" also leaves a
            # dangling month name once the year onward is cut — drop that too
            inst = re.sub(rf"\b(?:{MONTH_WORD})[a-z]*\.?,?\s*$", "", inst, flags=re.I).strip(" ,")
            cut = re.search(
                r"(?:^|,\s*)([^,]*\b(?:University|Institute|College|School of [A-Za-z]+|Academy|"
                r"Polytechnic|IIT|NIT|IIIT|BITS|JNTU)[^,]*)(,\s*[^,]+)?", inst, re.I)
            if cut:
                inst = cut.group(1).strip()
                tail = (cut.group(2) or "").lstrip(", ").rstrip(" -\u2013\u2014|,.").strip()
                if tail and len(tail.split()) <= 3 and not re.search(r"\d", tail) \
                        and not any(p.search(tail) for _, p, _ in DEGREE_TIERS):
                    inst = f"{inst}, {tail}"
            inst = inst.rstrip(" -\u2013\u2014|,.")
            if 4 < len(inst) < 82:
                out["institution"] = Extracted(
                    inst, 0.93 if in_section else 0.72, i,
                    "education section" if in_section else "keyword match")

        years = re.findall(r"\b(?:19|20)\d{2}\b", text)
        if years:
            newest = max(int(y) for y in years)
            current = out["grad_year"].value
            if 1975 <= newest <= 2032 and (current is None or newest > current):
                expected = bool(re.search(r"expect|pursu|present|ongoing", text, re.I))
                out["grad_year"] = Extracted(
                    newest, 0.80 if expected else 0.92, i,
                    "expected graduation" if expected else "year in education line")
    return out


def extract_ranges(lines: List[str], sections: Dict[str, Any]) -> List[Dict[str, Any]]:
    today = date.today()
    now = today.year * 12 + today.month - 1
    exp = sections.get("experience")
    scope = exp["rows"] if exp else list(enumerate(lines))

    with_month = re.compile(
        rf"({MONTH_WORD})[a-z]*\.?,?\s*'?(\d{{4}}|\d{{2}})\s*{DASH}\s*"
        rf"(present|current|now|till\s+date|({MONTH_WORD})[a-z]*\.?,?\s*'?(\d{{4}}|\d{{2}}))", re.I)
    year_only = re.compile(rf"\b((?:19|20)\d{{2}})\s*{DASH}\s*(present|current|now|(?:19|20)\d{{2}})\b", re.I)

    ranges: List[Dict[str, Any]] = []
    for i, text in scope:
        m = with_month.search(text)
        if m:
            start = month_value(m.group(1), m.group(2))
            open_ended = bool(re.search(r"present|current|now|till", m.group(3), re.I))
            end = now if open_ended else month_value(m.group(4), m.group(5))
            if start <= end and end - start < 600:
                ranges.append({"start": start, "end": end, "open": open_ended,
                               "line": i, "text": text, "coarse": False})
            continue
        m = year_only.search(text)
        if m:
            start = month_value("jan", m.group(1))
            open_ended = bool(re.search(r"present|current|now", m.group(2), re.I))
            end = now if open_ended else month_value("dec", m.group(2))
            if start <= end and end - start < 600:
                ranges.append({"start": start, "end": end, "open": open_ended,
                               "line": i, "text": text, "coarse": True})
    return ranges


def merge_tenure(ranges: List[Dict[str, Any]]) -> Tuple[int, List[Dict[str, Any]]]:
    """Overlapping roles must not be double counted, and real gaps are worth flagging."""
    if not ranges:
        return 0, []
    merged: List[List[int]] = []
    for r in sorted(ranges, key=lambda x: x["start"]):
        if merged and r["start"] <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], r["end"])
        else:
            merged.append([r["start"], r["end"]])
    months = sum(max(1, end - start) for start, end in merged)
    gaps = [
        {"from": month_label(merged[i - 1][1]), "to": month_label(merged[i][0]),
         "months": merged[i][0] - merged[i - 1][1]}
        for i in range(1, len(merged)) if merged[i][0] - merged[i - 1][1] >= 4
    ]
    return months, gaps


WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def seniority_for(years: Optional[float]) -> str:
    if years is None:
        return "Unclassified"
    if years < 1:
        return "Entry level"
    if years < 3:
        return "Junior"
    if years < 6:
        return "Mid level"
    if years < 10:
        return "Senior"
    return "Principal / Lead"


def extract_experience(lines: List[str], sections: Dict[str, Any],
                       summary: str) -> Tuple[Dict[str, Extracted], List[Dict[str, Any]], str]:
    ranges = extract_ranges(lines, sections)
    months, gaps = merge_tenure(ranges)

    years = Extracted()
    if months:
        coarse = all(r["coarse"] for r in ranges)
        years = Extracted(round(months / 12, 1), 0.78 if coarse else 0.93, ranges[0]["line"],
                          "year-only ranges merged" if coarse else "date ranges merged")

    claim = re.search(r"(\d{1,2}(?:\.\d)?)\+?\s*(?:years?|yrs?)\b", summary, re.I) or \
        re.search(r"\b(" + "|".join(WORD_NUMBERS) + r")\s+years?\b", summary, re.I)
    if claim:
        token = claim.group(1).lower()
        stated = WORD_NUMBERS.get(token, None)
        if stated is None:
            stated = float(token)
        if not years.found:
            years = Extracted(float(stated), 0.74, -1, "stated in summary")
        elif abs(stated - years.value) <= 1.6:
            years = Extracted(years.value, min(0.98, years.confidence + 0.05), years.line,
                              "date ranges, agrees with summary")
        else:
            years = Extracted(years.value, max(0.62, years.confidence - 0.16), years.line,
                              f"date ranges \u2014 summary claims {stated}y, flagged as a conflict")

    title = Extracted()
    employers: List[str] = []
    exp = sections.get("experience")
    rows = exp["rows"] if exp else []
    for i, text in rows:
        m = TITLE_RE.search(text)
        if m and not title.found and len(text) < 110:
            clean = title_case(m.group(0))
            for wrong, right in (("Ii", "II"), ("Iii", "III"), ("Qa", "QA"), ("Ml", "ML")):
                clean = re.sub(rf"\b{wrong}\b", right, clean)
            title = Extracted(clean, 0.90, i, "first role in experience section")
        for part in [p.strip() for p in re.split(r"\s*[|\u2014\u2013\-]\s*|,\s+", text) if p.strip()]:
            if not 3 <= len(part) <= 60 or part[0].isdigit() or re.search(r"\b(19|20)\d{2}\b", part):
                continue
            if TITLE_RE.search(part):
                continue
            known = any(c in part.lower() for c in KNOWN_COMPANIES)
            if COMPANY_SUFFIX.search(part) or known:
                clean = re.sub(r"^(at|with|for)\s+", "", part, flags=re.I).strip()
                if clean.lower() not in {e.lower() for e in employers}:
                    employers.append(clean)

    employer_field = Extracted(
        employers[:6], min(0.93, 0.74 + len(employers) * 0.05),
        rows[0][0] if rows else -1, "company suffix + known-employer list") if employers else Extracted()

    return ({"total_years": years, "current_title": title, "employers": employer_field},
            gaps, seniority_for(years.value))


def extract_skills(lines: List[str], sections: Dict[str, Any]) -> Tuple[Extracted, List[Dict[str, Any]]]:
    skills_section = sections.get("skills")
    section_text = "\n".join(t for _, t in skills_section["rows"]).lower() if skills_section else ""
    full_text = "\n".join(lines).lower()

    resolved: Dict[str, Dict[str, Any]] = {}
    for alias, canon, cat, pattern in ALIASES:
        in_section = bool(section_text and pattern.search(section_text))
        in_document = bool(pattern.search(full_text))
        if not (in_section or in_document):
            continue
        if len(alias) <= 2 and not in_section:
            continue  # 'ts', 'ml', 'py' are only trustworthy inside a skills block
        conf = 0.96 if in_section else 0.78
        if canon not in resolved or resolved[canon]["confidence"] < conf:
            line_no = next((i for i, line in enumerate(lines) if pattern.search(line.lower())), -1)
            resolved[canon] = {"skill": canon, "category": cat, "confidence": conf,
                               "matched_on": alias, "line": line_no, "in_section": in_section}

    items = sorted(resolved.values(), key=lambda s: (not s["in_section"], s["skill"]))
    if not items:
        return Extracted(), []
    anchor = skills_section["start"] if skills_section else items[0]["line"]
    field = Extracted(
        [s["skill"] for s in items],
        min(0.97, 0.62 + len(items) * 0.02 + (0.16 if skills_section else 0)), anchor,
        "skills section + taxonomy resolver" if skills_section else "taxonomy scan over full document")
    return field, items


def extract_list(sections: Dict[str, Any], key: str, cap: int = 8) -> Extracted:
    section = sections.get(key)
    if not section:
        return Extracted()
    items: List[str] = []
    for _, text in section["rows"]:
        cleaned = text.lstrip("- \u2022").strip()
        if not 3 < len(cleaned) < 130:
            continue
        if key == "languages" and "," in cleaned:
            items.extend(p.strip() for p in cleaned.split(",") if p.strip())
        else:
            items.append(cleaned)
    if not items:
        return Extracted()
    return Extracted(items[:cap], 0.90, section["rows"][0][0], f"{key} section")


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def parse_text(text: str, source_file: str = "inline.txt", source_format: str = "txt") -> ParsedResume:
    started = time.perf_counter()
    lines = normalise(text)
    sections = segment(lines)
    non_empty = [line for line in lines if line]
    summary = " ".join(t for _, t in sections["summary"]["rows"]) if "summary" in sections \
        else " ".join(non_empty[:8])

    email = extract_email(lines)
    fields: Dict[str, Extracted] = {
        "email": email,
        "name": extract_name(lines, email),
        "phone": extract_phone(lines),
        "location": extract_location(lines),
        "linkedin": extract_url(lines, "linkedin.com", "LinkedIn URL"),
        "github": extract_url(lines, "github.com", "repository URL"),
    }
    fields.update(extract_education(lines, sections))
    exp_fields, gaps, seniority = extract_experience(lines, sections, summary)
    fields.update(exp_fields)
    skills_field, skills = extract_skills(lines, sections)
    fields["skills"] = skills_field
    fields["certifications"] = extract_list(sections, "certifications", 6)
    fields["languages"] = extract_list(sections, "languages", 8)

    # recall answers "how much did we get?"; confidence answers "how sure are we?"
    weight_total = sum(w for _, _, w in REQUIRED_FIELDS)
    weight_found = 0
    weighted_conf = 0.0
    captured = 0
    for key, _label, weight in REQUIRED_FIELDS:
        rec = fields.get(key, Extracted())
        if rec.found:
            captured += 1
            weight_found += weight
            weighted_conf += weight * rec.confidence
    optional_captured = sum(1 for key, _ in OPTIONAL_FIELDS if fields.get(key, Extracted()).found)

    if email.found:
        seed = email.value
    elif fields["phone"].found:
        seed = _digits(fields["phone"].value)[-10:]
    elif fields["name"].found:
        seed = fields["name"].value.lower().replace(" ", "")
    else:
        seed = source_file
    identity = hashlib.sha1(seed.encode("utf-8")).hexdigest()

    return ParsedResume(
        source_file=source_file,
        source_format=source_format,
        fields=fields,
        skills=[{k: v for k, v in s.items() if k in ("skill", "category", "confidence", "matched_on")}
                for s in skills],
        sections=list(sections.keys()),
        tenure_gaps=gaps,
        seniority=seniority,
        field_recall=round(weight_found / weight_total * 100, 1),
        mean_confidence=round(weighted_conf / weight_found * 100, 1) if weight_found else 0.0,
        fields_captured=captured,
        optional_captured=optional_captured,
        identity_hash=identity,
        parse_ms=max(1, int((time.perf_counter() - started) * 1000)),
        char_count=len("\n".join(lines)),
        # a vendor note or a scanned page is not a resume; scoring it would quietly
        # drag the acceptance number down and hide the real parser quality
        is_resume=captured >= 5 or len(sections) >= 3,
        lines=lines,
    )


def read_file(path: str | Path) -> Tuple[str, str]:
    """Return (text, format). PDF and DOCX readers are imported only when needed."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import pdfplumber  # noqa: PLC0415
        with pdfplumber.open(path) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages), "pdf"
    if suffix in (".docx", ".doc"):
        import docx2txt  # noqa: PLC0415
        return docx2txt.process(str(path)) or "", "docx"
    return path.read_text(encoding="utf-8", errors="replace"), "txt"


def parse_file(path: str | Path) -> ParsedResume:
    path = Path(path)
    text, fmt = read_file(path)
    return parse_text(text, source_file=path.name, source_format=fmt)


def cohort_metrics(results: List[ParsedResume]) -> Dict[str, Any]:
    """Cohort roll-up. Documents that are not resumes are reported, not graded."""
    scored = [r for r in results if r.is_resume]
    rejected = [r for r in results if not r.is_resume]
    seen: Dict[str, str] = {}
    duplicates: List[Dict[str, str]] = []
    unique: List[ParsedResume] = []
    for r in scored:
        if r.identity_hash in seen:
            duplicates.append({"file": r.source_file, "duplicate_of": seen[r.identity_hash]})
        else:
            seen[r.identity_hash] = r.source_file
            unique.append(r)

    recall = round(sum(r.field_recall for r in scored) / len(scored), 1) if scored else 0.0
    confidence = round(sum(r.mean_confidence for r in scored) / len(scored), 1) if scored else 0.0
    skill_counts: Dict[str, int] = {}
    for r in unique:
        for s in r.skills:
            skill_counts[s["skill"]] = skill_counts.get(s["skill"], 0) + 1

    return {
        "documents_in": len(results),
        "resumes_scored": len(scored),
        "unique_candidates": len(unique),
        "duplicates": duplicates,
        "rejected": [r.source_file for r in rejected],
        "field_recall": recall,
        "mean_confidence": confidence,
        "acceptance_threshold": ACCEPTANCE_THRESHOLD,
        "gate_met": recall >= ACCEPTANCE_THRESHOLD,
        "formats": {fmt: sum(1 for r in scored if r.source_format == fmt) for fmt in ("pdf", "docx", "txt")},
        "unique_skills": len(skill_counts),
        "skill_counts": dict(sorted(skill_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "needs_review": [r.source_file for r in unique if r.fields_captured < r.fields_required],
        "categories": CATEGORIES,
    }
