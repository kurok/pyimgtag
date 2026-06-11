"""Shared Jinja2 environment for the webapp page templates.

The environment autoescapes by default. The handful of context values
that are intentionally raw — the nav shell, the design-system CSS, the
modal markup/JS, the API base prefix — are trusted, server-built
fragments (never user-influenced) and are explicitly marked with
:class:`Markup` at each render call site. Any other value handed to a
template is escaped automatically.
"""

from __future__ import annotations

from typing import Any

try:
    from jinja2 import Environment, PackageLoader
    from markupsafe import Markup

    _ENV: Any = Environment(
        loader=PackageLoader("pyimgtag.webapp", "templates"),
        autoescape=True,
    )
except ImportError:  # pragma: no cover — exercised in minimal envs only
    _ENV = None
    Markup = str  # type: ignore[assignment,misc]


def render(template_name: str, **context: Any) -> str:
    """Render a webapp page template with the given context.

    Args:
        template_name: File name under ``pyimgtag/webapp/templates/``.
        **context: Template variables. Values are autoescaped unless the
            caller marks them as trusted with :class:`Markup`.

    Returns:
        The rendered HTML page as a string.

    Raises:
        ImportError: If jinja2 is not installed.
    """
    if _ENV is None:
        raise ImportError(
            "jinja2 is required for the web UI. Install with: pip install 'pyimgtag[review]'"
        )
    return _ENV.get_template(template_name).render(**context)
