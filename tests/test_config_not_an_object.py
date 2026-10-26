"""config.json holding valid JSON that is not an object (null, a list, a number) used to
raise TypeError from every config call, including get_api_token(), so the API's token
check failed for good. It is treated like a corrupt file: defaults for display, a backup
before a read-modify-write replaces it."""
import glob
import json

import pytest

import config


@pytest.mark.parametrize("content", ["null", "[1, 2]", "123", '"x"', "true"])
def test_display_read_falls_back_to_defaults(isolate_config, content):
    with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    assert config.load_config()["last_duration_minutes"] == 25


@pytest.mark.parametrize("content", ["null", "[1, 2]", "123"])
def test_update_backs_up_the_odd_file_then_works(isolate_config, content):
    with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    token = config.get_api_token()                 # mints a token instead of raising
    assert token and config.get_api_token() == token
    assert glob.glob(config.CONFIG_PATH + ".corrupt-*")
    assert json.load(open(config.CONFIG_PATH, encoding="utf-8"))["apiToken"] == token
