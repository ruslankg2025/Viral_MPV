"""Self-improvement loop: периодически авто-обновляет prompt-profile
по всем аккаунтам на основе performance + feedback.

PLAN_SELF_LEARNING_AGENT этап 5: «без кнопок». Раз в N часов проходит
по profile.accounts, для каждого собирает performance из monitor +
feedback из script, вызывает script.improve_prompt, и если status=
'improved' — создаёт новую версию prompt-profile в profile-сервисе.

Performance — ГЛАВНЫЙ сигнал (важнее оценок). См. meta-prompt в
script/router.py::_IMPROVE_SYSTEM_PROMPT.
"""
from __future__ import annotations

import asyncio
import os

import httpx

from orchestrator.logging_setup import get_logger

log = get_logger("auto_improve")


def _hours_to_seconds(h: float) -> float:
    return h * 3600.0


async def run_auto_improve_loop():
    """Бесконечный async-loop. Запускается из shell main.lifespan
    как create_task. Cancellable.
    """
    interval_h = float(os.getenv("AUTO_IMPROVE_INTERVAL_HOURS", "24"))
    enabled = os.getenv("AUTO_IMPROVE_ENABLED", "true").lower() in ("1", "true", "yes")
    days = int(os.getenv("AUTO_IMPROVE_DAYS", "14"))
    min_events = int(os.getenv("AUTO_IMPROVE_MIN_EVENTS", "3"))

    if not enabled:
        log.info("auto_improve_disabled")
        return

    log.info(
        "auto_improve_started",
        interval_hours=interval_h, days=days, min_events=min_events,
    )

    # Первый прогон делаем через 5 минут после старта shell (чтобы не
    # бить сразу при деплое — даём системе устаканиться).
    initial_delay = 300.0
    while True:
        try:
            await asyncio.sleep(initial_delay)
            initial_delay = _hours_to_seconds(interval_h)
            await _run_one_cycle(days=days, min_events=min_events)
        except asyncio.CancelledError:
            log.info("auto_improve_stopped")
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("auto_improve_cycle_error", error=str(e)[:200])


async def _run_one_cycle(*, days: int, min_events: int) -> None:
    """Один проход: GET accounts → для каждого orchestrate improve."""
    profile_url = os.getenv("PROFILE_URL", "http://profile:8000").rstrip("/")
    profile_token = os.getenv("PROFILE_TOKEN", "")
    monitor_url = os.getenv("MONITOR_URL", "http://monitor:8000").rstrip("/")
    monitor_token = os.getenv("MONITOR_TOKEN", "")
    script_url = os.getenv("SCRIPT_URL", "http://script:8000").rstrip("/")
    script_token = os.getenv("SCRIPT_TOKEN", "")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1. Список accounts
        try:
            r = await client.get(
                f"{profile_url}/profile/accounts",
                headers={"X-Token": profile_token},
            )
            r.raise_for_status()
            accounts = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("auto_improve_no_accounts", error=str(e)[:200])
            return

        if not accounts:
            log.info("auto_improve_no_accounts_to_process")
            return

        log.info("auto_improve_cycle_start", accounts=len(accounts))
        improved = 0
        for acc in accounts:
            aid = acc.get("id")
            if not aid:
                continue
            try:
                if await _improve_one_account(
                    client, aid, days, min_events,
                    profile_url, profile_token,
                    monitor_url, monitor_token,
                    script_url, script_token,
                ):
                    improved += 1
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "auto_improve_account_failed", account_id=aid, error=str(e)[:200]
                )
        log.info("auto_improve_cycle_done", accounts=len(accounts), improved=improved)


async def _improve_one_account(
    client: httpx.AsyncClient,
    account_id: str,
    days: int,
    min_events: int,
    profile_url: str, profile_token: str,
    monitor_url: str, monitor_token: str,
    script_url: str, script_token: str,
) -> bool:
    """Возвращает True если был сохранён новый prompt-profile."""
    # 2. Текущий prompt из profile-сервиса
    pp_resp = await client.get(
        f"{profile_url}/profile/accounts/{account_id}/prompt-profile",
        headers={"X-Token": profile_token},
    )
    if pp_resp.status_code != 200:
        log.info("auto_improve_skip_no_prompt", account_id=account_id)
        return False
    pp = pp_resp.json()
    if not pp or not pp.get("system_prompt"):
        return False
    current_prompt = pp["system_prompt"]

    # 3. Performance из monitor
    perf = None
    try:
        m = await client.get(
            f"{monitor_url}/monitor/accounts/{account_id}/performance-summary",
            headers={"X-Token": monitor_token},
            params={"days": days},
        )
        if m.status_code == 200:
            perf = m.json()
    except Exception as e:  # noqa: BLE001
        log.info("auto_improve_no_perf", account_id=account_id, error=str(e)[:120])

    # 4. POST script.improve-prompt с performance + feedback (script сам читает feedback)
    s = await client.post(
        f"{script_url}/script/improve-prompt",
        headers={"X-Worker-Token": script_token, "Content-Type": "application/json"},
        json={
            "account_id": account_id,
            "current_prompt": current_prompt,
            "days": days,
            "min_events": min_events,
            "performance": perf,
        },
    )
    if s.status_code != 200:
        log.info(
            "auto_improve_script_failed",
            account_id=account_id, status=s.status_code,
        )
        return False

    resp = s.json()
    status = resp.get("status")
    if status != "improved":
        log.info(
            "auto_improve_no_change",
            account_id=account_id, status=status,
            feedback_count=resp.get("feedback_count"),
        )
        return False

    suggested = resp.get("suggested_prompt")
    if not suggested:
        return False

    # 5. POST новой версии prompt-profile в profile-сервис
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    new_version = f"auto-{ts}"
    save_r = await client.post(
        f"{profile_url}/profile/accounts/{account_id}/prompt-profile",
        headers={"X-Token": profile_token, "Content-Type": "application/json"},
        json={
            "version": new_version,
            "system_prompt": suggested,
            "modifiers": {
                "_auto_improvement": True,
                "_rationale": resp.get("rationale"),
                "_feedback_count": resp.get("feedback_count"),
                "_performance_used": resp.get("performance_used"),
                "_cost_usd": resp.get("cost_usd"),
            },
            "hard_constraints": {},
            "soft_constraints": {},
        },
    )
    if save_r.status_code in (200, 201):
        log.info(
            "auto_improve_saved",
            account_id=account_id, version=new_version,
            performance_used=resp.get("performance_used"),
            cost_usd=resp.get("cost_usd"),
        )
        return True
    log.warning(
        "auto_improve_save_failed",
        account_id=account_id, status=save_r.status_code,
    )
    return False
