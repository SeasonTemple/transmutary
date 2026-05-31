"""Read-only web dashboard (Phase B).

A local, read-only view over the system's runtime state (``state.sqlite3``) and
archived reports (``artifact_root``). It reuses the existing Starlette ASGI stack
and the store read interfaces; it never writes, never opens a mutation endpoint,
and never renders credentials or subscriber tokens (KTD-Dash-4/5/6).
"""
