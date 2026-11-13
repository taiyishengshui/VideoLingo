"""Service layer: the ONLY home for cross-entry decision logic.

Layering rule (project convention, 2026-09-09):
  routes/entries (server/*.py, batch engine)  -> thin adapters
  services (core/services/)                   -> decisions + orchestration
  utils (core/utils/)                         -> data + construction

A "decision" is anything that answers "what do we do when X happens"
(rotate a key? wipe a marker? which status codes mean dead?). Decisions
live in exactly one module; check_single_source.js enforces it.
"""
