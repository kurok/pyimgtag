"""Shared navigation shell for the unified webapp."""

from __future__ import annotations


def render_nav(active: str) -> str:
    """Return the nav-bar HTML with the given section marked active.

    ``active`` is one of ``"dashboard"``, ``"review"``, ``"faces"``,
    ``"tags"``, ``"query"``, ``"judge"``.
    """

    def cls(name: str) -> str:
        return "nav-link active" if name == active else "nav-link"

    return (
        '<nav class="nav">'
        f'<a class="{cls("dashboard")}" href="/">Dashboard</a>'
        f'<a class="{cls("review")}" href="/review">Review</a>'
        f'<a class="{cls("faces")}" href="/faces">Faces</a>'
        f'<a class="{cls("tags")}" href="/tags">Tags</a>'
        f'<a class="{cls("query")}" href="/query">Query</a>'
        f'<a class="{cls("judge")}" href="/judge">Judge</a>'
        "</nav>"
    )


NAV_STYLES = """
.nav{display:flex;gap:1rem;padding:.6rem 1.5rem;background:#1a1a1a;
     border-bottom:1px solid #333;font-size:.85rem;position:sticky;top:0;z-index:20}
.nav-link{color:#888;text-decoration:none;padding:.2rem .5rem;border-radius:4px}
.nav-link:hover{color:#e0e0e0;background:#252525}
.nav-link.active{color:#bb86fc;background:#252525}
"""
