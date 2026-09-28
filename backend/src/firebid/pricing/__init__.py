"""Pricing BOQ lines from the company rate library (P1-10, FR-CST-01).

Deterministic throughout: keys, matching, validity and money arithmetic. A model may only
propose which library entry a line is; the price always comes from the entry.
"""
