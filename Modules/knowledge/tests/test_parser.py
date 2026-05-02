"""Тесты parser/chunker (PLAN_SELF_LEARNING_AGENT этап 4)."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from parser import chunk_text, extract_text  # noqa: E402


def test_extract_text_md():
    content = "# Заголовок\n\nЭто текст. Ещё текст.".encode("utf-8")
    out = extract_text(content, "text/markdown", "doc.md")
    assert "Заголовок" in out
    assert "Это текст" in out


def test_extract_text_txt_extension():
    out = extract_text(b"plain text", "text/plain", "doc.txt")
    assert out == "plain text"


def test_extract_text_unsupported():
    with pytest.raises(ValueError):
        extract_text(b"binary", "image/jpeg", "img.jpg")


def test_chunk_text_short():
    """Один короткий абзац — один chunk."""
    out = list(chunk_text("Короткий текст.", max_tokens=500))
    assert len(out) == 1
    assert out[0][0] == "Короткий текст."
    assert out[0][1] >= 1


def test_chunk_text_paragraph_split():
    """Многопараграфный текст должен разделяться по \\n\\n."""
    text = "Абзац один.\n\nАбзац два.\n\nАбзац три."
    out = list(chunk_text(text, max_tokens=500))
    assert len(out) == 1  # все 3 в один chunk т.к. small
    assert "Абзац один" in out[0][0]


def test_chunk_text_long_split():
    """Большой текст должен раскрашиваться по нескольким chunks."""
    paragraph = "Слово " * 200  # ~1000 chars ~ 250 tokens
    text = (paragraph + "\n\n") * 5  # 5 абзацев × 250 tok = 1250 tok
    out = list(chunk_text(text, max_tokens=300, overlap_tokens=20))
    assert len(out) >= 3  # должен разбиться


def test_chunk_text_empty():
    assert list(chunk_text("")) == []
    assert list(chunk_text("   \n\n  ")) == []


def test_chunk_text_long_single_paragraph():
    """Один длинный параграф (без \\n\\n) должен резаться по предложениям."""
    sentences = "Это предложение. " * 200  # ~3400 chars ~ 850 tok
    out = list(chunk_text(sentences, max_tokens=300))
    assert len(out) >= 2
