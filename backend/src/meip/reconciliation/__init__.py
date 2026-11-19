"""Reconciliation (Stage 2-A): versioned, deterministic checks over reviewed payslip figures.

Findings are computed when requested and never stored; only a broker's acknowledgement of a
discrepancy is persisted. An acknowledgement is not a resolution and approves nothing: it
never changes the financial profile."""
