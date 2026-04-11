"""Выбор ключа для job-а с поддержкой fallback chain и лимитов."""

from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from keys.pricing import provider_kind
from keys.store import KeyKind, KeyStore
from logging_setup import get_logger

log = get_logger("keys.resolver")

T = TypeVar("T")


@dataclass
class UsageResult(Generic[T]):
    result: T
    provider: str
    model: str
    cost_usd: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    audio_seconds: float | None = None
    frames: int | None = None
    latency_ms: int | None = None


class NoProviderAvailable(RuntimeError):
    pass


class KeyResolver:
    def __init__(self, store: KeyStore):
        self.store = store

    def candidates(self, kind: KeyKind, provider: str | None = None) -> list[dict[str, Any]]:
        keys = self.store.list_active(kind, provider=provider)
        # Отсеиваем ключи, превысившие monthly_limit_usd
        filtered: list[dict[str, Any]] = []
        for k in keys:
            limit = k.get("monthly_limit_usd")
            if limit is not None:
                spent = self.store.month_cost(k["id"])
                if spent >= limit:
                    log.warning("key_limit_exceeded", key_id=k["id"], spent=spent, limit=limit)
                    continue
            filtered.append(k)
        return filtered

    async def run_with_fallback(
        self,
        *,
        kind: KeyKind,
        job_id: str,
        operation: str,
        provider: str | None,
        call: Callable[[dict[str, Any], str], Any],
    ) -> UsageResult[Any]:
        """
        `call(key_record, secret) -> UsageResult` — асинхронная функция, которая
        вызывает провайдера и возвращает результат + метрики использования.

        При исключении переходит к следующему ключу. Все попытки логируются.
        """
        keys = self.candidates(kind, provider=provider)
        if not keys:
            raise NoProviderAvailable(f"no active keys for kind={kind} provider={provider}")

        last_error: Exception | None = None
        for k in keys:
            secret = self.store.get_secret(k["id"])
            if not secret:
                continue
            try:
                usage: UsageResult = await call(k, secret)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                log.warning("provider_error", key_id=k["id"], provider=k["provider"], error=err)
                self.store.record_usage(
                    key_id=k["id"],
                    job_id=job_id,
                    operation=operation,
                    provider=k["provider"],
                    model="",
                    status="error",
                    cost_usd=0.0,
                    error=err[:500],
                )
                last_error = e
                continue

            self.store.record_usage(
                key_id=k["id"],
                job_id=job_id,
                operation=operation,
                provider=usage.provider,
                model=usage.model,
                status="ok",
                cost_usd=usage.cost_usd,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                audio_seconds=usage.audio_seconds,
                frames=usage.frames,
                latency_ms=usage.latency_ms,
            )
            return usage

        raise NoProviderAvailable(
            f"all {len(keys)} providers failed for kind={kind}: last_error={last_error}"
        )
