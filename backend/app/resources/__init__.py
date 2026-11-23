"""Recursos reales de internet para la edición (imágenes, logos, capturas, clips), verificados e indexados.

API:
- plan_needs(tl, understood, research_facts, per_min, max_items=40, progress=None, mode="cards"|"background")
- find(need, allow_web=True, verify=True, exclude=None) -> resource | None
- gather(tl, understood, research_facts, per_min, progress=None, mode="cards", allow_web=True) -> [{need, resource}]
- index_search(text, what=None, limit=20), index_list(limit, offset), index_delete(id)
"""
from .finder import find, gather, logo_bg
from .planner import plan_needs
from .store import ensure_schema, index_delete, index_list, index_search, mentions, norm

__all__ = ["plan_needs", "find", "gather", "index_search", "index_list", "index_delete", "ensure_schema",
           "mentions", "norm", "logo_bg"]
