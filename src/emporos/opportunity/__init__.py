"""Multi-strategy opportunity selection (EM-152): scan the universe, ask every applicable
strategy for a signal, normalize what comes back into comparable `OpportunityCandidate`
records, rank them, and hand the ranked list to portfolio allocation. Composes `strategies`,
`risk` and `portfolio` from above; none of those packages know this one exists.
"""
