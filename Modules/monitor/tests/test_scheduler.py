from datetime import datetime, timezone

import pytest
import pytest_asyncio

from scheduler import SchedulerWrapper, _anchor_dt


@pytest_asyncio.fixture
async def scheduler():
    async def noop(source_id: str):
        pass

    s = SchedulerWrapper(crawl_callback=noop)
    s.start()
    yield s
    s.stop()


@pytest.mark.asyncio
async def test_add_and_list_jobs(scheduler):
    scheduler.add_source_job("src-a", interval_min=30)
    scheduler.add_source_job("src-b", interval_min=60)
    jobs = scheduler.list_jobs()
    assert len(jobs) == 2
    ids = {j["id"] for j in jobs}
    assert ids == {"src:src-a", "src:src-b"}


@pytest.mark.asyncio
async def test_add_job_replaces_existing(scheduler):
    scheduler.add_source_job("src-a", interval_min=30)
    scheduler.add_source_job("src-a", interval_min=15)
    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert "0:15:00" in jobs[0]["trigger"]


@pytest.mark.asyncio
async def test_remove_job(scheduler):
    scheduler.add_source_job("src-a", interval_min=30)
    scheduler.add_source_job("src-b", interval_min=60)
    scheduler.remove_source_job("src-a")
    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == "src:src-b"


@pytest.mark.asyncio
async def test_remove_nonexistent_job_is_idempotent(scheduler):
    scheduler.remove_source_job("src-nonexistent")  # should not raise


@pytest.mark.asyncio
async def test_reload_from_sources(scheduler, store):
    s1 = store.create_source(account_id="a", platform="youtube", channel_url="u1", external_id="e1", interval_min=30)
    s2 = store.create_source(account_id="a", platform="youtube", channel_url="u2", external_id="e2", interval_min=60)
    s3 = store.create_source(account_id="a", platform="youtube", channel_url="u3", external_id="e3")
    store.update_source(s3.id, is_active=False)

    sources = store.list_sources()
    count = scheduler.reload_from_sources(sources)
    assert count == 2

    jobs = scheduler.list_jobs()
    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_reload_replaces_old_jobs(scheduler, store):
    scheduler.add_source_job("old-src", interval_min=30)
    s = store.create_source(account_id="a", platform="youtube", channel_url="u", external_id="e")
    scheduler.reload_from_sources([s])
    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == f"src:{s.id}"


def test_anchor_dt_default():
    dt = _anchor_dt("00:00")
    assert dt == datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_anchor_dt_custom_hour():
    dt = _anchor_dt("03:30")
    assert dt == datetime(2000, 1, 1, 3, 30, 0, tzinfo=timezone.utc)


def test_anchor_dt_invalid_falls_back_to_midnight():
    dt = _anchor_dt("not-a-time")
    assert dt == datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_add_job_uses_anchor_start_date(scheduler):
    """start_date фиксирует выравнивание интервальных тиков.
    Для anchor=00:00 и interval=360 min ближайший next_run_time должен быть
    в один из 00/06/12/18 UTC."""
    scheduler.add_source_job("src-aligned", interval_min=360)
    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    # Достанем реальный job и проверим next_run_time
    job = scheduler._scheduler.get_job("src:src-aligned")
    assert job is not None
    nrt = job.next_run_time
    assert nrt is not None
    # В UTC для anchor=00:00 и interval=360 — next_run_time час должен быть 0/6/12/18
    nrt_utc = nrt.astimezone(timezone.utc)
    assert nrt_utc.hour in {0, 6, 12, 18}
    assert nrt_utc.minute == 0


@pytest.mark.asyncio
async def test_set_anchor_changes_alignment(scheduler):
    scheduler.set_anchor("03:00")
    scheduler.add_source_job("src-x", interval_min=360)
    job = scheduler._scheduler.get_job("src:src-x")
    nrt = job.next_run_time.astimezone(timezone.utc)
    # Для anchor=03:00 и interval=360 — 03/09/15/21
    assert nrt.hour in {3, 9, 15, 21}
    assert nrt.minute == 0


def test_start_stop_idempotent_without_loop():
    """Чистая проверка флага running без создания реальных jobs."""
    import asyncio
    async def noop(source_id: str):
        pass

    async def _run():
        s = SchedulerWrapper(crawl_callback=noop)
        assert s.running is False
        s.start()
        assert s.running is True
        s.start()  # no-op
        assert s.running is True
        s.stop()
        assert s.running is False
        s.stop()  # no-op

    asyncio.run(_run())
