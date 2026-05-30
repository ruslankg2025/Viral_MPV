"""HTTP API сервиса carousel (prefix /carousel)."""
from __future__ import annotations

import io
import zipfile
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from PIL import Image

import render
import textfit
from auth import require_worker_token
from logging_setup import get_logger
from schemas import (
    CarouselOut,
    CarouselPatchReq,
    GenerateReq,
    RefineReq,
    SlideEditReq,
    TemplateOut,
)
from state import state

log = get_logger()

router = APIRouter(
    prefix="/carousel",
    tags=["carousel"],
    dependencies=[Depends(require_worker_token)],
)


# ── helpers ──────────────────────────────────────────────────────────────
def _settings():
    return state.settings


def _template_or_404(template_id: str | None) -> dict[str, Any]:
    store = state.template_store
    tpl = store.get(template_id) if template_id else store.get_default()
    if tpl is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="no_template: загрузите подложку через POST /carousel/templates",
        )
    return tpl


def _bg_path(tpl: dict[str, Any]) -> str | None:
    rel = tpl.get("background_path")
    if not rel:
        return None
    return str(_settings().media_dir / rel)


def _carousel_or_404(cid: str) -> dict[str, Any]:
    car = state.carousel_store.get(cid)
    if car is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="carousel_not_found")
    return car


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _to_out(car: dict[str, Any]) -> CarouselOut:
    return CarouselOut(**car)


# ── templates ──────────────────────────────────────────────────────────────
@router.post("/templates", response_model=TemplateOut, status_code=201)
async def upload_template(
    file: UploadFile = File(...),
    name: str = Form("Подложка"),
    is_default: bool = Form(True),
):
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"bad_image:{e}") from e

    tpl = state.template_store.create(name=name, background_path=None, is_default=is_default)
    rel = f"carousel/templates/{tpl['id']}.png"
    dst = _settings().media_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "PNG")
    state.template_store.set_background(tpl["id"], rel)
    log.info("template_uploaded", template_id=tpl["id"], size=img.size)
    tpl = state.template_store.get(tpl["id"])
    return TemplateOut(
        id=tpl["id"], name=tpl["name"], created_at=tpl["created_at"],
        has_background=bool(tpl.get("background_path")),
    )


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates():
    return [
        TemplateOut(
            id=t["id"], name=t["name"], created_at=t["created_at"],
            has_background=bool(t.get("background_path")),
        )
        for t in state.template_store.list_all()
    ]


@router.get("/templates/{template_id}/master.png")
async def template_master(template_id: str):
    tpl = _template_or_404(template_id)
    img = render.build_master(_bg_path(tpl), tpl.get("layout"))
    return Response(content=_png(img), media_type="image/png")


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(template_id: str):
    if not state.template_store.delete(template_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="template_not_found")
    return None


# ── carousels ────────────────────────────────────────────────────────────────
@router.post("/generate", response_model=CarouselOut, status_code=201)
async def generate(req: GenerateReq):
    tpl = _template_or_404(req.template_id)
    slides = await textfit.adapt(req.title, req.text, provider=req.provider)
    car = state.carousel_store.create(
        account_id=req.account_id, template_id=tpl["id"],
        title=req.title, text=req.text, slides=slides,
    )
    log.info("carousel_generated", carousel_id=car["id"], template_id=tpl["id"], account_id=req.account_id)
    return _to_out(car)


@router.get("", response_model=list[CarouselOut])
async def list_carousels(account_id: str | None = None, status: str | None = None):
    return [_to_out(c) for c in state.carousel_store.query(account_id=account_id, status=status)]


@router.patch("/{carousel_id}", response_model=CarouselOut)
async def patch_carousel(carousel_id: str, req: CarouselPatchReq):
    _carousel_or_404(carousel_id)
    car = state.carousel_store.set_meta(carousel_id, status=req.status, title=req.title)
    return _to_out(car)


@router.get("/{carousel_id}", response_model=CarouselOut)
async def get_carousel(carousel_id: str):
    return _to_out(_carousel_or_404(carousel_id))


@router.patch("/{carousel_id}/slides/{idx}", response_model=CarouselOut)
async def edit_slide(carousel_id: str, idx: int, req: SlideEditReq):
    car = _carousel_or_404(carousel_id)
    slides = car["slides"]
    target = next((s for s in slides if s.get("idx") == idx), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="slide_not_found")
    if req.heading is not None:
        target["heading"] = req.heading
    if req.body is not None:
        target["body"] = req.body
    return _to_out(state.carousel_store.update_slides(carousel_id, slides))


@router.post("/{carousel_id}/slides/{idx}/refine", response_model=CarouselOut)
async def refine_slide(carousel_id: str, idx: int, req: RefineReq):
    car = _carousel_or_404(carousel_id)
    slides = car["slides"]
    target = next((s for s in slides if s.get("idx") == idx), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="slide_not_found")
    heading, body = await textfit.refine(
        target.get("heading", ""), target.get("body", ""),
        action=req.action, instruction=req.instruction, provider=req.provider,
    )
    target["heading"], target["body"] = heading, body
    return _to_out(state.carousel_store.update_slides(carousel_id, slides))


@router.get("/{carousel_id}/slides/{idx}.png")
async def slide_png(carousel_id: str, idx: int):
    car = _carousel_or_404(carousel_id)
    tpl = _template_or_404(car["template_id"])
    target = next((s for s in car["slides"] if s.get("idx") == idx), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="slide_not_found")
    img = render.render_carousel(_bg_path(tpl), [target], tpl.get("layout"))[0]
    return Response(content=_png(img), media_type="image/png")


@router.get("/{carousel_id}/download")
async def download_zip(carousel_id: str):
    car = _carousel_or_404(carousel_id)
    tpl = _template_or_404(car["template_id"])
    imgs = render.render_carousel(_bg_path(tpl), car["slides"], tpl.get("layout"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, img in enumerate(imgs, start=1):
            z.writestr(f"slide_{i}.png", _png(img))
    state.carousel_store.mark_rendered(carousel_id)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="carousel_{carousel_id[:8]}.zip"'},
    )


@router.delete("/{carousel_id}", status_code=204)
async def delete_carousel(carousel_id: str):
    if not state.carousel_store.delete(carousel_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="carousel_not_found")
    return None
