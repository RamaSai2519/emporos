"""Promotion of a strategy configuration along RESEARCH -> PAPER -> LIVE_CONSERVATIVE (EM-189).

Requirements are one class each, a stage's policy is a list of them, and the service appends the
result to an append-only ledger. Nothing here places an order or touches a broker: it decides
whether a configuration MAY go further, on evidence, and records who decided it.
"""
