"""Backtest-vs-paper parity (EM-185): does a paper session behave like the backtest of the same
config over the same day? Not to be confused with `portfolio/reconciliation.py`, which reconciles
the broker's books against ours.

Layering: parity reads from `backtest`, `strategies`, `domain` and persistence records (through
Protocols). It never imports a broker, the execution engine or the API.
"""
