from pathlib import Path

from orchestrator.dedup import find_active_duplicate
from orchestrator.runs.store import RunStore


def test_dedup_priority_video_id_over_url(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    by_video = store.create(url="https://a", platform="instagram", video_id="vid1")
    # Другой run на том же URL, но без video_id
    store.create(url="https://a", platform="instagram")

    # Запрос с video_id="vid1" — приоритет на by_video, даже если url совпал
    found = find_active_duplicate(store, video_id="vid1", url="https://a")
    assert found is not None
    assert found["id"] == by_video


def test_dedup_no_match_returns_none(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    found = find_active_duplicate(store, video_id="nope", url="https://nope")
    assert found is None


def test_dedup_skips_terminal(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    a = store.create(url="https://x", platform="instagram", video_id="v1")
    store.set_status(a, "done", result={})
    # Active не должен находиться, т.к. a — done
    assert find_active_duplicate(store, video_id="v1", url="https://x") is None


def test_dedup_finds_by_url_when_no_video_id(tmp_path: Path):
    store = RunStore(tmp_path / "runs.db")
    a = store.create(url="https://x", platform="tiktok")
    found = find_active_duplicate(store, video_id=None, url="https://x")
    assert found is not None
    assert found["id"] == a
