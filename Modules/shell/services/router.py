"""
Services dashboard — статус подключённых платных API.

Юзер хочет одно место где видно: где сколько потрачено, где упёрлись в лимит,
где остался ключ. Сейчас единственный сервис с публичным billing API,
который работает с обычным token-ом, — Apify (`/v2/users/me/usage/monthly`
+ `/v2/users/me/limits`). Остальные (OpenAI, Anthropic, Deepgram, AssemblyAI,
Groq, Gemini) либо требуют admin-key / project-id discovery, либо вовсе не
имеют публичного usage-эндпоинта — для них показываем только наличие ключа
в env + кнопку «Открыть консоль».

Кэшируем 5 минут чтобы не ходить в Apify на каждый рендер.
"""
import os
import time
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends

from auth.deps import require_auth

router = APIRouter(prefix="/api/services", tags=["services"],
    # Дашборд сервисов показывает состояние инфраструктуры и расходы —
    # не то, что стоит отдавать анониму. При AUTH_ENABLED=false
    # зависимость пропускает всех, как и раньше.
    dependencies=[Depends(require_auth)],
)
log = structlog.get_logger("services")

CACHE_TTL_SEC = 300
_CACHE: dict[str, tuple[float, dict]] = {}

# Консоли — куда юзер кликает чтобы посмотреть детали биллинга в первоисточнике.
SERVICE_CONSOLES = {
    "apify":      "https://console.apify.com/billing",
    "openai":     "https://platform.openai.com/usage",
    "anthropic":  "https://console.anthropic.com/settings/billing",
    "assemblyai": "https://www.assemblyai.com/app/account",
    "deepgram":   "https://console.deepgram.com/usage",
    "groq":       "https://console.groq.com/settings/limits",
    "gemini":     "https://aistudio.google.com/app/apikey",
}

# Список «прочих» сервисов: показываем по факту наличия env-key.
# Шелл загружает .env.shell + .env.monitor + .env.profile — APIFY_TOKEN
# виден отсюда. Чтобы видеть OPENAI/ANTHROPIC/DEEPGRAM/ASSEMBLYAI/GROQ/GEMINI
# прямо из shell, нужно добавить .env.processor/.env.knowledge/.env.downloader
# в env_file shell-сервиса в docker-compose.yml (в TODO).
OTHER_SERVICES = [
    ("OpenAI",      "openai",     ["OPENAI_API_KEY", "BOOTSTRAP_OPENAI_API_KEY"]),
    ("Anthropic",   "anthropic",  ["ANTHROPIC_API_KEY", "BOOTSTRAP_ANTHROPIC_API_KEY"]),
    ("AssemblyAI",  "assemblyai", ["ASSEMBLYAI_API_KEY", "BOOTSTRAP_ASSEMBLYAI_API_KEY"]),
    ("Deepgram",    "deepgram",   ["DEEPGRAM_API_KEY", "BOOTSTRAP_DEEPGRAM_API_KEY"]),
    ("Groq",        "groq",       ["GROQ_API_KEY", "BOOTSTRAP_GROQ_API_KEY"]),
    ("Gemini",      "gemini",     ["GOOGLE_GEMINI_API_KEY", "BOOTSTRAP_GOOGLE_GEMINI_API_KEY"]),
]


def _walk_find_number(d: Any, name_substrings: tuple[str, ...]) -> float | None:
    """Recursively walk dict and return first numeric value whose key contains
    any of `name_substrings` (case-insensitive). Used для устойчивого
    извлечения hard-limit / used-spend из ответа Apify независимо от того,
    лежат ли поля в .data.current.*, .data.*, .data.limits.*, и т.п.
    """
    if isinstance(d, dict):
        for k, v in d.items():
            kl = k.lower()
            if isinstance(v, (int, float)) and any(s in kl for s in name_substrings):
                return float(v)
        for v in d.values():
            found = _walk_find_number(v, name_substrings)
            if found is not None:
                return found
    elif isinstance(d, list):
        for v in d:
            found = _walk_find_number(v, name_substrings)
            if found is not None:
                return found
    return None


