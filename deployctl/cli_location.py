"""Locate the invoking CLI independently of PATH and installation defaults."""
from pathlib import Path
import sys


def invoking_cli():
    # Keep links intact so privileged installers can reject them in trusted().
    # Installed zipapps receive their actual filename as argv[0].
    return str(Path(sys.argv[0]).absolute())
