"""Unit-тесты KeyResolver без зависимостей от сети/ffmpeg/processor."""
import asyncio

import pytest
from cryptography.fernet import Fernet

from viral_llm.clients.base import ProviderError
from viral_llm.keys.crypto import KeyCrypto
from viral_llm.keys.resolver import KeyResolver, NoProviderAvailable, UsageResult
from viral_llm.keys.store import KeyStore


@pytest.fixture()
def store(tmp_path):
    crypto = KeyCrypto(Fernet.generate_key().decode())
    return KeyStore(tmp_path / "keys.db", crypto)


def test_resolver_happy_path(store):
    store.create(provider="deepgram", label="dg", secret="secret-dg", priority=1)
    resolver = KeyResolver(store)

    async def call(key, secret):
        assert secret == "secret-dg"
        return UsageResult(
            result={"text": "hi"},
            provider="deepgram",
            model="nova-3",
            cost_usd=0.01,
            audio_seconds=10.0,
            latency_ms=100,
        )

    usage = asyncio.run(
        resolver.run_with_fallback(
            kind="transcription",
            job_id="j1",
            operation="transcribe",
            provider=None,
            call=call,
        )
    )
    assert usage.result == {"text": "hi"}
    assert store.usage_30d_summary(1)["calls"] == 1


def test_resolver_fallback_chain(store):
    store.create(provider="deepgram", label="dg", secret="s1", priority=1)
    store.create(provider="assemblyai", label="aa", secret="s2", priority=2)
    store.create(provider="groq_whisper", label="gr", secret="s3", priority=3)
    resolver = KeyResolver(store)

    calls: list[str] = []

    async def call(key, secret):
        calls.append(key["provider"])
        if key["provider"] in ("deepgram", "assemblyai"):
            raise ProviderError(f"{key['provider']}_down")
        return UsageResult(
            result={"text": "ok"},
            provider="groq_whisper",
            model="whisper-large-v3",
            cost_usd=0.0,
        )

    usage = asyncio.run(
        resolver.run_with_fallback(
            kind="transcription",
            job_id="j2",
            operation="transcribe",
            provider=None,
            call=call,
        )
    )
    assert calls == ["deepgram", "assemblyai", "groq_whisper"]
    assert usage.provider == "groq_whisper"


def test_resolver_no_providers_raises(store):
    resolver = KeyResolver(store)

    async def call(k, s):
        return None

    with pytest.raises(NoProviderAvailable):
        asyncio.run(
            resolver.run_with_fallback(
                kind="transcription",
                job_id="j3",
                operation="transcribe",
                provider=None,
                call=call,
            )
        )


def test_resolver_skips_over_limit(store):
    """Ключ с превышенным monthly_limit_usd должен быть пропущен."""
    k1 = store.create(
        provider="deepgram", label="dg", secret="s1", priority=1,
        monthly_limit_usd=1.0,
    )
    store.create(provider="assemblyai", label="aa", secret="s2", priority=2)

    store.record_usage(
        key_id=k1["id"], job_id="x", operation="transcribe",
        provider="deepgram", model="nova-3", status="ok", cost_usd=1.5,
    )

    resolver = KeyResolver(store)
    calls: list[str] = []

    async def call(key, secret):
        calls.append(key["provider"])
        return UsageResult(
            result={}, provider=key["provider"], model="m", cost_usd=0.0
        )

    asyncio.run(
        resolver.run_with_fallback(
            kind="transcription",
            job_id="j4",
            operation="transcribe",
            provider=None,
            call=call,
        )
    )
    assert "deepgram" not in calls
    assert "assemblyai" in calls


def test_resolver_explicit_provider(store):
    store.create(provider="deepgram", label="dg", secret="s1", priority=1)
    store.create(provider="groq_whisper", label="gr", secret="s2", priority=2)
    resolver = KeyResolver(store)

    calls: list[str] = []

    async def call(key, secret):
        calls.append(key["provider"])
        return UsageResult(result={}, provider=key["provider"], model="m", cost_usd=0)

    asyncio.run(
        resolver.run_with_fallback(
            kind="transcription",
            job_id="j5",
            operation="transcribe",
            provider="groq_whisper",
            call=call,
        )
    )
    assert calls == ["groq_whisper"]
