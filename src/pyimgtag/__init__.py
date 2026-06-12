"""pyimgtag — Tag macOS Photos library images using local Gemma model."""

try:
    from pyimgtag._version import __version__
except ImportError:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        __version__ = _pkg_version("pyimgtag")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
