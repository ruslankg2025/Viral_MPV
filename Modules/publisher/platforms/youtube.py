"""YouTube Shorts адаптер (Data API v3) — live-публикация через httpx.

Поток (всё по официальной документации Data API v3):
  1. OAuth 2.0 refresh: POST https://oauth2.googleapis.com/token
     (grant_type=refresh_token, client_id, client_secret, refresh_token)
     → access_token. refresh_token заранее получен offline-флоу со scope
     https://www.googleapis.com/auth/youtube.upload.
  2. Resumable upload videos.insert:
       a) POST .../upload/youtube/v3/videos?uploadType=resumable&part=snippet,status
          с JSON-метаданными (snippet/status) и Authorization: Bearer <token>.
          В ответе НЕТ тела — upload-сессия лежит в заголовке `Location`.
       b) PUT <Location> с байтами файла, Content-Type: video/* — заливает медиа.
          Тело ответа PUT — это финальный videos.insert resource (JSON) с "id".
  3. videoId = response["id"] → возвращаем {"external_id": video_id, ...}.

Почему resumable, а не multipart:
  Resumable — канонический и устойчивый путь для видео (большие файлы,
  возможность докачки при обрыве, без буферизации составного тела). Чёткое
  двухшаговое разделение «метаданные → Location → PUT байт» к тому же прозрачно
  тестируется MockTransport. Multipart (uploadType=multipart, один POST со
  составным телом) проще, но менее надёжен на больших файлах и не даёт докачки —
  оставлен как осознанный отказ. Саму докачку (Content-Range / 308 Resume
  Incomplete) НЕ реализуем: файлы Shorts малы (вертикаль <60s), один PUT целиком
  достаточен; точку расширения отмечаем комментарием.

Shorts: YouTube классифицирует ролик как Short по вертикали 9:16 и наличию
"#Shorts" в заголовке/описании — отдельного API-флага для Shorts НЕТ. Поэтому к
заголовку добавляется суффикс " #Shorts" (см. _shorts_title). Вертикальность —
ответственность пайплайна, не адаптера.

privacyStatus: по умолчанию "private" (безопасность — не палить ролик до ревью).
Сменить на "public" можно одним полем в _build_metadata, но осознанно оставляем
приватность дефолтом для live-пути этого сервиса.

Квота: free quota ~10 000 units/day; videos.insert стоит ~1600 units → максимум
~6 публикаций в сутки на проект. Превышение → ошибка quotaExceeded (HTTP 403)
маппится в PlatformError.

КРИТИЧНО: модуль НЕ должен вызываться в dry-run — реальные HTTP к Google делает
только он.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from platforms.base import NotConfiguredError, PlatformError, PlatformPublisher

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = (
    "https://www.googleapis.com/upload/youtube/v3/videos"
    "?uploadType=resumable&part=snippet,status"
)
# privacyStatus по умолчанию — см. докстринг модуля.
DEFAULT_PRIVACY_STATUS = "private"
# Категория 22 = "People & Blogs" — безопасный дефолт; snippet.categoryId не
# является строго обязательным, но без него часть регионов возвращает
# invalidCategoryId. Фиксируем явно.
DEFAULT_CATEGORY_ID = "22"


class YouTubeShortsPublisher(PlatformPublisher):
    platform_id = "youtube_shorts"

    def __init__(self, settings, *, timeout: float = 120.0):
        super().__init__(settings)
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(
            self.settings.youtube_client_id
            and self.settings.youtube_client_secret
            and self.settings.youtube_refresh_token
        )

    # ── publish ───────────────────────────────────────────────────────────────
    async def publish(
        self,
        *,
        video_path: str | None,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise NotConfiguredError("youtube_not_configured")
        if not video_path or not os.path.exists(video_path):
            raise PlatformError(f"youtube_video_not_found: {video_path}")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            access_token = await self._refresh_access_token(client)
            video_id = await self._resumable_upload(
                client,
                access_token=access_token,
                video_path=video_path,
                title=title,
                description=description,
                tags=tags,
            )
        return {"external_id": video_id, "url": f"https://youtube.com/shorts/{video_id}"}

    # ── OAuth refresh ─────────────────────────────────────────────────────────
    async def _refresh_access_token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": self.settings.youtube_client_id,
                "client_secret": self.settings.youtube_client_secret,
                "refresh_token": self.settings.youtube_refresh_token,
            },
        )
        data = self._json_or_error(resp, stage="oauth_refresh")
        token = data.get("access_token")
        if not token:
            # Тело без access_token (напр. {"error":"invalid_grant"}) — токен протух.
            raise PlatformError(f"youtube_oauth_no_access_token: {data}")
        return token

    # ── resumable videos.insert ───────────────────────────────────────────────
    async def _resumable_upload(
        self,
        client: httpx.AsyncClient,
        *,
        access_token: str,
        video_path: str,
        title: str,
        description: str,
        tags: list[str] | None,
    ) -> str:
        metadata = self._build_metadata(title=title, description=description, tags=tags)

        # Шаг 1: инициируем сессию — метаданные JSON, апстрим вернёт Location.
        init = await client.post(
            UPLOAD_URL,
            json=metadata,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                # X-Upload-Content-Type помогает апстриму заранее знать тип медиа.
                "X-Upload-Content-Type": "video/*",
            },
        )
        if init.status_code not in (200, 201):
            # 403 quotaExceeded / 401 invalid token и т.п. — детали в теле.
            self._raise_api_error(init, stage="resumable_init")
        # httpx нормализует заголовки регистронезависимо, но подстрахуемся.
        upload_url = init.headers.get("location") or init.headers.get("Location")
        if not upload_url:
            raise PlatformError("youtube_resumable_no_location_header")

        # Шаг 2: PUT байтов файла в сессию. Тело ответа — финальный resource с "id".
        # NOTE: одним PUT целиком (без Content-Range / докачки 308) — файлы Shorts
        # малы. Точка расширения: chunked upload с обработкой 308 Resume Incomplete.
        with open(video_path, "rb") as fh:
            body = fh.read()
        put = await client.put(
            upload_url,
            content=body,
            headers={"Content-Type": "video/*"},
        )
        data = self._json_or_error(put, stage="resumable_upload")
        video_id = data.get("id")
        if not video_id:
            raise PlatformError(f"youtube_insert_no_id: {data}")
        return video_id

    # ── helpers ───────────────────────────────────────────────────────────────
    def _build_metadata(
        self, *, title: str, description: str, tags: list[str] | None
    ) -> dict[str, Any]:
        snippet: dict[str, Any] = {
            "title": _shorts_title(title),
            "description": description,
            "categoryId": DEFAULT_CATEGORY_ID,
        }
        if tags:
            snippet["tags"] = [t.lstrip("#") for t in tags]
        return {
            "snippet": snippet,
            "status": {
                "privacyStatus": DEFAULT_PRIVACY_STATUS,
                # selfDeclaredMadeForKids обязателен по YouTube COPPA-политике;
                # False = не детский контент (дефолт для виральных Shorts).
                "selfDeclaredMadeForKids": False,
            },
        }

    @staticmethod
    def _json_or_error(resp: httpx.Response, *, stage: str) -> dict[str, Any]:
        """Парсит JSON, маппя HTTP- и API-ошибки в PlatformError."""
        if resp.status_code >= 400:
            YouTubeShortsPublisher._raise_api_error(resp, stage=stage)
        try:
            return resp.json()
        except ValueError as exc:
            raise PlatformError(f"youtube_{stage}_bad_json: {exc}") from exc

    @staticmethod
    def _raise_api_error(resp: httpx.Response, *, stage: str) -> None:
        # Google API ошибки: {"error": {"code":..., "message":..., "errors":[...]}}
        # или OAuth-форма {"error":"invalid_grant","error_description":...}.
        detail: str
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            if isinstance(err, dict):
                detail = err.get("message") or str(err)
            else:
                desc = data.get("error_description", "")
                detail = f"{err}: {desc}".strip(": ") if desc else str(err)
        else:
            detail = resp.text[:300]
        raise PlatformError(f"youtube_{stage}_error[{resp.status_code}]: {detail}")


def _shorts_title(title: str) -> str:
    """Добавляет суффикс ' #Shorts' (идемпотентно, без дублей)."""
    base = title.strip()
    if "#shorts" in base.lower():
        return base
    return f"{base} #Shorts"
