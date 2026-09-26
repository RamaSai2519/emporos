"""Insider (SEBI PIT), promoter and substantial-holder (SAST Reg 29) disclosures from NSE's public
feeds, as structured rows (EM-244, driver-atlas-plan §3.2 item 4).

Two feeds, one request per calendar month: `corporates-pit` (Reg 7 trades and pledges by promoters,
directors, designated employees and their relatives) and `corporate-sast-reg29` (a holder crossing
a threshold, a change of 2% or more). A row is available when the exchange DISSEMINATED it (the
feed's own timestamp, IST), never when the trade happened: a PIT filing is due up to two trading
days after the trade, so `trade_from` is earlier than `published_at`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from emporos.core.clock import IST
from emporos.research.cause_ledger.pages import PageSpec

__all__ = [
    "PIT_URL", "SAST_URL", "InsiderTrade", "SastDisclosure", "monthly_pages", "parse_pit",
    "parse_sast",
]  # fmt: skip

PIT_URL = "https://www.nseindia.com/api/corporates-pit?index=equities&from_date={a}&to_date={b}"
SAST_URL = (
    "https://www.nseindia.com/api/corporate-sast-reg29?index=equities&from_date={a}&to_date={b}"
)
_DAY = "%d-%m-%Y"


@dataclass(frozen=True)
class InsiderTrade:
    symbol: str
    company: str
    person: str
    person_category: str  # Promoters, Promoter Group, Director, Employees/Designated Employees, ...
    transaction: str  # Buy, Sell, Pledge, Pledge Revoke, Pledge Invoke
    mode: str  # Market Purchase, Market Sale, ESOP, Pledge Creation, Off Market, ...
    security_type: str
    quantity: int  # securities acquired, disposed or pledged
    value: float  # rupees
    holding_before: int | None
    holding_after: int | None
    trade_from: date | None
    trade_to: date | None
    published_at: datetime  # exchange dissemination time, IST
    source_url: str  # the XBRL filing


@dataclass(frozen=True)
class SastDisclosure:
    symbol: str
    company: str
    acquirer: str
    is_promoter: bool
    regulation: str  # Reg29(1), Reg29(2)
    side: str  # Acquisition, Sale, Both
    mode: str
    shares_acquired: int | None
    shares_sold: int | None
    shares_after: int | None
    percent_acquired: float | None
    percent_after: float | None
    published_at: datetime  # exchange dissemination time, IST
    source_url: str


def monthly_pages(first: date, last: date) -> list[PageSpec]:
    """One PIT and one SAST page per calendar month touching [first, last] (a month is asked for
    whole; rows outside the span are dropped when read)."""
    pages: list[PageSpec] = []
    month = first.replace(day=1)
    while month <= last:
        end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        a, b = month.strftime(_DAY), end.strftime(_DAY)
        tag = month.strftime("%Y-%m")
        pages.append(PageSpec("nse-pit", f"pit/{tag}.json", PIT_URL.format(a=a, b=b)))
        pages.append(PageSpec("nse-sast", f"sast/{tag}.json", SAST_URL.format(a=a, b=b)))
        month = end + timedelta(days=1)
    return pages


def _int(value: object) -> int | None:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _stamp(value: object) -> datetime:
    """`07-Mar-2024 19:58` (IST), or the comma form `07-Mar-2024, 20-46` some rows carry."""
    text = str(value).strip()
    for form in ("%d-%b-%Y %H:%M", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y, %H-%M"):
        try:
            return datetime.strptime(text, form).replace(tzinfo=IST)
        except ValueError:
            continue
    raise ValueError(f"not a dissemination time: {value!r}")


def _day(value: object) -> date | None:
    try:
        return datetime.strptime(str(value).strip(), "%d-%b-%Y").date()
    except ValueError:
        return None


def parse_pit(body: bytes) -> list[InsiderTrade]:
    records = json.loads(body).get("data", [])
    out: list[InsiderTrade] = []
    for r in records:
        quantity = _int(r.get("secAcq"))
        value = _float(r.get("secVal"))
        if not r.get("symbol") or quantity is None or value is None:
            continue
        out.append(
            InsiderTrade(
                r["symbol"],
                str(r.get("company", "")),
                str(r.get("acqName", "")).strip(),
                str(r.get("personCategory", "")),
                str(r.get("tdpTransactionType", "")),
                str(r.get("acqMode", "")),
                str(r.get("secType", "")),
                quantity,
                value,
                _int(r.get("befAcqSharesNo")),
                _int(r.get("afterAcqSharesNo")),
                _day(r.get("acqfromDt")),
                _day(r.get("acqtoDt")),
                _stamp(r["date"]),
                str(r.get("xbrl") or ""),
            )  # fmt: skip
        )
    return out


def parse_sast(body: bytes) -> list[SastDisclosure]:
    records = json.loads(body).get("data", [])
    out: list[SastDisclosure] = []
    for r in records:
        if not r.get("symbol") or not r.get("timestamp"):
            continue
        out.append(
            SastDisclosure(
                r["symbol"],
                str(r.get("company", "")),
                str(r.get("acquirerName", "")).strip(),
                str(r.get("promoterType", "")).upper() == "Y",
                str(r.get("regType", "")),
                str(r.get("acqSaleType", "")),
                str(r.get("acquisitionMode", "")),
                _int(r.get("noOfShareAcq")),
                _int(r.get("noOfShareSale")),
                _int(r.get("noOfShareAft")),
                _float(r.get("totAcqShare")),
                _float(r.get("totAftShare")),
                _stamp(r["timestamp"]),
                str(r.get("attachement") or ""),
            )  # fmt: skip
        )
    return out
