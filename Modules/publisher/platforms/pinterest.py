"""Pinterest адаптер (REST API v5).

Контракт зафиксирован фундаментом: класс PinterestPublisher,
platform_id='pinterest', зарегистрирован в platforms/__init__.py.

Особенность: Pinterest РФ-ограничен → для prod нужен residential proxy
(settings.http_proxy). Если он задан, передаётся в httpx.AsyncClient(proxy=...)
для ВСЕХ исходящих запросов (включая заливку файла на upload-эндпоинт S3).

Live-flow (строго по официальной доке API v5, https://developers.pinterest.com/docs/api/v5/):
  1. OAuth refresh: POST /v5/oauth/token (Basic auth app_id:app_secret,
     grant_type=refresh_token) → access_token.
  2. Видео-пин (Idea/video pin):
       a. POST /v5/media {media_type: "video"} → {media_id, upload_url, upload_parameters}.
       b. POST upload_url (S3-стиль): form-поля из upload_parameters + сам файл (ключ "file").
       c. Поллинг GET /v5/media/{media_id} до status == "succeeded" (обработка асинхронная).
       d. POST /v5/pins с board_id, title, description,
          media_source = {source_type: "video_id", media_id, cover_image_url?}.
  3. Ответ POST /v5/pins → {"id": "<pinId>", ...} → возвращаем {"external_id": pin_id}.

FALLBACK на image-pin:
  Video-flow в API v5 относительно новый и капризный (требует доступной
  cover-обложки, асинхронной обработки медиа, иногда — расширенного scope
  у приложения). Если видео недоступно (нет video_path / шаг media/upload/poll
  упал) — деградируем на обычный image-pin через media_source source_type:
  "image_url" (нужен публично доступный URL картинки). URL обложки берём из
  необязательной настройки; если его нет — это ОСОЗНАННАЯ точка отказа
  (PlatformError), а НЕ молчаливый «успех».

ТОЧКИ РИСКА (живого ключа нет, end-to-end не проверялся):
  * Idea/video pin (media_type=video) — относительно новый флоу v5; формат
    upload_parameters и имя файлового поля ("file") взяты из доки, но у разных
    приложений/скоупов поведение может отличаться.
  * cover_image_url для видео-пина: Pinterest требует обложку. Здесь её нет
    (нет генерации thumbnail), поэтому video-pin создаётся БЕЗ обложки, что
    Pinterest может отвергнуть → тогда сработает fallback на image-pin.
  * Лимиты: free tier ~1000 запросов/час на приложение — поллинг медиа делаем
    ограниченно (MEDIA_POLL_ATTEMPTS), чтобы не выжигать квоту.

КРИТИЧНО: этот модуль НЕ должен вызываться в dry-run — реальные HTTP к
api.pinterest.com делает только он.
"""
from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

import httpx

from platforms.base import (
    NotConfiguredError,
    PlatformError,
    PlatformPublisher,
    compose_caption,
)

API_BASE = "https://api.pinterest.com/v5"
OAUTH_URL = f"{API_BASE}/oauth/token"
MEDIA_URL = f"{API_BASE}/media"
PINS_URL = f"{API_BASE}/pins"

# Поллинг статуса медиа: обработка видео асинхронная. Держим в узде ради
# free-tier лимита (~1000 req/hour).
MEDIA_POLL_ATTEMPTS = 10
MEDIA_POLL_DELAY = 2.0  # сек между опросами


