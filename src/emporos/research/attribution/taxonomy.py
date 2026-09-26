"""The fixed driver taxonomy of the driver atlas (driver-atlas-plan §4.1, EM-245)."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Driver", "DRIVER_NAMES"]


class Driver(StrEnum):
    G1 = "G1"  # US equity overnight
    G2 = "G2"  # Fed / FOMC
    G3 = "G3"  # US data (CPI, payrolls)
    G4 = "G4"  # crude oil
    G5 = "G5"  # USD/INR
    G6 = "G6"  # global risk-off / Asia session
    M1 = "M1"  # RBI policy
    M2 = "M2"  # Budget or tax
    M3 = "M3"  # elections
    M4 = "M4"  # government or regulator action on a sector
    M5 = "M5"  # domestic data
    F1 = "F1"  # FII/DII flows
    F2 = "F2"  # index rebalancing or inclusion
    F3 = "F3"  # F&O expiry or rollover
    F4 = "F4"  # bulk or block deal
    F5 = "F5"  # promoter buy, sell or pledge
    F6 = "F6"  # new supply (QIP, OFS, IPO lock-in expiry)
    C1 = "C1"  # results
    C2 = "C2"  # guidance or con-call commentary
    C3 = "C3"  # orders or contracts
    C4 = "C4"  # M&A or restructuring
    C5 = "C5"  # capital actions (buyback, dividend, split, bonus)
    C6 = "C6"  # management change
    C7 = "C7"  # regulatory action, penalty or litigation
    C8 = "C8"  # rating change
    C9 = "C9"  # product approval
    C10 = "C10"  # governance or fraud allegation or short report
    P1 = "P1"  # group company news
    P2 = "P2"  # sector peer news
    P3 = "P3"  # sector-wide move without own news
    U = "U"  # unexplained

    @property
    def family(self) -> str:
        return self.value[0]


DRIVER_NAMES: dict[Driver, str] = {
    Driver.G1: "US equity overnight", Driver.G2: "Fed / FOMC", Driver.G3: "US data (CPI, payrolls)",
    Driver.G4: "crude oil", Driver.G5: "USD/INR", Driver.G6: "global risk-off / Asia session",
    Driver.M1: "RBI policy", Driver.M2: "Budget or tax", Driver.M3: "elections",
    Driver.M4: "government or regulator action on a sector", Driver.M5: "domestic data",
    Driver.F1: "FII/DII flows", Driver.F2: "index rebalancing or inclusion",
    Driver.F3: "F&O expiry or rollover", Driver.F4: "bulk or block deal",
    Driver.F5: "promoter buy, sell or pledge", Driver.F6: "new supply (QIP, OFS, lock-in expiry)",
    Driver.C1: "results", Driver.C2: "guidance or con-call commentary",
    Driver.C3: "orders or contracts", Driver.C4: "M&A or restructuring",
    Driver.C5: "capital actions (buyback, dividend, split, bonus)", Driver.C6: "management change",
    Driver.C7: "regulatory action, penalty or litigation", Driver.C8: "rating change",
    Driver.C9: "product approval", Driver.C10: "governance or fraud allegation or short report",
    Driver.P1: "group company news", Driver.P2: "sector peer news",
    Driver.P3: "sector-wide move without own news", Driver.U: "unexplained",
}  # fmt: skip
