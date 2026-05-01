from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.config import OrchestratorSettings

if TYPE_CHECKING:
    from insights.store import InsightsStore
    from orchestrator.clients.monitor import MonitorClient
    from orchestrator.clients.profile import ProfileClient
    from orchestrator.clients.script import ScriptClient
    from orchestrator.runs.runner import RunRunner
    from orchestrator.runs.store import RunStore


class AppState:
    settings: OrchestratorSettings
    run_store: "RunStore"
    runner: "RunRunner"
    monitor_client: "MonitorClient"
    script_client: "ScriptClient | None" = None
    profile_client: "ProfileClient | None" = None
    insights_store: "InsightsStore | None" = None


state = AppState()
