"""The Supabase transport is a documented stub: every call raises NotImplementedError, and the tables/functions it
names must exist in the schema in carmenlinked/supabase/ (skipped if that folder is not next to this repo)."""
import os
import re

import pytest

import family_supabase as fs
from family_transport import FamilyTransport

SQL_DIR = "/home/user/build/carmenlinked/supabase"
METHODS = [m for m in dir(FamilyTransport) if not m.startswith("_") and callable(getattr(FamilyTransport, m)) and m != "server_clock_offset_ms"]


@pytest.mark.parametrize("name", METHODS)
def test_every_call_raises_not_implemented(name):
    fn = getattr(fs.SupabaseFamilyTransport(), name)
    with pytest.raises(NotImplementedError) as err:
        fn(*([None] * fn.__code__.co_argcount)[1:]) if False else fn(*[None] * (fn.__code__.co_argcount - 1))
    assert "not written yet" in str(err.value)


def test_names_the_project_and_does_no_network():
    assert fs.SUPABASE_PROJECT_REF == "wwkoiwegmgweblyaxmdw"
    import inspect
    assert "urllib" not in inspect.getsource(fs) and "supabase import" not in inspect.getsource(fs)


@pytest.mark.skipif(not os.path.isdir(SQL_DIR), reason="backend repo not present")
def test_tables_and_rpcs_exist_in_the_schema():
    sql = "\n".join(open(os.path.join(SQL_DIR, f), encoding="utf-8").read() for f in os.listdir(SQL_DIR) if f.endswith(".sql") and f[0] in "0")
    for table in fs.SUPABASE_TABLES:
        assert re.search(rf"create table\s+(if not exists\s+)?(public\.)?{table}\b", sql), table
    for fn in fs.SUPABASE_RPCS:
        assert re.search(rf"create (or replace )?function\s+{fn}\b", sql), fn
