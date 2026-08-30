"""Customizable tagging prompt: template file, vocabulary block, output language.

:class:`PromptBuilder` renders the per-image tagging prompt from a template
with these placeholders:

``{context}``
    EXIF/geocoding hints block (date, location, GPS) or empty.
``{vocabulary}``
    The controlled-vocabulary instruction block or empty.
``{language}``
    The output-language instruction or empty.
``{max_tags}``
    The maximum number of tags (``5``).
``{fields}``
    The schema-critical field/JSON instructions the response parser depends
    on. A template that omits it (and does not spell those instructions out
    itself) gets it appended automatically, so customizing the *domain* can
    never break the *parser*.

Unknown placeholders are a startup error — never a run of unparseable
responses.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from pathlib import Path

from pyimgtag.ollama_client import _PROMPT_FIELDS
from pyimgtag.vocabulary import Vocabulary

__all__ = [
    "DEFAULT_TEMPLATE",
    "MAX_TAGS",
    "PLACEHOLDERS",
    "PromptBuilder",
    "PromptTemplateError",
    "load_prompt_template",
    "validate_template",
]

MAX_TAGS = 5
PLACEHOLDERS = ("context", "vocabulary", "language", "max_tags", "fields")

# Mirrors the hand-built prompt in ollama_client._build_prompt_with_context
# so a run without --prompt-template produces byte-identical prompts.
DEFAULT_TEMPLATE = """\
Tag this image for a photo gallery.

{context}{vocabulary}{language}{fields}"""

_CONTEXT_TAIL = (
    "Prefer broad useful tags. Ignore small background objects. "
    "No place guesses from image content "
    "(location context above is from GPS metadata).\n\n"
)

# Markers that prove a template already carries the schema instructions.
_SCHEMA_MARKERS = ("tags", "summary", "scene_category", "cleanup_class", "json")


class PromptTemplateError(ValueError):
    """Raised for an unreadable template or one using unknown placeholders."""


def validate_template(text: str, *, source: str = "<template>") -> str:
    """Validate placeholders and guarantee the schema block is present.

    Returns the (possibly augmented) template text.

    Raises:
        PromptTemplateError: On unknown placeholders or an empty template.
    """
    if not text.strip():
        raise PromptTemplateError(f"{source}: prompt template is empty")
    fields_used: set[str] = set()
    try:
        for _literal, field_name, _spec, _conv in string.Formatter().parse(text):
            if field_name is None:
                continue
            fields_used.add(field_name)
    except ValueError as exc:
        raise PromptTemplateError(
            f"{source}: malformed placeholder syntax ({exc}); "
            "write literal braces as '{{' and '}}'"
        ) from exc
    unknown = sorted(f for f in fields_used if f not in PLACEHOLDERS)
    if unknown:
        raise PromptTemplateError(
            f"{source}: unknown placeholder(s) {', '.join('{' + u + '}' for u in unknown)}; "
            f"allowed: {', '.join('{' + p + '}' for p in PLACEHOLDERS)}"
        )
    if "fields" in fields_used:
        return text
    lowered = text.lower()
    if all(marker in lowered for marker in _SCHEMA_MARKERS):
        return text  # author spelled the JSON contract out themselves
    return text.rstrip() + "\n\n{fields}"


def load_prompt_template(path: str | Path) -> str:
    """Read and validate a template file."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptTemplateError(f"{p}: cannot read prompt template: {exc}") from exc
    # Editors and `prompt show > file` leave a trailing newline; the prompt
    # itself never ends with one, so strip it for a byte-identical round-trip.
    return validate_template(text.rstrip("\n"), source=str(p))


def _context_block(context: dict | None) -> str:
    if not context:
        return ""
    ctx_lines = []
    if context.get("date"):
        ctx_lines.append(f"- Date: {context['date']}")
    loc_parts = [
        p for p in [context.get("city"), context.get("region"), context.get("country")] if p
    ]
    if loc_parts:
        ctx_lines.append(f"- Location: {', '.join(loc_parts)}")
    if context.get("lat") is not None and context.get("lon") is not None:
        ctx_lines.append(f"- GPS: {context['lat']}, {context['lon']}")
    if not ctx_lines:
        return ""
    return (
        "Context (use to improve tag relevance, not as tags themselves):\n"
        + "\n".join(ctx_lines)
        + "\n\n"
        + _CONTEXT_TAIL
    )


@dataclass
class PromptBuilder:
    """Render the tagging prompt for one image.

    Attributes:
        template: Validated template text (see :func:`validate_template`).
        vocabulary: Optional controlled vocabulary embedded as the allowed set.
        language: Optional output language (e.g. ``"ru"``, ``"Portuguese"``).
    """

    template: str = DEFAULT_TEMPLATE
    vocabulary: Vocabulary | None = None
    language: str | None = None

    def __post_init__(self) -> None:
        self.template = validate_template(self.template)

    @property
    def is_default(self) -> bool:
        """True when rendering is indistinguishable from the built-in prompt."""
        return (
            self.template == DEFAULT_TEMPLATE
            and self.vocabulary is None
            and not (self.language or "").strip()
        )

    def _vocabulary_block(self) -> str:
        if self.vocabulary is None:
            return ""
        return self.vocabulary.prompt_block() + "\n\n"

    def _language_block(self) -> str:
        lang = (self.language or "").strip()
        if not lang:
            return ""
        if self.vocabulary is not None:
            return (
                f"Write the summary and text_summary in {lang}. "
                "Tags must stay exactly as written in the tag list above.\n\n"
            )
        return f"Write the tags, summary, and text_summary in {lang}.\n\n"

    def render(self, context: dict | None = None) -> str:
        """Return the final prompt for the given EXIF/geocoding *context*."""
        return self.template.format(
            context=_context_block(context),
            vocabulary=self._vocabulary_block(),
            language=self._language_block(),
            max_tags=MAX_TAGS,
            fields=_PROMPT_FIELDS,
        )
