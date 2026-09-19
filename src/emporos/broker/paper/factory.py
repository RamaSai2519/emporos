"""Wires a `PaperBroker` from its parts. This is composition, kept out of the broker so the broker
never builds its own collaborators: swap any policy here and nothing else changes.

The defaults are the stated assumptions of the simulation, not claims of realism:

* fills: a tick AT the limit is enough (`TouchCrossing`), at the order's own limit price;
* liquidity: we may take 50% of what traded since the previous tick, across all our orders;
* no orders are lost or rejected at random; the reject screen is validation + margin only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from emporos.broker.paper.account import PaperAccount
from emporos.broker.paper.broker import PaperBroker
from emporos.broker.paper.costs import CostModel
from emporos.broker.paper.exchange import RestoredOrder, SimulatedExchange
from emporos.broker.paper.faults import ReliableReplies, ReplyLoss
from emporos.broker.paper.fills import (
    AtLimitPrice,
    FillPolicy,
    LimitFillPolicy,
    LiquidityModel,
    ParticipationLiquidity,
    StopTrigger,
    TouchCrossing,
    TriggerRule,
)
from emporos.broker.paper.funds import LastPrices, PaperFunds
from emporos.broker.paper.journal import PaperJournal, RestoredSession
from emporos.broker.paper.market import MarketDataSource
from emporos.broker.paper.rejects import OrderScreen, default_rules
from emporos.broker.paper.session import PaperSessionKeeper
from emporos.core.clock import Clock
from emporos.core.ids import IdGenerator
from emporos.domain.money import Money


@dataclass(frozen=True)
class PaperBrokerConfig:
    client_code: str
    starting_cash: Money
    leverage: Decimal = Decimal(1)  # 1 = fully funded; >1 assumes broker intraday leverage
    latency: timedelta = timedelta(0)  # order -> exchange delay before it can trade
    session_lifetime: timedelta = timedelta(hours=12)


class PaperBrokerFactory:
    def __init__(
        self,
        config: PaperBrokerConfig,
        costs: CostModel,
        *,
        fill_policy: FillPolicy | None = None,
        liquidity: LiquidityModel | None = None,
        trigger: TriggerRule | None = None,
        screen: OrderScreen | None = None,
        replies: ReplyLoss | None = None,
    ) -> None:
        self._config = config
        self._costs = costs
        self._fill_policy = fill_policy or LimitFillPolicy(TouchCrossing(), AtLimitPrice())
        self._liquidity = liquidity or ParticipationLiquidity(Decimal("0.5"))
        self._trigger = trigger or StopTrigger()
        self._screen = screen or OrderScreen(default_rules())
        self._replies = replies or ReliableReplies()

    def build(
        self,
        source: MarketDataSource,
        journal: PaperJournal,
        clock: Clock,
        ids: IdGenerator,
        restored: RestoredSession | None = None,
    ) -> PaperBroker:
        """A broker for one session. Given the session as persisted, it resumes exactly where
        that left off: same orders on the book, same fills, same positions and cash."""
        config = self._config
        exchange = SimulatedExchange(
            self._fill_policy, self._liquidity, self._trigger, ids, config.latency
        )
        account = PaperAccount(config.starting_cash)
        if restored is not None:
            exchange.restore(list(restored.orders), clock.now())
            for fill in restored.fills:
                account.apply(fill.trade, fill.fees)
        prices = LastPrices()
        funds = PaperFunds(account, exchange, prices, config.leverage)
        sessions = PaperSessionKeeper(clock, config.client_code, config.session_lifetime)
        return PaperBroker(
            source, exchange, account, funds, prices, self._screen, self._costs, journal,
            self._replies, sessions, clock,
        )  # fmt: skip


__all__ = ["PaperBrokerConfig", "PaperBrokerFactory", "RestoredOrder"]
