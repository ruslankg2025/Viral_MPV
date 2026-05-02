"""Парсеры файлов в plain-text + чанкер для RAG.

Поддерживаем:
  - PDF (через pypdf)
  - Markdown / TXT — native UTF-8 read

Чанкер — простой и устойчивый: режем по абзацам, потом по N-токенам.
Без markdown-AST анализа — для текущих use cases (тренинги в Pdf/MD)
этого хватает.
"""
from __future__ import annotations

import io
import re
from typing import Iterator


def _approx_token_count(text: str) -> int:
    """Грубая оценка: 1 токен ≈ 4 символа для CJK/латиницы. Для русского
    немного завышаем (~3.5 символов на токен), но для chunking это ок."""
    return max(1, len(text) // 4)


def extract_text(content: bytes, content_type: str | None, filename: str) -> str:
    """Возвращает plain text из загруженного файла. Бросает ValueError
    если формат неизвестен или парсинг упал."""
    name = (filename or "").lower()
    ct = (content_type or "").lower()

    if ct == "application/pdf" or name.endswith(".pdf"):
        return _extract_pdf(content)
    if ct.startswith("text/") or name.endswith((".md", ".markdown", ".txt")):
        try:
            return content.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"text_decode_failed: {e}")
    raise ValueError(f"unsupported_content_type: {content_type or 'unknown'} ({name})")


def _extract_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError as e:
        raise ValueError(f"pypdf_not_installed: {e}")
    reader = PdfReader(io.BytesIO(content))
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text() or "")
        except Exception:  # noqa: BLE001
            pages.append("")
    return "\n\n".join(pages).strip()


def chunk_text(
    text: str,
    *,
    max_tokens: int = 500,
    overlap_tokens: int = 50,
) -> Iterator[tuple[str, int]]:
    """Разбивает текст на чанки ~max_tokens с overlap. Yields (text, token_count).

    Стратегия: режем по абзацам (двойной перевод строки), копим до лимита,
    потом выгружаем + сохраняем небольшой overlap для контекста соседних
    chunks.
    """
    text = (text or "").strip()
    if not text:
        return

    # Нормализуем переводы строк: 2+ → \n\n (абзацы), 1 → пробел
    text = re.sub(r"\n{2,}", "\n\n", text)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    if not paragraphs:
        return

    buf: list[str] = []
    buf_tokens = 0
    overlap_buf: list[str] = []

    def _flush():
        nonlocal buf, buf_tokens, overlap_buf
        if not buf:
            return
        chunk = "\n\n".join(buf).strip()
        if not chunk:
            buf = []
            buf_tokens = 0
            return
        yield chunk, buf_tokens
        # Готовим overlap: tail последних абзацев равный overlap_tokens
        tail: list[str] = []
        tail_tokens = 0
        for p in reversed(buf):
            t = _approx_token_count(p)
            if tail_tokens + t > overlap_tokens:
                break
            tail.insert(0, p)
            tail_tokens += t
        overlap_buf = tail
        buf = list(tail)
        buf_tokens = tail_tokens

    for p in paragraphs:
        p_tokens = _approx_token_count(p)
        # Если один абзац больше max_tokens — режем его по предложениям
        if p_tokens > max_tokens:
            # сначала flush того что накопилось
            yield from _flush()
            for sub_chunk in _split_long_paragraph(p, max_tokens):
                yield sub_chunk, _approx_token_count(sub_chunk)
            buf = []
            buf_tokens = 0
            continue

        if buf_tokens + p_tokens > max_tokens and buf:
            yield from _flush()
        buf.append(p)
        buf_tokens += p_tokens

    yield from _flush()


def _split_long_paragraph(text: str, max_tokens: int) -> list[str]:
    """Режет один длинный абзац по предложениям до ≤max_tokens на кусок."""
    # Простой sentence-splitter: точка/?/!/перевод строки + пробел/EOS
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    buf = []
    buf_tokens = 0
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        s_tok = _approx_token_count(s)
        if buf_tokens + s_tok > max_tokens and buf:
            chunks.append(" ".join(buf))
            buf, buf_tokens = [], 0
        buf.append(s)
        buf_tokens += s_tok
    if buf:
        chunks.append(" ".join(buf))
    # Если предложение само по себе > max_tokens (редко) — режем посимвольно
    out: list[str] = []
    char_limit = max_tokens * 4  # обратное приближение
    for c in chunks:
        if len(c) <= char_limit:
            out.append(c)
        else:
            for i in range(0, len(c), char_limit):
                out.append(c[i:i + char_limit])
    return out
