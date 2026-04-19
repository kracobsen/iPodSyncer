"""ipodsync — sync music, podcasts, and audiobooks to iPod Classic 6G."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

try:
    __version__ = _dist_version("ipodsync")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
