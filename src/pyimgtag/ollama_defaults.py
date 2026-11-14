"""Ollama defaults shared by the CLI, the clients and preflight.

Kept free of heavy imports (no Pillow, no requests) so ``main.py`` can use
them for argparse defaults and help text without slowing every command's
start-up. :mod:`pyimgtag.ollama_client` re-exports them.
"""

from __future__ import annotations

import os

#: Model used when ``--model`` is not given for the Ollama backend.
DEFAULT_OLLAMA_MODEL = "gemma4:e4b"

#: Ollama base URL used when neither ``--ollama-url`` nor ``OLLAMA_URL`` is set.
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def ollama_url_from_env() -> str:
    """Return ``$OLLAMA_URL`` when it is set, else :data:`DEFAULT_OLLAMA_URL`."""
    return os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL)
