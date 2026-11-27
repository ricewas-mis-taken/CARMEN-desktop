"""Options > "Let AI agents ... work in their own tabs": on by default, shared with the extension through /status."""
import api_server
import config


def test_it_is_on_by_default_and_reaches_the_extension(isolate_state, isolate_review_db, isolate_config):
    client = api_server.app.test_client()
    assert config.DEFAULT_CONFIG["allowAiAgents"] is True
    assert client.get("/status").get_json()["allowAiAgents"] is True


def test_turning_it_off_reaches_the_extension(isolate_state, isolate_review_db, isolate_config):
    config.update_config(lambda cfg: cfg.update(allowAiAgents=False))
    assert api_server.app.test_client().get("/status").get_json()["allowAiAgents"] is False


def test_the_options_box_writes_it(qtbot, isolate_config, monkeypatch):
    import qt_ui.options_dialog as options
    monkeypatch.setattr(options, "reapply_stylesheet", lambda: None)
    dialog = options.OptionsDialog()
    qtbot.addWidget(dialog)
    box = next(b for b in dialog.findChildren(options.QCheckBox) if "AI agents" in b.text())
    assert box.isChecked()
    box.setChecked(False)
    assert config.load_config()["allowAiAgents"] is False
