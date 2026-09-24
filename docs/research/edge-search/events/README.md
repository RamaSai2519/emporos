# Results-announcement events (EM-191 D5, EM-209)

What this is: when each D1 name's financial results **became public**, taken from the exchange's own
dissemination timestamp (`public_at`, IST) on the filing. Nothing here is derived from a price or a
volume, so a lane that uses it cannot leak the outcome. The reaction day is decided later, from
`public_at`: a result published after the close reacts the next session, one published in the session
reacts within it.

Source: NSE's corporate-announcements endpoint, used on the operator's authorisation (2026-09-24),
one request per name and subject, three seconds apart, no retries, an honest user agent. Collected by
`emporos research collect-results --to 2026-09-18`. Window asked for: 2016-10-03..2026-09-18.

Files (append-only; never edit by hand):
- `results-filings.jsonl`: every results filing, once each (name and the exchange's sequence id).
  `subject` is "Financial Result Updates" (6,766) or "Outcome of Board Meeting" (1,807, kept only when
  the text says it is about results).
- `results-collected.jsonl`: which name was collected under which subject and when.
- `emporos.research.results_filings.first_public_results` collapses a name's filings to one event per
  results season: the EARLIEST filing of a cluster (a later filing more than 20 days after the
  cluster's first starts a new one).

## Coverage, as of 2026-09-24 (8,573 filings, 8,070 first-public events, 250 names)
- Median 37 events per name (about 40 quarters exist in the window), maximum 41.
- **Known gaps.** The exchange's tagging is inconsistent, so some results seasons are missing:
  2022 has 541 events against about 860 in a normal year, and 5% of the gaps between a name's
  consecutive events exceed 150 days (a missing quarter). Newly listed names have fewer events from
  their listing date. **ABBOTINDIA and MCX have none** (their results are filed under other subjects).
- A missing event is a MISSED trade for a lane, never a leaked one, but it can bias a lane: lanes must
  report the events per year they used, and must not read a missing 2022 as "no earnings".
- The list of names is the CURRENT NIFTY 100 and Midcap 150 (survivorship bias, see
  `config/universe/d1/SOURCE.yaml`).
- Only results are collected. Board-meeting intimations, index rebalances, F&O ban lists, bulk deals
  and ex-dates (the rest of D5) are not.
