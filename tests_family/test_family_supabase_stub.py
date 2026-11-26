"""The Supabase transport (family_supabase.py) is real now; this file used to pin the old documented stub (every call
raised NotImplementedError). What survives from it: the project ref, and the check that every table and SQL function the
transport uses exists in the schema in carmenlinked/supabase/ (skipped if that folder is not next to this repo).
The behaviour tests are in test_family_supabase.py (fake HTTP server) and test_family_supabase_live.py (real project)."""
import os
import re

import pytest

import family_supabase as fs
from family_transport import FamilyTransport

SQL_DIR = "/home/user/build/carmenlinked/supabase"
METHODS = [m for m in dir(FamilyTransport) if not m.startswith("_") and callable(getattr(FamilyTransport, m)) and m != "server_clock_offset_ms"]


@pytest.mark.parametrize("name", METHODS)
def test_every_interface_call_is_implemented(name):
    assert getattr(fs.SupabaseFamilyTransport, name) is not getattr(FamilyTransport, name)


def test_names_the_project_and_is_stdlib_only():
    assert fs.SUPABASE_PROJECT_REF == "wwkoiwegmgweblyaxmdw" and fs.DEFAULT_URL == "https://wwkoiwegmgweblyaxmdw.supabase.co"
    import inspect
    src = inspect.getsource(fs)
    assert "import requests" not in src and "import httpx" not in src and "supabase import" not in src


@pytest.mark.skipif(not os.path.isdir(SQL_DIR), reason="backend repo not present")
def test_tables_and_rpcs_exist_in_the_schema():
    sql = "\n".join(open(os.path.join(SQL_DIR, f), encoding="utf-8").read() for f in os.listdir(SQL_DIR) if f.endswith(".sql") and f[0] in "0")
    for table in fs.SUPABASE_TABLES:
        assert re.search(rf"create table\s+(if not exists\s+)?(public\.)?{table}\b", sql, re.I), table
    for fn in fs.SUPABASE_RPCS:
        assert re.search(rf"create (or replace )?function\s+(public\.)?{fn}\b", sql, re.I), fn