async def _fetch_apify_status(
    client: httpx.AsyncClient, token: str, *, debug: bool = False
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    base = "https://api.apify.com/v2/users/me"
    # Sequential, не gather: параллельные запросы к /users/me/* у Apify иногда
    # ловят 403 «insufficient-permissions» (rate-limit-подобное поведение),
    # хотя последовательные с тем же токеном проходят нормально.
    try:
        usage_resp = await client.get(f"{base}/usage/monthly", headers=headers)
    except Exception as e:
        log.warning("apify_usage_network_error", error=str(e))
        return {
            "key":         "apify",
            "name":        "Apify",
            "configured":  True,
            "status":      "error",
            "error":       f"network: {e}",
            "console_url": SERVICE_CONSOLES["apify"],
        }
    if usage_resp.status_code != 200:
        log.warning("apify_usage_http_error",
                    status=usage_resp.status_code,
                    body=usage_resp.text[:300])
        return {
            "key":         "apify",
            "name":        "Apify",
            "configured":  True,
            "status":      "error",
            "error":       f"HTTP {usage_resp.status_code}: {usage_resp.text[:300]}",
            "console_url": SERVICE_CONSOLES["apify"],
        }
    usage_raw = usage_resp.json() or {}
    usage = usage_raw.get("data", {}) or {}
    # /limits — best-effort: если упадёт (Apify иногда 403 подряд за параллель),
    # просто покажем usage без лимита.
    limits_raw: dict = {}
    limits: dict = {}
    limits_status: int | str = "skipped"
    try:
        limits_resp = await client.get(f"{base}/limits", headers=headers)
        limits_status = limits_resp.status_code
        if limits_resp.status_code == 200:
            limits_raw = limits_resp.json() or {}
            limits = limits_raw.get("data", {}) or {}
        else:
            log.warning("apify_limits_http_error",
                        status=limits_resp.status_code,
                        body=limits_resp.text[:200])
    except Exception as e:
        limits_status = f"err:{e}"
        log.warning("apify_limits_network_error", error=str(e))

    # ── used_usd: пробуем плоское поле, потом сумму monthlyServiceUsage ──
    used_usd: float | None = (
        usage.get("monthlyUsageUsd")
        or usage.get("usageUsd")
    )
    if used_usd is None:
        msu = usage.get("monthlyServiceUsage")
        if isinstance(msu, dict):
            used_usd = sum(
                float(v.get("amountAfterVolumeDiscountUsd") or v.get("baseAmountUsd") or 0)
                for v in msu.values() if isinstance(v, dict)
            )
        else:
            used_usd = 0.0
    used_usd = float(used_usd or 0)

    # ── limit_usd: рекурсивный поиск hard-limit в /limits.data ──
    # Apify v2 кладёт в limits.maxMonthlyUsageUsd (новая схема), легаси —
    # *.monthlyUsageHardLimitUsd. Substring-матч case-insensitive.
    limit_usd = _walk_find_number(
        limits,
        ("maxmonthlyusageusd", "monthlyusagehardlimit", "hardlimit"),
    )
    # Логируем найденные top-keys для диагностики структуры
    log.info("apify_status_fetched",
             usage_top_keys=list(usage.keys())[:15],
             limits_top_keys=list(limits.keys())[:15],
             limits_status=limits_status,
             used_usd=round(used_usd, 4),
             limit_usd=limit_usd)

    pct = round(used_usd / limit_usd * 100, 1) if (limit_usd and limit_usd > 0) else None
    # Severity: ok/warn/critical/blocked
    if pct is None:
        severity = "ok"
    elif pct >= 100:
        severity = "blocked"
    elif pct >= 90:
        severity = "critical"
    elif pct >= 70:
        severity = "warn"
    else:
        severity = "ok"
    out: dict[str, Any] = {
        "key":         "apify",
        "name":        "Apify",
        "configured":  True,
        "status":      "ok",
        "severity":    severity,
        "used_usd":    round(used_usd, 2),
        "limit_usd":   round(limit_usd, 2) if limit_usd is not None else None,
        "pct":         pct,
        "period":      "monthly",
        "console_url": SERVICE_CONSOLES["apify"],
    }
    if debug:
        out["_debug"] = {
            "limits_status":     limits_status,
            "usage_top_keys":    list(usage.keys()),
            "limits_top_keys":   list(limits.keys()),
            "limits_raw_sample": limits,  # полный /limits.data — billing-only data
        }
    return out


def _other_service_card(label: str, console_key: str, env_keys: list[str]) -> dict[str, Any]:
    found = next((k for k in env_keys if os.getenv(k)), None)
    return {
        "key":         console_key,
        "name":        label,
        "configured":  bool(found),
        "status":      "configured" if found else "missing",
        "severity":    "ok" if found else "warn",
        "env_var":     found,  # какой именно ключ нашли (для отладки)
        "console_url": SERVICE_CONSOLES[console_key],
        "note":        "Откройте консоль для usage/billing"
                       if found else "Ключ не найден в env",
    }


@router.get("/status")
async def get_services_status(
    force: bool = False, debug: bool = False
) -> dict[str, Any]:
    """Сводный статус всех платных сервисов. Кэш 5 мин (force=true — обновить).
    debug=true — добавить _debug-блок с raw-структурой ответа Apify (top-keys
    + полный /limits.data) для диагностики извлечения полей."""
    cache_key = "services_status"
    now = time.time()
    # debug всегда обходит кэш — кэш-объект без _debug, иначе придёт без него
    if not force and not debug:
        cached = _CACHE.get(cache_key)
        if cached and (now - cached[0]) < CACHE_TTL_SEC:
            return cached[1]

    services: list[dict[str, Any]] = []

    apify_token = os.getenv("APIFY_TOKEN")
    async with httpx.AsyncClient(timeout=10.0) as client:
        if apify_token:
            services.append(await _fetch_apify_status(client, apify_token, debug=debug))
        else:
            services.append({
                "key":         "apify",
                "name":        "Apify",
                "configured":  False,
                "status":      "missing",
                "severity":    "critical",
                "console_url": SERVICE_CONSOLES["apify"],
                "note":        "APIFY_TOKEN не задан в env",
            })

    for label, console_key, env_keys in OTHER_SERVICES:
        services.append(_other_service_card(label, console_key, env_keys))

    result = {"services": services, "fetched_at": int(now), "ttl_sec": CACHE_TTL_SEC}
    if not debug:
        _CACHE[cache_key] = (now, result)
    return result
