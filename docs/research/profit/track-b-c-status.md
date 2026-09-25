# Track B and Track C: status, lessons, and what reopens each (2026-09-25)

Decision by the head session after EM-230 (B1), EM-231 (L2 in-session) and EM-234 (B1b). Recorded in
`docs/research/edge-search/STATE.md` (top note). The program's ledgers are `screens.jsonl` here (Track A and
B, ids `SWG-` and `OPT-`) and `docs/research/edge-search/screens.jsonl` (Track C).

## Track B (index options at Rs 1,00,000): CLOSED

| Cell | Arms | Result |
|---|---|---|
| B1 NIFTY put-credit-spread ladder (`b1-nifty-put-spread-ladder`) | 8 (k 1.0/1.5 x filter x depth 2/3) | all SCREEN_REJECT: net CAGR -1.0..-2.9%, adverse -2.8..-12.0%, worst month -1.6..-3.4%, P(dd >= 30%) 0 for the depth-3 arms |
| B1b no-stop child (`b1b-nifty-put-spread-no-stop`) | 4 (filter x wing cap Rs 5,000/10,000) | all SCREEN_REJECT: net CAGR -0.5..-1.9%, adverse -2.7..-8.6% |

Reports: `reports/b1-nifty-put-spread-ladder.txt`, `reports/b1b-nifty-put-spread-no-stop.txt`.

**Closure line (declared before B1b ran, met):** no arm's gross per spread at ZERO slippage reaches 2x its
charges (1.46x, 0.95x, 1.20x, 1.32x), so index put-spread selling is closed at this capital and no further
put-spread cell is declared from Discovery output.

Lessons:
- The structure needs about an 83% win rate before any cost (average loss about 5x the average win) and has
  82%: the premium in a Rs 5,000-capped NIFTY put spread is about zero with free fills (gross Rs 111 per
  spread against Rs 109 of statutory charges and brokerage in B1's best arm).
- Fees are 26% of the credit at k = 1.0 (four leg orders at Rs 20 plus statutory charges), slippage at the
  benchmark another 18%: a Rs 5,000 wing on a 75-unit lot is 50-65 points, too small a credit to carry four
  orders.
- Removing the 2x stop (B1b) raised gross a little (Rs 156 against 111 per spread) and changed nothing that
  matters: the wing losses take it back. A wider wing (Rs 10,000) halves the fee share of the credit but
  its average loss is four times larger; those arms also fail the -10% worst-month bar and need an operator
  change to PROFIT_PLAN §3.5.
- The amended months bar (PROFIT_PLAN §3.2, 2026-09-25) is passed by three B1b arms that still lose money:
  a high win rate with a large loss-to-win ratio is not an edge.
- Slippage is an assumption (no bid-ask history): the result would not change at zero slippage.

Reopens only if: (1) an instrument or structure with a measured credit per order well above the charges is
proposed (a wider defined-risk structure needs a §3.5 change first), or (2) the operator raises the capital
so fees are a smaller share, and either is declared as a new cell with its own rationale. A change to the fee
schedule (`verified: false` today, D8) that halves the charges would also justify one new declaration.

## Track C (intraday cash equity): PAUSED

The loop ran 74 arms in the intraday ledger (plus the earlier documented studies) across lanes L2, L3, L4,
L5 and L17. Every cell is SCREEN_REJECT. The last two: L5-index-trend-day-leader (EM-227, gross -0.10..+0.085%)
and L2-in-session-results-drift (EM-231, gross -0.03..+0.002% in both directions).

Lessons:
- Large days are necessary, not sufficient: median absolute moves of 1-2% clear S1, but the mean gross in
  the signed direction is about zero (the direction, not the size, is the missing information).
- Overnight and first-five-minute news is absorbed before a signal can be traded; waiting helps a little
  (fade after 30 minutes +0.14..0.19%) but stays under the 0.23% benchmark and 0.495% adverse break-even.
- The best gross in the program is L17's daily top-shock fade (+0.21..0.34%), concentrated in one name and
  still under the adverse break-even.
- Spreads on mid-caps are unmeasured (D7); every adverse scenario is an assumption.

Resumes only for L13 passive entries and scalping, designed against at least two months of D7 quotes
(`docs/ops/quote-recording.md`). Nothing else is declared until then.
