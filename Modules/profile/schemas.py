from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ #
# Taxonomy
# ------------------------------------------------------------------ #

class NicheEntry(BaseModel):
    slug: str
    label_ru: str
    label_en: str | None = None
    parent_slug: str | None = None


# ------------------------------------------------------------------ #
# Account
# ------------------------------------------------------------------ #

class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    niche_slug: str | None = None


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    niche_slug: str | None = None


class AccountResponse(BaseModel):
    id: str
    name: str
    niche_slug: str | None
    created_at: str
    updated_at: str


# ------------------------------------------------------------------ #
# Brand Book
# ------------------------------------------------------------------ #

class ToneAxes(BaseModel):
    formality: int | None = Field(default=None, ge=1, le=10)
    energy: int | None = Field(default=None, ge=1, le=10)
    humor: int | None = Field(default=None, ge=1, le=10)
    expertise: int | None = Field(default=None, ge=1, le=10)


class BrandBookUpdate(BaseModel):
    tone: ToneAxes | None = None
    forbidden_words: list[str] | None = None
    cta: list[str] | None = None
    extra: dict[str, Any] | None = None


class BrandBookResponse(BaseModel):
    account_id: str
    tone_of_voice: ToneAxes
    forbidden_words: list[str]
    cta: list[str]
    extra: dict[str, Any]
    updated_at: str


# ------------------------------------------------------------------ #
# Audience Profile
# ------------------------------------------------------------------ #

ExpertiseLevel = Literal["beginner", "intermediate", "expert"]


class AudienceUpdate(BaseModel):
    age_range: str | None = Field(default=None, examples=["25-35"])
    geography: str | None = None
    gender: str | None = None
    expertise_level: ExpertiseLevel | None = None
    pain_points: list[str] | None = None
    desires: list[str] | None = None
    extra: dict[str, Any] | None = None


class AudienceResponse(BaseModel):
    account_id: str
    age_range: str | None
    geography: str | None
    gender: str | None
    expertise_level: str | None
    pain_points: list[str]
    desires: list[str]
    extra: dict[str, Any]
    updated_at: str


# ------------------------------------------------------------------ #
# Prompt Profile
# ------------------------------------------------------------------ #

class PromptProfileCreate(BaseModel):
    version: str = Field(min_length=1, max_length=50, examples=["1.0"])
    system_prompt: str = Field(min_length=1)
    modifiers: dict[str, Any] = {}
    hard_constraints: dict[str, Any] = {}
    soft_constraints: dict[str, Any] = {}


class PromptProfileResponse(BaseModel):
    id: int
    account_id: str
    version: str
    system_prompt: str
    modifiers: dict[str, Any]
    hard_constraints: dict[str, Any]
    soft_constraints: dict[str, Any]
    is_active: bool
    created_at: str


# ------------------------------------------------------------------ #
# Full Profile (merged — передаётся как GenContext.profile в A5)
# ------------------------------------------------------------------ #

class FullProfileResponse(BaseModel):
    account_id: str
    name: str
    niche: str | None = None
    brand_book: dict[str, Any] | None = None
    audience: dict[str, Any] | None = None
    system_prompt: str | None = None
    modifiers: dict[str, Any] = {}
    hard_constraints: dict[str, Any] = {}
    soft_constraints: dict[str, Any] = {}
    prompt_version: str | None = None
