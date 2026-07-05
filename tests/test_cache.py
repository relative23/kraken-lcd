import os

from kraken_lcd.cache import RenderCache


def test_get_or_render_roundtrip(tmp_path):
    cache = RenderCache(tmp_path / "cache", max_bytes=10_000)
    calls = []

    def render(path):
        calls.append(path)
        path.write_bytes(b"x" * 100)

    first = cache.get_or_render("key1", render)
    second = cache.get_or_render("key1", render)
    assert first == second
    assert len(calls) == 1  # second call was a cache hit


def test_different_keys_get_different_files(tmp_path):
    cache = RenderCache(tmp_path / "cache", max_bytes=10_000)
    assert cache.path_for("a") != cache.path_for("b")


def test_failed_render_returns_none_and_leaves_no_file(tmp_path):
    cache = RenderCache(tmp_path / "cache", max_bytes=10_000)

    def render(path):
        raise RuntimeError("render exploded")

    assert cache.get_or_render("key", render) is None
    assert not cache.path_for("key").exists()


def test_empty_result_treated_as_miss(tmp_path):
    cache = RenderCache(tmp_path / "cache", max_bytes=10_000)
    cache.path_for("key").write_bytes(b"")  # corrupt zero-byte entry

    def render(path):
        path.write_bytes(b"fresh")

    result = cache.get_or_render("key", render)
    assert result is not None
    assert result.read_bytes() == b"fresh"


def test_prune_removes_oldest_first(tmp_path):
    cache = RenderCache(tmp_path / "cache", max_bytes=250)
    for age, key in enumerate(["old", "mid", "new"]):
        path = cache.path_for(key)
        path.write_bytes(b"x" * 100)
        os.utime(path, (age, age))  # deterministic mtimes: old=0, mid=1, new=2
    cache.prune()  # 300 bytes > 250: drop the oldest entry only
    assert not cache.path_for("old").exists()
    assert cache.path_for("mid").exists()
    assert cache.path_for("new").exists()


def test_unwritable_dir_falls_back_to_home_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    blocker = tmp_path / "blocker"
    blocker.write_text("")  # a *file* where a directory is needed
    cache = RenderCache(blocker / "sub", max_bytes=1_000)
    assert cache.directory == tmp_path / ".cache" / "kraken-lcd"
