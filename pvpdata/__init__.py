"""
Package for managing this repository's data, including generating or modifying
data files for the site or extracting data from external sources to use in the
site.

The top level package module only contains information about file paths
in the overall project.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SITE_SOURCE = PROJECT_ROOT / 'sitesource'
DATA_DIR = SITE_SOURCE / '_data'
GAME_RESOURCES_DIR = PROJECT_ROOT / 'gamefiles'
