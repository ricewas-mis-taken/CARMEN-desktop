import os

import board_store


def test_board_photo_extension_is_allowlisted(tmp_path, monkeypatch):
    monkeypatch.setattr(board_store, "PHOTOS_DIR", str(tmp_path / "photos"))
    exe = board_store.save_photo_bytes(b"MZ\x90\x00", "evil.exe")
    html = board_store.save_photo_bytes(b"<script>", "x.HTML")
    jpg = board_store.save_photo_bytes(b"\xff\xd8", "ok.JPG")
    assert os.path.splitext(exe)[1] == ".png"
    assert os.path.splitext(html)[1] == ".png"
    assert os.path.splitext(jpg)[1] == ".jpg"
