"""Colour themes: the setting, the lists in Python and JavaScript, and the generated stylesheet blocks."""
import importlib.util
import re
from pathlib import Path

import pytest

from tunerbox import config

ROOT = Path(__file__).resolve().parent.parent


def _gen():
    spec = importlib.util.spec_from_file_location("themes", ROOT / "packaging" / "themes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_theme_setting_is_checked():
    assert config.get("theme") == "midnight"
    assert config.clean_changes({"theme": "retro"}, from_pc=False) == {"theme": "retro"}
    with pytest.raises(ValueError):
        config.clean_changes({"theme": "neon"}, from_pc=True)


def test_python_and_javascript_list_the_same_themes():
    js = (ROOT / "web" / "theme.js").read_text(encoding="utf-8")
    assert re.findall(r'\["([a-z]+)", "', js) == list(config.THEMES) == list(_gen().THEMES)


def test_every_stylesheet_has_every_theme():
    for f in ("tv.css", "setup.css", "remote.css"):
        css = (ROOT / "web" / f).read_text(encoding="utf-8")
        for t in config.THEMES:
            if (f, t) == ("tv.css", "light"):       # TV screens stay dark in Light (pictures and video behind text)
                assert '[data-theme="light"]' not in css
                continue
            assert f'[data-theme="{t}"]' in css, (f, t)


def test_every_colour_variable_has_a_value_in_every_theme():
    gen = _gen()
    for f in ("tv.css", "setup.css", "remote.css"):
        css = (ROOT / "web" / f).read_text(encoding="utf-8")
        used = set(re.findall(r"var\((--c-[a-z0-9-]+)\)", css.split(gen.BEGIN)[0]))
        themes = css.split(gen.BEGIN)[1]
        for line in themes.splitlines()[:-1]:
            if line.startswith(":root"):
                assert all(f"{v}:" in line for v in used), (f, line[:40])


def test_generated_files_are_up_to_date(tmp_path, monkeypatch):
    """Running packaging/themes.py again changes nothing (the stylesheets and the palettes agree)."""
    gen = _gen()
    for f in gen.FILES:
        (tmp_path / f).write_text((ROOT / "web" / f).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    monkeypatch.setattr(gen, "WEB", tmp_path)
    gen.main()
    for f in gen.FILES:
        norm = lambda s: s.replace("\r\n", "\n")
        assert norm((tmp_path / f).read_text(encoding="utf-8")) == norm((ROOT / "web" / f).read_text(encoding="utf-8")), f


def test_pages_load_the_theme_first():
    for page in ("index.html", "setup.html", "remote.html", "pair.html"):
        html = (ROOT / "web" / page).read_text(encoding="utf-8")
        assert html.index("/static/theme.js") < html.index('rel="stylesheet"'), page
