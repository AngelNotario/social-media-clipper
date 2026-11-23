"""API de lo que la IA sabe de edición (guía) y lo que aprende de ti (opiniones)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from .. import knowledge

router = APIRouter(tags=["knowledge"])


class GuideIn(BaseModel):
    text: str = Field("", max_length=12000)


@router.get("/knowledge/guide")
def get_guide():
    return {"text": knowledge.guide(), "default": knowledge.DEFAULT_GUIDE,
            "custom": knowledge.GUIDE_PATH.exists()}


@router.put("/knowledge/guide")
def put_guide(body: GuideIn):
    return {"text": knowledge.save_guide(body.text), "custom": knowledge.GUIDE_PATH.exists()}


class FeedbackIn(BaseModel):
    kind: Literal["youtube", "clip", "studio"]
    ref_id: int | None = None
    rating: int = Field(0, ge=-1, le=1)
    note: str | None = Field(None, max_length=600)
    context: dict | None = None


@router.post("/feedback")
def add_feedback(body: FeedbackIn):
    return knowledge.add_feedback(body.kind, body.ref_id, body.rating, body.note, body.context)


@router.get("/feedback")
def list_feedback(kind: str | None = None, limit: int = 30):
    return knowledge.list_feedback(min(limit, 100), kind)


@router.delete("/feedback/{fid}")
def delete_feedback(fid: int):
    knowledge.delete_feedback(fid)
    return {"ok": True}


@router.get("/knowledge/taste")
def get_taste():
    from .. import taste
    return taste.public()


@router.post("/knowledge/taste/reset")
def reset_taste():
    from .. import taste
    n = taste.reset()
    return {"removed": n, **taste.public()}
