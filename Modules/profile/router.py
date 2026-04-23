from fastapi import APIRouter, Depends, HTTPException, Response

from auth import require_admin_token, require_token
from schemas import (
    TONE_PRESET_AXES,
    AccountCreate,
    AccountPatch,
    AccountResponse,
    AudienceResponse,
    AudienceUpdate,
    BrandBookResponse,
    BrandBookUpdate,
    FullProfileResponse,
    NicheEntry,
    PromptProfileCreate,
    PromptProfileResponse,
    ToneAxes,
)
from seed import load_example_account
from state import state

router = APIRouter(prefix="/profile", dependencies=[Depends(require_token)])

# Отдельный роутер для admin-only операций (без require_token)
admin_router = APIRouter(prefix="/profile", dependencies=[Depends(require_admin_token)])


# ------------------------------------------------------------------ #
# Taxonomy
# ------------------------------------------------------------------ #

@router.get("/taxonomy", response_model=list[NicheEntry])
async def get_taxonomy(parent_slug: str | None = None):
    rows = state.profile_store.list_taxonomy(parent_slug)
    return [NicheEntry(**r) for r in rows]


# ------------------------------------------------------------------ #
# Accounts
# ------------------------------------------------------------------ #

@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts():
    return [_account_response(a) for a in state.profile_store.list_accounts()]


@router.post("/accounts", response_model=AccountResponse, status_code=201)
async def create_account(body: AccountCreate):
    account = state.profile_store.create_account(
        body.name,
        niche_slug=body.niche_slug,
        niche_slugs=body.niche_slugs,
        language=body.language,
    )
    return _account_response(account)


@router.get("/accounts/{account_id}", response_model=FullProfileResponse)
async def get_full_profile(account_id: str):
    profile = state.profile_store.get_full_profile(account_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    return FullProfileResponse(**profile)


@router.patch("/accounts/{account_id}", response_model=AccountResponse)
async def patch_account(account_id: str, body: AccountPatch):
    _require_account(account_id)
    state.profile_store.update_account(
        account_id,
        name=body.name,
        niche_slug=body.niche_slug,
        niche_slugs=body.niche_slugs,
        language=body.language,
    )
    account = state.profile_store.get_account(account_id)
    return _account_response(account)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: str):
    """Удалить аккаунт + каскад: brand_book, audience, prompt_profiles."""
    ok = state.profile_store.delete_account(account_id)
    if not ok:
        raise HTTPException(status_code=404, detail="account_not_found")
    return Response(status_code=204)


# ------------------------------------------------------------------ #
# Brand Book
# ------------------------------------------------------------------ #

@router.get("/accounts/{account_id}/brand-book", response_model=BrandBookResponse | None)
async def get_brand_book(account_id: str):
    _require_account(account_id)
    bb = state.profile_store.get_brand_book(account_id)
    if bb is None:
        return None
    return _brand_book_response(bb)


@router.put("/accounts/{account_id}/brand-book", response_model=BrandBookResponse)
async def upsert_brand_book(account_id: str, body: BrandBookUpdate):
    _require_account(account_id)
    tone = body.tone or ToneAxes()

    # Если передан preset — заполняем оси дефолтами preset'а,
    # но сохраняем явно указанные пользователем значения осей.
    formality = tone.formality
    energy = tone.energy
    humor = tone.humor
    expertise = tone.expertise
    if body.tone_preset is not None:
        defaults = TONE_PRESET_AXES.get(body.tone_preset, {})
        formality = formality if formality is not None else defaults.get("formality")
        energy = energy if energy is not None else defaults.get("energy")
        humor = humor if humor is not None else defaults.get("humor")
        expertise = expertise if expertise is not None else defaults.get("expertise")

    bb = state.profile_store.upsert_brand_book(
        account_id,
        tone_preset=body.tone_preset,
        formality=formality,
        energy=energy,
        humor=humor,
        expertise=expertise,
        forbidden_words=body.forbidden_words,
        cta=body.cta,
        extra=body.extra,
    )
    return _brand_book_response(bb)


