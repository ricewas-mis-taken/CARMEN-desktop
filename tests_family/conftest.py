"""Fixtures for the family tests. These need no Windows modules and no Qt: they run on any machine
(`python -m pytest tests_family`)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
