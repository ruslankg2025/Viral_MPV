"""Dry-run: НЕ должно быть НИ ОДНОГО HTTP к api.vk.com."""
import httpx
import pytest


@pytest.fixture
def _ban_http(monkeypatch):
    """Любой исходящий HTTP роняет тест — гарантия отсутствия сети в dry-run."""
    hits = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        raise AssertionError(f"UNEXPECTED HTTP in dry-run: {request.url}")

    transport = httpx.MockTransport(handler)
    orig_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        kwargs.pop("timeout", None)
        orig_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    return hits


@pytest.mark.asyncio
async def test_simulate_publish_no_http(_ban_http):
    from dry_run import simulate_publish

    res = simulate_publish(
        platform="vk_video", video_path="/m/v.mp4",
        title="T", description="D", tags=["x"],
    )
    assert res["external_id"].startswith("dry-run-")
    assert res["payload"]["platform"] == "vk_video"
    assert _ban_http == []


@pytest.mark.asyncio
async def test_execute_publication_dry_run_no_http(_ban_http, tmp_path):
    from config import get_settings
    from service import execute_publication
    from storage import PublicationStore

    store = PublicationStore(tmp_path / "p.db")
    pub = store.create(platform="vk_clips", title="Clip", description="", tags=[],
                       video_path="/m/v.mp4", status="dry_run")
    updated = await execute_publication(
        pub, store=store, settings=get_settings(), dry_run=True
    )
    assert updated["status"] == "dry_run"
    assert updated["external_id"].startswith("dry-run-")
    assert updated["error_message"] is None
    assert _ban_http == []