# ------------------------------------------------------------------ #
# Audience Profile
# ------------------------------------------------------------------ #

@router.get("/accounts/{account_id}/audience", response_model=AudienceResponse | None)
async def get_audience(account_id: str):
    _require_account(account_id)
    aud = state.profile_store.get_audience(account_id)
    if aud is None:
        return None
    return _audience_response(aud)


@router.put("/accounts/{account_id}/audience", response_model=AudienceResponse)
async def upsert_audience(account_id: str, body: AudienceUpdate):
    _require_account(account_id)
    aud = state.profile_store.upsert_audience(
        account_id,
        age_range=body.age_range,
        geography=body.geography,
        gender=body.gender,
        expertise_level=body.expertise_level,
        pain_points=body.pain_points,
        desires=body.desires,
        extra=body.extra,
    )
    return _audience_response(aud)


# ------------------------------------------------------------------ #
# Prompt Profile
# ------------------------------------------------------------------ #

@router.get("/accounts/{account_id}/prompt-profile", response_model=PromptProfileResponse | None)
async def get_prompt_profile(account_id: str):
    _require_account(account_id)
    pp = state.profile_store.get_active_prompt_profile(account_id)
    if pp is None:
        return None
    return PromptProfileResponse(**vars(pp))


@router.get("/accounts/{account_id}/prompt-profile/versions", response_model=list[PromptProfileResponse])
async def list_prompt_versions(account_id: str):
    _require_account(account_id)
    return [PromptProfileResponse(**vars(pp)) for pp in state.profile_store.list_prompt_versions(account_id)]


@router.post("/accounts/{account_id}/prompt-profile", response_model=PromptProfileResponse, status_code=201)
async def create_prompt_profile(account_id: str, body: PromptProfileCreate):
    _require_account(account_id)
    pp = state.profile_store.create_prompt_profile(
        account_id,
        version=body.version,
        system_prompt=body.system_prompt,
        modifiers=body.modifiers,
        hard_constraints=body.hard_constraints,
        soft_constraints=body.soft_constraints,
    )
    return PromptProfileResponse(**vars(pp))


@router.post(
    "/accounts/{account_id}/prompt-profile/rollback/{version}",
    response_model=PromptProfileResponse,
)
async def rollback_prompt_profile(account_id: str, version: str):
    _require_account(account_id)
    pp = state.profile_store.rollback_prompt_profile(account_id, version)
    if pp is None:
        raise HTTPException(status_code=404, detail="version_not_found")
    return PromptProfileResponse(**vars(pp))


# ------------------------------------------------------------------ #
# Admin: seed
# ------------------------------------------------------------------ #

@admin_router.post("/seed", status_code=200)
async def seed_example():
    result = load_example_account(state.profile_store)
    return {"seeded": result}


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #

def _require_account(account_id: str) -> None:
    if state.profile_store.get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="account_not_found")


def _account_response(a) -> AccountResponse:
    return AccountResponse(
        id=a.id,
        name=a.name,
        niche_slug=a.niche_slug,
        niche_slugs=a.niche_slugs,
        language=a.language,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


def _brand_book_response(bb) -> BrandBookResponse:
    return BrandBookResponse(
        account_id=bb.account_id,
        tone_preset=bb.tone_preset,
        tone_of_voice=ToneAxes(
            formality=bb.formality, energy=bb.energy,
            humor=bb.humor, expertise=bb.expertise,
        ),
        forbidden_words=bb.forbidden_words,
        cta=bb.cta,
        extra=bb.extra,
        updated_at=bb.updated_at,
    )


def _audience_response(aud) -> AudienceResponse:
    return AudienceResponse(
        account_id=aud.account_id,
        age_range=aud.age_range,
        geography=aud.geography,
        gender=aud.gender,
        expertise_level=aud.expertise_level,
        pain_points=aud.pain_points,
        desires=aud.desires,
        extra=aud.extra,
        updated_at=aud.updated_at,
    )
