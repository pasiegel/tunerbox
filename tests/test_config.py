import json
import threading

import pytest

from tunerbox import config


def test_defaults_written_on_first_load(fresh_data):
    config.load()
    saved = json.loads((fresh_data / "settings.json").read_text())
    assert saved["port"] == 8642
    assert config.valid_device_id(saved["hdhr_device_id"])          # new install: its own id


def test_existing_install_keeps_legacy_device_id(fresh_data):
    (fresh_data / "settings.json").write_text(json.dumps({"port": 9000}))
    assert config.load()["hdhr_device_id"] == config.LEGACY_DEVICE_ID
    assert config.get("port") == 9000


def test_device_ids_are_valid_and_random():
    ids = {config.new_device_id() for _ in range(200)}
    assert len(ids) > 190
    assert all(len(i) == 8 and config.valid_device_id(i) for i in ids)
    assert not config.valid_device_id("1234567X")


def test_damaged_settings_file_is_set_aside(fresh_data):
    (fresh_data / "settings.json").write_text('{"port": 9000,')       # cut off mid-write
    assert config.load()["port"] == 8642
    assert (fresh_data / "settings.json.broken").read_text() == '{"port": 9000,'
    json.loads((fresh_data / "settings.json").read_text())             # replaced with a good file


def test_concurrent_updates_are_not_lost():
    config.load()

    def star(n):
        config.update({f"x{n}": n})
    threads = [threading.Thread(target=star, args=(n,)) for n in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(config.get(f"x{n}") == n for n in range(50))


# ---------------------------------------------------------------- clean_changes
def test_unknown_and_mistyped_settings_are_refused():
    with pytest.raises(ValueError, match="Unknown setting"):
        config.clean_changes({"nope": 1}, from_pc=True)
    with pytest.raises(ValueError):
        config.clean_changes({"hide_unmapped": "yes"}, from_pc=True)
    with pytest.raises(ValueError):
        config.clean_changes({"gracenote": {"bogus": 1}}, from_pc=True)


def test_values_are_coerced_to_the_default_type():
    out = config.clean_changes({"match_threshold": "90", "stream_buffer": 10.0, "media_resolution": 720,
                                "epg_refresh_hours": 0.5, "gracenote": {"hours": "24"}}, from_pc=True)
    assert out == {"match_threshold": 90, "stream_buffer": 10, "media_resolution": "720",
                   "epg_refresh_hours": 0.5, "gracenote": {"hours": 24}}


def test_devices_and_device_id_cannot_be_set():
    assert config.clean_changes({"devices": [{"hash": "x"}], "hdhr_device_id": "1"}, from_pc=True) == {}


def test_pc_only_settings():
    with pytest.raises(PermissionError):
        config.clean_changes({"ffmpeg": r"\\evil\share\x.exe"}, from_pc=False)
    with pytest.raises(PermissionError):
        config.clean_changes({"require_pairing": True}, from_pc=False)
    assert config.clean_changes({"ffmpeg": "/opt/ffmpeg"}, from_pc=True) == {"ffmpeg": "/opt/ffmpeg"}


def test_phone_can_resave_whole_form_with_unchanged_pc_only_values():
    form = {"ffmpeg": config.get("ffmpeg"), "recordings_dir": config.get("recordings_dir"), "hide_unmapped": False}
    assert config.clean_changes(form, from_pc=False) == {"hide_unmapped": False}


def test_phone_can_add_online_guides_but_not_files():
    assert config.clean_changes({"xmltv_sources": ["https://g/epg.xml", " "]}, from_pc=False) == {
        "xmltv_sources": ["https://g/epg.xml"]}
    with pytest.raises(PermissionError):
        config.clean_changes({"xmltv_sources": ["C:/secret.xml"]}, from_pc=False)


def test_home_screen_setting():
    assert config.get("home_screen") is True                                  # on unless turned off
    assert config.clean_changes({"home_screen": False}, from_pc=False) == {"home_screen": False}
    with pytest.raises(ValueError):
        config.clean_changes({"home_screen": "no"}, from_pc=False)
