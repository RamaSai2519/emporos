# Emporos

Personal algorithmic trading platform trading NSE/BSE cash equity intraday through
Angel One SmartAPI. See [plan.md](plan.md) for the full architecture and implementation
plan, and the Jira project `EM` for phase-by-phase work tracking (plan.md §21).

## Setup

```
pipenv install --dev
cp .env.example .env   # fill in MONGO_URL and Angel One credentials
pipenv run test        # fast, DB-free gate
pipenv run test-db     # full suite incl. live Atlas checks (needs MONGO_URL)
```
