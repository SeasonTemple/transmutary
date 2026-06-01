"""Web dashboard (Phase B).

A local view over the system's runtime state (``state.sqlite3``) and archived
reports (``artifact_root``). Most routes are read-only. Localhost dashboards can
also promote/demote repositories through CSRF-protected confirmation forms that
write only the ``promoted_repo`` table. The dashboard never renders credentials
or subscriber tokens (KTD-Dash-4/5/6).
"""
