from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SlideRole = Literal["hook", "point", "cta"]


class SlideModel(BaseModel):
    """Унифицированный слайд. Render интерпретирует поля по role:
      hook  → heading = основной хук, body = подзаголовок
      point → heading = заголовок-принцип, body = тело
      cta   → heading = призыв, body = оффер
    """
    idx: int = Field(..., ge=1, le=7)
    role: SlideRole
    heading: str = ""
    body: str = ""


class GenerateReq(BaseModel):
    title: str = Field(..., min_length=1, max_length=600)
    text: str = Field(..., min_length=1, max_length=20000)
    template_id: str | None = None
    provider: str | None = None  # None → resolver выберет по приоритету


class SlideEditReq(BaseModel):
    heading: str | None = None
    body: str | None = None


class RefineReq(BaseModel):
    action: Literal["strengthen", "shorten", "expand"] = "strengthen"
    instruction: str | None = None  # произвольное уточнение для LLM
    provider: str | None = None


class TemplateOut(BaseModel):
    id: str
    name: str
    created_at: str
    has_background: bool


class CarouselOut(BaseModel):
    id: str
    template_id: str
    title: str
    text: str
    status: str
    rendered: bool
    slides: list[SlideModel]
    created_at: str
