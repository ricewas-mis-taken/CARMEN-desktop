"""update_config()/set_focus_rules() read via load_config(), which maps an
unreadable or corrupt config.json to DEFAULT_CONFIG, then saved the result
over the real file -- wiping the saved blocklists and regenerating apiToken
(unpairing the browser extension)."""
import builtins
import glob
import json

import config


def _seed(tmp_path):
    cfg = dict(config.DEFAULT_CONFIG)
    cfg.update({"processBlocklist": ["discord.exe"], "domainWhitelist": ["school.edu"], "apiToken": "tok123"})
    config.save_config(cfg)


def test_transient_read_error_does_not_reset_config(isolate_config, tmp_path, monkeypatch):
    _seed(tmp_path)
    real_open = builtins.open
    state = {"failed": False}

    def flaky_open(file, mode="r", *a, **kw):
        if str(file) == config.CONFIG_PATH and "r" in mode and not state["failed"]:
            state["failed"] = True
            raise PermissionError("locked")
        return real_open(file, mode, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", flaky_open)
        config.update_config(lambda c: c.update({"last_duration_minutes": 50}))

    saved = json.load(open(config.CONFIG_PATH, encoding="utf-8"))
    assert saved["processBlocklist"] == ["discord.exe"]
    assert saved["apiToken"] == "tok123"


def test_corrupt_config_is_backed_up_before_being_replaced(isolate_config, tmp_path):
    _seed(tmp_path)
    good = open(config.CONFIG_PATH, encoding="utf-8").read()
    open(config.CONFIG_PATH, "w", encoding="utf-8").write(good[: len(good) // 2])  # truncated
    config.update_config(lambda c: c.update({"last_duration_minutes": 50}))
    backups = glob.glob(config.CONFIG_PATH + ".corrupt*")
    assert backups and "discord.exe" in open(backups[0], encoding="utf-8").read()
