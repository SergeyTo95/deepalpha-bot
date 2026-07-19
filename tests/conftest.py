"""Shared pytest runtime adapters.

Register an explicit sqlite datetime adapter so Python 3.12+ does not fall
back to sqlite3's deprecated default adapter while PolyWar tests exercise
TIMESTAMP columns with ``datetime`` values.
"""
from datetime import datetime
import sqlite3

sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" "))
