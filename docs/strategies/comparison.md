# EM-114 final comparison: every strategy, one verdict

The closing artifact of EM-114. Every strategy that reached a curation, and every strategy
shipped in `config/strategies/`, with its recorded verdict. Live standings are the system of
record (`emporos backtest verdicts list`); this document reproduces them and links the evidence.

**Verdict: no strategy is validated. No strategy is enabled. Nothing is promoted to live.**

| strategy (config) | verdict | OOS trades | gross | charges | net P&L | profit factor | win rate | compounded | evidence |
|---|---|---|---|---|---|---|---|---|---|
| orb_v1 | **rejected** | 167 | -915.68 | 2,220.59 | -3,136.27 | 0.166 | 27.5% | -6.12% | benchmark_50k/orb_v1.md |
| vwap_reversion_v1 | **rejected** | 174 | -787.65 | 2,319.43 | -3,107.08 | 0.127 | 19.0% | -6.06% | benchmark_50k/vwap_reversion_v1.md |
| rsi_pullback_v1 | **rejected** | 171 | -864.94 | 2,273.74 | -3,138.68 | 0.045 | 3.5% | -6.12% | benchmark_50k/rsi_pullback_v1.md |
| vwap_trend_v1 | **rejected** | 161 | -1,017.92 | 2,137.96 | -3,155.88 | 0.177 | 14.9% | -6.15% | benchmark_50k/vwap_trend_v1.md |
| donchian_v1 | **rejected** | 173 | -730.93 | 2,302.33 | -3,033.26 | 0.188 | 21.4% | -5.92% | benchmark_50k/donchian_v1.md |
| ema_pullback_v1 | **rejected** | 155 | -1,247.61 | 2,064.94 | -3,312.55 | 0.115 | 12.3% | -6.45% | benchmark_50k/ema_pullback_v1.md |
| gap_go_v1 | **rejected** | 151 | -781.40 | 2,010.54 | -2,791.94 | 0.402 | 29.8% | -5.47% | benchmark_50k/gap_go_v1.md |
| gap_fade_v1 | **rejected** | 147 | -123.36 | 1,953.89 | -2,077.25 | 0.344 | 34.7% | -4.09% | benchmark_50k/gap_fade_v1.md |
| regime_selector_v1 | **rejected** | 175 | -548.36 | 2,331.36 | -2,879.72 | 0.123 | 22.3% | -5.63% | benchmark_50k/regime_selector_v1.md |
| relative_strength_v1 | **rejected** | 176 | -509.71 | 2,359.63 | -2,869.34 | 0.184 | 10.2% | -5.61% | benchmark_50k/relative_strength_v1.md |
| squeeze_breakout_v1 | **rejected** | 167 | -950.83 | 2,225.50 | -3,176.33 | 0.105 | 22.2% | -6.19% | benchmark_50k/squeeze_breakout_v1.md |
| momentum_v1 | no verdict (backtested, loses) | — | -14,015.76 | 14,400.78 | -28,416.54 | — | — | -28.42% | ../backtests/README.md |
| hold_baseline_v1 | baseline, never enabled | — | — | — | — | — | — | — | config, never a candidate |

All figures are real 5-minute bars over 2025-09-22 to 2026-09-18, out-of-sample walk-forward
windows on 50,000 rupees under `config/robustness/benchmark.yaml` (10% per position, 2% daily
loss), the platform's own `RiskRuleGate` in the loop, dated Angel One charges and a conservative
5 bps marketable-limit buffer.

## How to reproduce

```bash
pipenv run emporos backtest verdicts list          # the live standings (system of record)
pipenv run emporos backtest curate --only <name> --from 2025-09-22 --to 2026-09-18 \
    --cash 50000 --config config/robustness/benchmark.yaml --assume-earliest-fees
```

Each `benchmark_50k/*.md` card shows every gate, Monte Carlo, Deflated Sharpe, parameter
neighbours and the always-long baseline.

## Why every one loses

Two structural facts, not a tuning miss. At 5,000 per position the charges are about 13 per round
trip and the 5 bps marketable-limit buffer about 5.5 more, so a trade must gain about 0.37% gross
to break even — and **no signal gains anything before costs** (gross is negative for all eleven).
Every one of the eleven still loses with slippage removed entirely (`no_slippage`, -1,456 to
-2,644): the drag is the charges alone. The always-long baseline (net -3,062.66 over the same
windows) beats every candidate: simply being long intraday under the same engine out-earned every
strategy's view.

## Paper-trading gate

The paper-trading path closes EM-114's "no promotion on backtest profit alone": the paper
worker (`emporos worker run`) runs any strategy through the same signal, risk and execution path
as live, with fills against real quotes and books that cannot reach Angel One. Every launchable
strategy's verdict is shown on the dashboard beside its start control, and a strategy that is not
validated starts in paper only when the operator names its standing (`--acknowledge`). No strategy
here is validated, so none can be promoted to live regardless of paper results. The path has been
run end-to-end on a scripted feed over real Atlas in virtual time; a live open-market session
starts it for real.