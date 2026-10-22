"""Payee names from bank texts (F09): "Einkauf Debitkarte 12.10.2026 MIGROS ZUERICH HB 1234" → "Migros".

Deterministic and local. `clean_words` drops what changes from one booking to the next (dates, times, card,
reference, terminal and phone numbers, IBANs, postal codes) and the booking words Swiss banks put around the
merchant (UBS, PostFinance, ZKB, Raiffeisen, Revolut, TWINT: "Einkauf", "Kauf/Dienstleistung", "Zahlung",
"Debit", "Karte", "TWINT *", "SumUp *", "POS", "Achat", "Paiement", "Card payment" ...). `normalize` then names
a known brand (`resources/payees.json`) or keeps the remaining words without the location and legal form, in
sensible casing. `alias_key` is the folded cleaned text, which the payee table stores so that a renamed or
merged payee keeps its bank texts (services/payees.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cache

from budget_app.config import RESOURCES_DIR
from budget_app.domain.coicop import fold

PAYEES_FILE = RESOURCES_DIR / "payees.json"

# ------------------------------------------------------------------ what changes from booking to booking
_NOISE_PATTERNS = [
    r"\bCH\d{2}(?:\s?[0-9A-Z]{4}){4}\s?[0-9A-Z]{1,2}\b",  # IBAN
    r"\+\d{2}[\d\s]{8,}\d",  # phone number (TWINT to a person)
    r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b",  # 12.10.2026, 12/10/26
    r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+)?\b",  # 2026-10-12
    r"\b\d{1,2}/\d{4}\b",  # 10/2026
    r"\b\d{1,2}[:.h]\d{2}(?::\d{2})?\s*(?:uhr|h)?\b",  # 14:23, 14.23 Uhr
    # card numbers: "Karte Nr. 1234", "Kartennummer: XXXX 1234", "No de carte XXXX1234", "card number *1234"
    r"(?:karten?|card|carte|carta)\s*(?:-?\s*(?:nr|nummer|no|number|n°|numero|numéro)\.?)?\s*[:.]?\s*[x*•\d][x*•\d\s-]{2,}",
    r"(?:nr|no|n°|numero|numéro|number)\.?\s*(?:de|di)?\s*(?:carte|carta|card)\s*[:.]?\s*[x*•\d][x*•\d\s-]{2,}",
    r"[x*•]{2,}\s?\d{2,}",  # XXXX1234, ****1234
    r"\*\d{3,}",  # *1234
    # references and terminals: "Ref. 12345", "Referenz: AB12-34", "Terminal 0815", "Trx-ID 99"
    r"\b(?:ref|referenz|reference|référence|riferimento|trx|transaktion|transaction|terminal|term|beleg|"
    r"auftrag|order|id)\b\.?\s*(?:-?\s*(?:nr|no|id)\.?)?\s*[:#]?\s*[\w-]*\d[\w-]*",
    r"\b(?:CH|LI|D|F|I|A)-\d{4,5}\b",  # CH-8001
]
_NOISE_RE = re.compile("|".join(f"(?:{p})" for p in _NOISE_PATTERNS), re.IGNORECASE)

# payment services in front of the merchant: "TWINT *Brezelkoenig", "SumUp *Cafe", "PAYPAL *NETFLIX"
_PROVIDERS = {
    "twint": "TWINT",
    "sumup": "SumUp",
    "sum up": "SumUp",
    "sq": "Square",
    "paypal": "PayPal",
    "zettle": "Zettle",
    "izettle": "Zettle",
    "ztl": "Zettle",
    "sp": "Shopify",
    "stripe": "Stripe",
    "payrexx": "Payrexx",
    "wallee": "wallee",
    "mollie": "Mollie",
    "klarna": "Klarna",
    "google": "Google",
    "crv": "Curve",
    "curve": "Curve",
}
_PROVIDER_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in sorted(_PROVIDERS, key=len, reverse=True)) + r")\s*\*\s*", re.IGNORECASE
)
# the card a bank issued: "ZKB Visa Debit", "UBS Debit Mastercard", "PostFinance Card"
_ISSUER_RE = re.compile(
    r"\b(?:zkb|ubs|raiffeisen|postfinance|bcv|bcge|bkb|lukb|sgkb|blkb|tkb|glkb|valiant|migros\s+bank|cs|clientis|"
    r"cembra|viseca|swisscard|revolut|neon|yuh)\s+(?:visa|maestro|mastercard|debit|debitkarte|karte|card|"
    r"v\s?pay|carte|kreditkarte|prepaid)\b",
    re.IGNORECASE,
)


def _phrases(text: str) -> frozenset[str]:
    """A comma-separated list of key phrases."""
    return frozenset(p.strip() for p in text.split(",") if p.strip())


# booking words, removed at the start (several in a row): "Einkauf", "Kauf/Dienstleistung vom", "Paiement à"
_START = _phrases(
    # German
    "einkauf, kauf, dienstleistung, kauf dienstleistung, zahlung, zahlungen, kartenzahlung, bezahlung, lastschrift,"
    "lsv, lsv+, dauerauftrag, e banking, e banking auftrag, ebanking, auftrag, ebill, e bill, e rechnung, gutschrift,"
    "vergutung, uberweisung, belastung, bezug, online, online einkauf, einzahlung,"
    # French
    "achat, achat service, achat en ligne, service, paiement, paiements, virement, ordre permanent, prelevement,"
    "recouvrement, debit direct, versement,"
    # Italian
    "acquisto, acquisto online, pagamento, addebito, accredito, bonifico, ordine permanente, versamento,"
    # English (Revolut and English e-banking)
    "purchase, pos purchase, card payment, card purchase, payment, transfer, direct debit, standing order,"
    "online payment, to, from, contactless payment"
)
# words between a booking word and the merchant: "Zahlung an", "Paiement à", "Card payment to"
_CONNECTORS = _phrases(
    "an, von, vom, am, bei, fur, zu, a, au, aux, de, du, des, le, la, par, chez, pour, da, di, del, presso, per,"
    "to, from, at, for, by, with"
)
# words that never name the merchant, removed wherever they stand: cards, purposes, currencies, months
_NOISE_WORDS = _phrases(
    "karte, karten, kartennummer, kartennr, debitkarte, kreditkarte, maestro, mastercard, visa, vpay, v pay, pos,"
    "nr, no, n°, twint, sumup, contactless, kontaktlos, nfc, carte, carta, card, debit, debito, debitcard,"
    "apple pay, google pay, samsung pay, rechnung, invoice, facture, fattura, miete, loyer, affitto, lohn, salaire,"
    "salario, salary, chf, eur, usd, gbp, betrag, montant, importo, amount, esr, qr, qr rechnung, bvr,"
    "januar, februar, marz, april, mai, juni, juli, august, september, oktober, november, dezember,"
    "janvier, fevrier, mars, avril, juin, juillet, aout, septembre, octobre, novembre, decembre,"
    "gennaio, febbraio, marzo, aprile, maggio, giugno, luglio, agosto, settembre, ottobre, dicembre,"
    "january, february, march, may, june, july, october, december"
)
# what follows the merchant: its location and country ...
_LOCATION = _phrases(
    "zurich, zuerich, zuri, zh, bern, berne, be, basel, bale, bs, geneve, genf, geneva, ge, lausanne, luzern,"
    "lucerne, lugano, winterthur, st gallen, sankt gallen, st gall, biel, bienne, biel bienne, thun, zug, chur,"
    "fribourg, freiburg, neuchatel, schaffhausen, aarau, baden, olten, solothurn, sion, sitten, wil, uster,"
    "dubendorf, kloten, emmen, kriens, yverdon, montreux, vevey, nyon, locarno, bellinzona, davos, st moritz,"
    "interlaken, rapperswil, frauenfeld, wetzikon, dietikon, horgen, oerlikon, altstetten, spreitenbach,"
    "glattzentrum, sihlcity, hb, bhf, bahnhof, flughafen, airport, gare, stazione, cornavin,"
    "ch, che, li, de, fr, it, at, gb, us, nl, ie, lu, schweiz, suisse, svizzera, switzerland"
)
# ... and its legal form
_LEGAL = _phrases(
    "ag, gmbh, sa, sarl, s a, s a r l, ltd, limited, inc, llc, kg, se, bv, b v, plc, co, & co, cie, & cie,"
    "genossenschaft, sagl, spa, srl, oy, ab, as"
)
# written in lower case inside a name: "Restaurant zum Hirschen", "Café du Nord"
_SMALL_WORDS = _phrases(
    "zum, zur, am, im, an, der, die, das, und, von, de, du, des, la, le, les, et, and, of, the, di, del, della, e,"
    "en, au, aux"
)
_LEADING = _START | _NOISE_WORDS
_TAIL = _LOCATION | _LEGAL
_NOT_ACRONYMS = _phrases("dr, st, str, mr, mrs, nr, ms")
_DOMAIN_RE = re.compile(r"\.(?:ch|com|de|net|org|io|li|fr|it|eu|at|co\.uk|swiss)$", re.IGNORECASE)
_SPLIT_RE = re.compile(r"[\s/,;:()\[\]{}\"|·<>_=!?]+")
_KEY_RE = re.compile(r"[^\w&+]+")


def key(text: str) -> str:
    """`text` as a lookup key: folded (case and accents ignored), punctuation as spaces ("Coop-City" = "coop city")."""
    return " ".join(_KEY_RE.sub(" ", fold(text)).replace("_", " ").split())


# ------------------------------------------------------------------ brands
@dataclass(frozen=True)
class Brand:
    name: str
    pattern: tuple[str, ...]  # key words
    start_only: bool = False  # matches only at the start ("avec" is also a French word)
    weak: bool = False  # banks and payment services: only when no other brand matches


@cache
def _resources() -> tuple[list[Brand], frozenset[str]]:
    raw = json.loads(PAYEES_FILE.read_text(encoding="utf-8"))
    brands = []
    for section, weak in (("brands", False), ("weak_brands", True)):
        for name, patterns in raw[section].items():
            for pattern in patterns:
                start_only = pattern.startswith("^")
                brands.append(Brand(name, tuple(key(pattern.lstrip("^")).split()), start_only, weak))
    acronyms = frozenset(a.upper() for a in raw["acronyms"])
    return brands, acronyms


def brand(words: list[str]) -> str:
    """The brand named by `words` (key words): the longest pattern, else the earliest; weak brands last."""
    brands, _ = _resources()
    found: list[tuple[bool, int, int, str]] = []
    for b in brands:
        n = len(b.pattern)
        starts = [0] if b.start_only else range(len(words) - n + 1)
        for i in starts:
            if tuple(words[i : i + n]) == b.pattern:
                found.append((b.weak, -n, i, b.name))
                break
    return min(found)[3] if found else ""


# ------------------------------------------------------------------ cleaning
@dataclass
class Cleaned:
    words: list[str]  # the merchant's words as written, without booking words and numbers
    provider: str = ""  # the payment service in front of it ("TWINT"), if any


def _strip_token(token: str) -> str:
    token = token.strip(".-'*+#&%$€")
    token = re.sub(r"-\d+$", "", token)  # Coop-1234
    return _DOMAIN_RE.sub("", token)


def _is_reference(token: str) -> bool:
    """Numbers and codes such as 1234, 8001, AB12CD345: they change from one booking to the next."""
    digits = sum(c.isdigit() for c in token)
    return digits > 0 and (digits == len(token) or digits >= 3 or not any(c.isalpha() for c in token))


def _phrase_at(keys: list[str], i: int, phrases: frozenset[str]) -> int:
    """The number of words of the longest phrase in `phrases` that starts at keys[i] (0: none)."""
    for n in (3, 2, 1):
        if i + n <= len(keys) and " ".join(keys[i : i + n]) in phrases:
            return n
    return 0


def clean_words(raw: str) -> Cleaned:
    """The words of a bank text that name the merchant (and its location), as written."""
    text = _ISSUER_RE.sub(" ", _NOISE_RE.sub(" ", raw or ""))
    provider = ""
    if m := _PROVIDER_RE.search(text):
        provider = _PROVIDERS[" ".join(m.group(1).lower().split())]
        text = text[: m.start()] + " " + text[m.end() :]
    text = text.replace("*", " ")
    words = [w for w in (_strip_token(tok) for tok in _SPLIT_RE.split(text)) if w and not _is_reference(w)]
    keys = [key(w) for w in words]
    # booking words at the start, with the connectors that follow them; noise words anywhere
    i, after_booking = 0, False
    while i < len(words):
        n = _phrase_at(keys, i, _LEADING)
        if n:
            i, after_booking = i + n, True
        elif after_booking and keys[i] in _CONNECTORS:
            i += 1
        else:
            break
    out: list[str] = []
    j = i
    while j < len(words):
        n = _phrase_at(keys, j, _NOISE_WORDS)
        if n:
            j += n
            continue
        if keys[j]:
            out.append(words[j])
        j += 1
    while out and key(out[-1]) in _CONNECTORS:  # "Top-Up by"
        out.pop()
    return Cleaned(out, provider)


def alias_key(raw: str) -> str:
    """The key a bank text is remembered by: its cleaned words, folded. Empty when nothing is left."""
    return key(" ".join(clean_words(raw).words))


# ------------------------------------------------------------------ the name
def _strip_tail(words: list[str]) -> list[str]:
    """Without the location, country and legal form at the end ("Restaurant Zum Hirschen Luzern AG")."""
    out = list(words)
    while len(out) > 1:
        keys = [key(w) for w in out]
        for n in (3, 2, 1):
            if len(out) > n and " ".join(keys[-n:]) in _TAIL:
                del out[-n:]
                break
        else:
            break
    return out


def _case(words: list[str]) -> list[str]:
    """Bank texts in capitals get title case; acronyms (SBB, IKEA) and mixed case stay as written."""
    _, acronyms = _resources()
    if any(c.islower() for w in words for c in w) and any(c.isupper() for w in words for c in w):
        return words
    out = []
    for i, w in enumerate(words):
        upper = w.upper()
        short = 2 <= len(w) <= 4 and not re.search(r"[AEIOUYÄÖÜ]", upper) and w.lower() not in _NOT_ACRONYMS
        if upper in acronyms or short or any(c.isdigit() for c in w):
            out.append(upper)
        elif i > 0 and w.lower() in _SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append("-".join(part[:1].upper() + part[1:].lower() for part in w.split("-")))
    return out


def normalize(raw: str) -> str:
    """The payee name for a bank text, "" when it names none. The same text always gives the same name."""
    cleaned = clean_words(raw)
    keys = key(" ".join(cleaned.words)).split()
    if name := brand(keys):
        return name
    words = _strip_tail(cleaned.words)
    if not words:  # only booking words: "TWINT: Zahlung an +41 79 …" is a TWINT payment
        return cleaned.provider or brand(key(raw).split())
    return " ".join(_case(words))