class PinterestPublisher(PlatformPublisher):
    platform_id = "pinterest"

    # Pinterest заголовок ограничен 100 символами (API v5). Обрезаем явно,
    # чтобы не словить 400 на длинном title.
    TITLE_MAX = 100

    def is_configured(self) -> bool:
        return bool(
            self.settings.pinterest_app_id
            and self.settings.pinterest_app_secret
            and self.settings.pinterest_refresh_token
            and self.settings.pinterest_board_id
        )

    # ── httpx-клиент с прокси ──────────────────────────────────────────────────
    def _client(self, *, timeout: float = 60.0) -> httpx.AsyncClient:
        """AsyncClient с прокси, если settings.http_proxy задан.

        Pinterest РФ-ограничен — весь трафик (включая заливку файла) идёт через
        residential proxy. httpx 0.27 принимает одиночный proxy=<url>.
        """
        kwargs: dict[str, Any] = {"timeout": timeout}
        proxy = (self.settings.http_proxy or "").strip()
        if proxy:
            kwargs["proxy"] = proxy
        return httpx.AsyncClient(**kwargs)

    # ── OAuth refresh ──────────────────────────────────────────────────────────
    async def _access_token(self, client: httpx.AsyncClient) -> str:
        """POST /v5/oauth/token (Basic app_id:app_secret) → access_token."""
        basic = base64.b64encode(
            f"{self.settings.pinterest_app_id}:{self.settings.pinterest_app_secret}".encode()
        ).decode()
        resp = await client.post(
            OAUTH_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.settings.pinterest_refresh_token,
            },
        )
        data = _json_or_error(resp, "oauth_token")
        token = data.get("access_token")
        if not token:
            raise PlatformError(f"pinterest_oauth_no_access_token: {data}")
        return token

    # ── Видео: register → upload → poll ────────────────────────────────────────
    async def _register_media(self, client: httpx.AsyncClient, token: str) -> dict[str, Any]:
        """POST /v5/media {media_type: video} → {media_id, upload_url, upload_parameters}."""
        resp = await client.post(
            MEDIA_URL,
            headers=_bearer(token),
            json={"media_type": "video"},
        )
        return _json_or_error(resp, "register_media")

    async def _upload_file(
        self,
        client: httpx.AsyncClient,
        *,
        upload_url: str,
        upload_parameters: dict[str, Any],
        video_path: str,
    ) -> None:
        """Заливка файла на upload_url (S3-стиль multipart).

        upload_parameters — словарь form-полей, которые Pinterest требует
        приложить ПЕРЕД файлом. Файловое поле называется "file" (по доке v5).
        Ответ S3 — обычно 204 No Content без тела.
        """
        with open(video_path, "rb") as fh:
            files = {"file": (os.path.basename(video_path), fh, "video/mp4")}
            # upload_parameters могут содержать любые значения — приводим к str.
            form = {k: str(v) for k, v in (upload_parameters or {}).items()}
            resp = await client.post(upload_url, data=form, files=files)
        # S3 отдаёт 200/201/204 без JSON — проверяем только статус.
        if resp.status_code >= 400:
            raise PlatformError(
                f"pinterest_media_upload_failed[{resp.status_code}]: {resp.text[:300]}"
            )

    async def _wait_media_ready(
        self, client: httpx.AsyncClient, token: str, media_id: str
    ) -> None:
        """Поллинг GET /v5/media/{media_id} до status == 'succeeded'.

        status ∈ {registered, processing, succeeded, failed} (по доке v5).
        Ограничиваем число попыток ради free-tier лимита.
        """
        for _ in range(MEDIA_POLL_ATTEMPTS):
            resp = await client.get(f"{MEDIA_URL}/{media_id}", headers=_bearer(token))
            data = _json_or_error(resp, "media_status")
            status = (data.get("status") or "").lower()
            if status == "succeeded":
                return
            if status == "failed":
                raise PlatformError(f"pinterest_media_processing_failed: {media_id}")
            await asyncio.sleep(MEDIA_POLL_DELAY)
        raise PlatformError(f"pinterest_media_not_ready: {media_id}")

    # ── Создание пина ──────────────────────────────────────────────────────────
    async def _create_pin(
        self,
        client: httpx.AsyncClient,
        token: str,
        media_source: dict[str, Any],
        *,
        title: str,
        description: str,
    ) -> str:
        """POST /v5/pins → возвращает pin id (поле 'id')."""
        body = {
            "board_id": self.settings.pinterest_board_id,
            "title": title[: self.TITLE_MAX],
            "description": description,
            "media_source": media_source,
        }
        resp = await client.post(PINS_URL, headers=_bearer(token), json=body)
        data = _json_or_error(resp, "create_pin")
        pin_id = data.get("id")
        if not pin_id:
            raise PlatformError(f"pinterest_pin_no_id: {data}")
        return str(pin_id)

    async def _publish_video_pin(
        self,
        client: httpx.AsyncClient,
        token: str,
        *,
        video_path: str,
        title: str,
        description: str,
    ) -> str:
        """Полный video-flow: media → upload → poll → pin (video_id)."""
        media = await self._register_media(client, token)
        media_id = media.get("media_id")
        upload_url = media.get("upload_url")
        if not media_id or not upload_url:
            raise PlatformError(f"pinterest_media_incomplete: {media}")
        await self._upload_file(
            client,
            upload_url=upload_url,
            upload_parameters=media.get("upload_parameters") or {},
            video_path=video_path,
        )
        await self._wait_media_ready(client, token, str(media_id))
        # ТОЧКА РИСКА: cover_image_url не задаётся (нет генерации обложки).
        # Pinterest может потребовать обложку — тогда _create_pin кинет
        # PlatformError, и вызывающий код в publish() уйдёт в image-fallback.
        media_source = {"source_type": "video_id", "media_id": str(media_id)}
        return await self._create_pin(
            client, token, media_source, title=title, description=description
        )

    async def _publish_image_pin(
        self,
        client: httpx.AsyncClient,
        token: str,
        *,
        title: str,
        description: str,
        image_url: str,
    ) -> str:
        """Fallback: обычный image-pin через source_type=image_url.

        Требует публично доступного URL картинки. Pinterest скачает его сам.
        """
        media_source = {"source_type": "image_url", "url": image_url}
        return await self._create_pin(
            client, token, media_source, title=title, description=description
        )

    # ── publish() ──────────────────────────────────────────────────────────────
    async def publish(
        self,
        *,
        video_path: str | None,
        title: str,
        description: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.is_configured():
            raise NotConfiguredError("pinterest_not_configured")

        caption = compose_caption(title, description, tags)
        # description у Pinterest — отдельное поле; туда кладём caption,
        # а в title — короткий заголовок (обрезается до 100 в _create_pin).
        pin_title = title or (description[:50] if description else "Video")

        async with self._client() as client:
            token = await self._access_token(client)

            # Основной путь — video pin, если есть локальный файл.
            if video_path and os.path.exists(video_path):
                try:
                    pin_id = await self._publish_video_pin(
                        client,
                        token,
                        video_path=video_path,
                        title=pin_title,
                        description=caption,
                    )
                    return {"external_id": pin_id}
                except PlatformError as exc:
                    # Video-flow не прошёл → пробуем image-fallback, если есть URL.
                    image_url = _fallback_image_url(self.settings)
                    if not image_url:
                        # Нет ни рабочего видео, ни картинки — не молчим.
                        raise PlatformError(
                            f"pinterest_video_failed_no_image_fallback: {exc}"
                        ) from exc
                    pin_id = await self._publish_image_pin(
                        client,
                        token,
                        title=pin_title,
                        description=caption,
                        image_url=image_url,
                    )
                    return {"external_id": pin_id}

            # Видео нет вовсе → image-pin (нужен URL картинки).
            image_url = _fallback_image_url(self.settings)
            if not image_url:
                raise PlatformError("pinterest_no_video_and_no_image_url")
            pin_id = await self._publish_image_pin(
                client,
                token,
                title=pin_title,
                description=caption,
                image_url=image_url,
            )
            return {"external_id": pin_id}


# ── helpers ────────────────────────────────────────────────────────────────────
def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _json_or_error(resp: httpx.Response, where: str) -> dict[str, Any]:
    """Парсим JSON; на >=400 или невалидном теле → PlatformError.

    Pinterest на ошибках отдаёт {"code": N, "message": "..."} (API v5).
    """
    if resp.status_code >= 400:
        try:
            body = resp.json()
            msg = body.get("message") or body
            code = body.get("code")
            raise PlatformError(f"pinterest_{where}_error[{resp.status_code}/{code}]: {msg}")
        except PlatformError:
            raise
        except Exception:  # тело не JSON
            raise PlatformError(
                f"pinterest_{where}_error[{resp.status_code}]: {resp.text[:300]}"
            ) from None
    try:
        return resp.json()
    except Exception as exc:
        raise PlatformError(f"pinterest_{where}_bad_json: {resp.text[:300]}") from exc


def _fallback_image_url(settings: Any) -> str:
    """URL картинки для image-pin fallback.

    Источника обложки в текущих настройках НЕТ (config.py трогать нельзя),
    поэтому смотрим на необязательный атрибут pinterest_fallback_image_url,
    если он когда-нибудь появится. Сейчас обычно пусто → fallback недоступен,
    и video-flow обязан отработать сам.
    """
    return (getattr(settings, "pinterest_fallback_image_url", "") or "").strip()
