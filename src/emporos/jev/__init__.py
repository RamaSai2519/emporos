"""Jev: an optional, experimental AI meta-decision provider reached through the Vercel Gateway
(EM-152). Never a hard dependency — every collaborator in the trading core (`risk`, `execution`,
`portfolio`, `strategies`, `domain`) is fully functional with Jev disabled or unreachable, and
none of them import this package (enforced by an import-linter contract). Jev only ever
confirms, ranks or advises; it cannot bypass risk, sizing or execution, and it fails closed by
default wherever that matters (live trading).
"""
