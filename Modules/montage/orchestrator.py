"""Детерминированный оркестратор автомонтажа (фаза A0: сквозной single-cam).

Rule Zero: монтаж ТОЛЬКО через инструменты OpenMontage (`registry.get(name).execute(inputs)`),
не ad-hoc ffmpeg и без живого агента. Паттерн скопирован с рабочего
`OpenMontage/scripts/kling_official_animated_explainer_e2e.py`:
  1) sys.path → OpenMontage root, load_env, registry.clear()+discover("tools")
  2) preflight статусов инструментов (block до тяжёлых шагов)
  3) упорядоченная цепочка .execute(), каждый шаг guard `if not result.success: raise`,
     прокидываем result.data[...] в следующий шаг.

A0-цепочка (single-cam, сырьё → 4К-вертикаль):
  transcriber (input_path → segments+word_timestamps; артефакт для будущей A1-нарезки)
  → auto_reframe (target_width/height=2160/3840; ЯВНО читает rotation-метадату)
  → video_compose op=compose (edit_decisions.cuts + compose_target 2160×3840 fit=cover;
     именованного 4К-вертикаль профиля в media_profiles НЕТ — задаём через compose_target)
  → visual_qa op=probe (expected w/h/has_audio → validation_passed) как QC-гейт.

✅ Проверено smoke-прогоном (2026-09-02): АУДИО сохраняется сквозь
auto_reframe→compose (мастер вышел aac 48k stereo без отдельного audio_path).
Отдельный `audio_path` нужен только когда подмешиваем обработанную дорожку
(A4 loudnorm) — тогда он перекрывает исходную.

Запуск (на сервере, где стоит OpenMontage):
  OPENMONTAGE_ROOT=/home/claw/claudeclaw/workspace/OpenMontage \
  python orchestrator.py --source /path/front.mp4 --out-dir /path/out \
    --model tiny --width 2160 --height 3840
Лёгкий smoke: короткий клип + --model tiny + --width 1080 --height 1920.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── подключение OpenMontage (его venv/пакет должны быть доступны в этом окружении) ──
def _bootstrap_openmontage() -> Any:
    """sys.path → OpenMontage root, автозагрузка .env, discover инструментов.
    Возвращает singleton registry. Копия bootstrap-паттерна kling-скрипта."""
    root = os.environ.get("OPENMONTAGE_ROOT", "/home/claw/claudeclaw/workspace/OpenMontage")
    root_p = Path(root)
    if not root_p.is_dir():
        raise RuntimeError(f"OPENMONTAGE_ROOT не найден: {root}")
    if str(root_p) not in sys.path:
        sys.path.insert(0, str(root_p))
    try:
        from lib.env_loader import load_env  # type: ignore
        load_env(root_p)
    except Exception as e:  # noqa: BLE001 — env опционален (ключи могут быть в окружении)
        _log("warn", f"load_env: {e}")
    from tools.tool_registry import registry  # type: ignore
    registry.clear()
    registry.discover("tools")
    return registry


def _log(level: str, msg: str) -> None:
    print(f"[montage:{level}] {msg}", flush=True)


@dataclass
class StageResult:
    name: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0


@dataclass
class MontageResult:
    ok: bool
    output_path: str | None = None
    stages: list[StageResult] = field(default_factory=list)
    error: str | None = None
    report: dict[str, Any] = field(default_factory=dict)


# A0 нужны эти инструменты OpenMontage
_REQUIRED_TOOLS = ("transcriber", "auto_reframe", "video_compose", "visual_qa")


def _preflight(registry: Any) -> None:
    missing, unavailable = [], []
    for name in _REQUIRED_TOOLS:
        tool = registry.get(name)
        if tool is None:
            missing.append(name)
            continue
        try:
            status = tool.get_status().value
        except Exception as e:  # noqa: BLE001
            status = f"error:{e}"
        if status != "available":
            unavailable.append(f"{name}={status}")
    if missing:
        raise RuntimeError(f"инструменты не найдены в registry: {missing}")
    if unavailable:
        raise RuntimeError(f"инструменты недоступны (deps): {unavailable}")


def _run_tool(registry: Any, name: str, inputs: dict[str, Any]) -> StageResult:
    """Вызов одного инструмента с guard'ом и таймингом (как _require_success в kling)."""
    tool = registry.get(name)
    if tool is None:
        return StageResult(name, False, error="tool_not_registered")
    t0 = time.monotonic()
    try:
        res = tool.execute(inputs)
    except Exception as e:  # noqa: BLE001
        return StageResult(name, False, error=f"exception:{e}", duration_s=time.monotonic() - t0)
    dur = time.monotonic() - t0
    if not getattr(res, "success", False):
        return StageResult(name, False, error=getattr(res, "error", "unknown"), duration_s=dur)
    return StageResult(name, True, data=dict(res.data or {}),
                       artifacts=list(res.artifacts or []), duration_s=dur)


