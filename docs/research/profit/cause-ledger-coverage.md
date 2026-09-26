# Cause Ledger coverage notes (atlas §3.2)

What the ledger holds, what it lacks, and why. Attribution prompts (Call A/C) must say plainly
when an item class is absent. Checked 2026-09-26; collection follows §8 (no bypass of any block).

## Held
| §3.2 item | State |
|---|---|
| 1 Macro/event calendar | FOMC, RBI MPC + repo, Budget, elections, GST, NIFTY notices, US CPI/payrolls (464 events). India CPI/GDP only FY2024-25. F&O expiries follow from the archives. |
| 4 Insider/promoter (PIT, SAST) | 2017-2026, availability = dissemination stamp. Row counts fall from about 58k (2018) to about 17k (2024); whether the API caps results is unknown. |
| 5 Group map | 14 groups, 44 rows. |
| 7 Asia closes | Nikkei, KOSPI, Hang Seng; availability = the exchange close in IST. |

## Pending (say so in the prompt)
- **2 FII/DII cash flows:** NSE fiidii API sits behind Akamai (bot cookies, 503); NSDL FPI history is an
  ASP.NET postback. Not bypassed. Participant-wise F&O OI (nsearchives static files) is the substitute.
- **3 Bulk/block deals:** NSE API behind Akamai. Static dated files at nsearchives are being checked.
- **6 Date-level headlines: NONE. Prompts must state "no headlines are available".** Tried:
  - The Hindu, BusinessLine, Business Standard sitemaps: HTTP 403 to automated requests.
  - Economic Times: archive day list is JS-rendered (robots.txt allows crawling, but there is no static list).
  - Mint: no static sitemap or archive.
  - PIB: ASP.NET postback.
  - Wikipedia current events: world-level only, not market-level.
  - GDELT DOC: about 3 months only; the head said not to fetch it for now.
- SEBI board decisions, MSCI/FTSE rebalances: pending, low priority.
- BLS (403), FRED (no connect), stooq (proof-of-work), RBI cpolicy (captcha): not bypassed.
  US CPI/payrolls dates come from ALFRED release files fetched with curl and the same honest UA
  (an httpx client quirk, not an access control; the head approved).
