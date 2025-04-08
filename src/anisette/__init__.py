"""Anisette provider in a Python package."""

from importlib.metadata import version

from ._device import AnisetteDeviceConfig
from .anisette import Anisette, AnisetteHeaders

__version__ = version("anisette")

__all__ = ("Anisette", "AnisetteDeviceConfig", "AnisetteHeaders")