def run_montage(
    *,
    source_video: str,
    out_dir: str,
    audio_path: str | None = None,
    language: str | None = "ru",
    model_size: str = "base",
    width: int = 2160,
    height: int = 3840,
) -> MontageResult:
    """A0: сквозной прогон одного клипа → вертикальный мастер, через инструменты OpenMontage."""
    src = Path(source_video)
    if not src.is_file():
        return MontageResult(False, error=f"source_not_found:{source_video}")
    out = Path(out_dir)
    (out / "assets").mkdir(parents=True, exist_ok=True)
    (out / "renders").mkdir(parents=True, exist_ok=True)

    registry = _bootstrap_openmontage()
    _preflight(registry)

    stages: list[StageResult] = []

    # 1) Транскрипт (segments + word_timestamps; артефакт для A1-нарезки по словам)
    st = _run_tool(registry, "transcriber", {
        "input_path": str(src),
        "model_size": model_size,          # A0 smoke: "tiny"; прод: "base"/"large-v3"
        "language": language,              # None → авто-детект
        "output_dir": str(out / "assets"),
    })
    stages.append(st)
    if not st.ok:
        return MontageResult(False, stages=stages, error=f"transcribe:{st.error}")
    n_words = len(st.data.get("word_timestamps") or [])
    _log("info", f"transcribe ok: {n_words} слов, lang={st.data.get('language')}")

    # 2) Нормализация вертикали (auto_reframe ЯВНО читает rotation → target WxH)
    normalized = out / "assets" / "normalized.mp4"
    st = _run_tool(registry, "auto_reframe", {
        "input_path": str(src),
        "target_width": int(width),
        "target_height": int(height),
        "output_path": str(normalized),
    })
    stages.append(st)
    if not st.ok:
        return MontageResult(False, stages=stages, error=f"auto_reframe:{st.error}")
    normalized_out = st.data.get("output") or str(normalized)
    _log("info", f"auto_reframe ok: {st.data.get('output_resolution')} → {normalized_out}")

    # 3) Сборка мастера (video_compose op=compose; 4К-вертикаль через compose_target,
    #    т.к. именованного профиля 2160×3840 в media_profiles нет). Один cut = весь клип.
    final_path = out / "renders" / "master.mp4"
    # длительность cut — из транскрипта (duration_seconds), иначе без out_seconds
    st_dur = None
    for s in stages:
        if s.name == "transcriber":
            st_dur = s.data.get("duration_seconds")
    cut: dict[str, Any] = {"id": "cut-001", "source": normalized_out, "in_seconds": 0, "speed": 1.0}
    if st_dur:
        cut["out_seconds"] = float(st_dur)
    edit_decisions = {
        "version": "1.0",
        "render_runtime": "ffmpeg",
        "renderer_family": "video_concat_smoke",
        "cuts": [cut],
        "subtitles": {"enabled": False},   # субтитры — фаза A3
        "metadata": {
            "pipeline": "vm-talking-head",
            "compose_target": {"width": int(width), "height": int(height), "fit": "cover"},
        },
    }
    compose_inputs: dict[str, Any] = {
        "operation": "compose",
        "edit_decisions": edit_decisions,
        "output_path": str(final_path),
        "crf": 18,
        "preset": "medium",
    }
    # ⚠️ аудио: если auto_reframe/compose не сохраняют дорожку — подать audio_path.
    if audio_path:
        compose_inputs["audio_path"] = audio_path
    st = _run_tool(registry, "video_compose", compose_inputs)
    stages.append(st)
    if not st.ok:
        return MontageResult(False, stages=stages, error=f"compose:{st.error}")
    master = st.data.get("output") or str(final_path)
    _log("info", f"compose ok → {master}")

    # 4) QC-гейт: probe + expected (validation_passed). Не блокируем жёстко — репортим.
    st = _run_tool(registry, "visual_qa", {
        "operation": "probe",
        "input_path": master,
        "expected": {"width": int(width), "height": int(height), "has_audio": True},
    })
    stages.append(st)
    qc_passed = bool(st.ok and st.data.get("validation_passed"))
    qc_issues = st.data.get("validation_issues") if st.ok else [st.error]
    _log("info" if qc_passed else "warn", f"QC passed={qc_passed} issues={qc_issues}")

    report = {
        "source": str(src),
        "master": master,
        "qc_passed": qc_passed,
        "qc_issues": qc_issues,
        "stages": [{"name": s.name, "ok": s.ok, "error": s.error, "duration_s": round(s.duration_s, 2)} for s in stages],
    }
    (out / "renders" / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return MontageResult(True, output_path=master, stages=stages, report=report)


def main() -> int:
    ap = argparse.ArgumentParser(description="A0 montage orchestrator (single-cam → vertical master)")
    ap.add_argument("--source", required=True, help="исходный клип (talking-head)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--audio", default=None, help="отдельная аудиодорожка (если нужна)")
    ap.add_argument("--language", default="ru")
    ap.add_argument("--model", default="base", help="whisper model_size (smoke: tiny)")
    ap.add_argument("--width", type=int, default=2160)
    ap.add_argument("--height", type=int, default=3840)
    args = ap.parse_args()
    res = run_montage(
        source_video=args.source, out_dir=args.out_dir, audio_path=args.audio,
        language=args.language, model_size=args.model, width=args.width, height=args.height,
    )
    print(json.dumps(res.report or {"error": res.error}, ensure_ascii=False, indent=2))
    return 0 if res.ok and res.report.get("qc_passed") else (0 if res.ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
