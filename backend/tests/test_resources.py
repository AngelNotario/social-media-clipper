"""Recursos de internet: plan anclado a lo que se dice, reutilización del índice, umbral del verificador,
búsqueda sin acentos y API del índice. Sin red: todo lo externo está simulado."""
from pathlib import Path

from PIL import Image

from app import research
from app.ai import llm
from app.resources import finder, planner, store
import app.resources as resources


def _tl():
    text = ("hoy les cuento lo que pasó con Eneba y los códigos de GTA seis que nunca llegaron "
            "y además Xbox no dijo nada sobre esto porque Eneba no es tienda oficial").split()
    words = [{"word": w, "start": i * 1.0, "end": i * 1.0 + 0.8} for i, w in enumerate(text)]
    return {"duration": len(text) * 1.0, "words": words, "tracks": {"video": [], "broll": [], "text": [], "music": [], "sfx": []}}


def _llm_ok(monkeypatch, items):
    monkeypatch.setattr(llm, "status", lambda url: {"available": True, "models": ["m", "qwen3.6:35b-a3b", None]})
    monkeypatch.setattr(planner.db, "role_model", lambda role: "m")
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"items": items})


def test_norm_and_mentions():
    assert store.norm("GTA VI") == "gta 6" == store.norm("gta seis") == store.norm("GTA6")
    assert store.mentions("GTA 6", "los códigos de GTA seis")
    assert store.mentions("Eneba", "lo que pasó con enebá")
    assert not store.mentions("PlayStation 5", "Xbox no dijo nada")


def test_plan_drops_entities_not_said(client, monkeypatch):
    _llm_ok(monkeypatch, [
        {"t0": 2.0, "t1": 4.5, "entity": "Eneba", "what": "logo", "query": "Eneba logo", "priority": 3},
        {"t0": 6.0, "t1": 10.0, "entity": "PlayStation 5", "what": "producto", "query": "PS5", "priority": 3},  # no se dice
        {"t0": 9.0, "t1": 13.0, "entity": "GTA 6", "what": "juego", "query": "GTA 6", "priority": 2,
         "label": "Códigos de GTA 6"},
        {"t0": 14.0, "t1": 18.0, "entity": "Xbox", "what": "logo", "query": "", "label": "Sony lo confirmó"},  # rótulo inventado
    ])
    needs = planner.plan_needs(_tl(), {"tema": "Eneba"}, None, per_min=20, mode="cards")
    ents = [n["entity"] for n in needs]
    assert "PlayStation 5" not in ents and "Eneba" in ents and "GTA 6" in ents
    eneba = next(n for n in needs if n["entity"] == "Eneba")
    assert abs(eneba["t0"] - (7 * 1.0 - 0.15)) < 0.01  # alineado a cuando se dice «Eneba»
    assert 2.5 <= eneba["t1"] - eneba["t0"] <= 7
    gta = next(n for n in needs if n["entity"] == "GTA 6")
    assert gta["label"] == "Códigos de GTA 6"
    xbox = [n for n in needs if n["entity"] == "Xbox"]
    assert not xbox or xbox[0]["label"] == ""  # el rótulo solo lleva lo que se dice
    assert all(a["t1"] <= b["t0"] for a, b in zip(needs, needs[1:]))  # sin solapes


def test_plan_without_llm_returns_nothing(client, monkeypatch):
    monkeypatch.setattr(llm, "status", lambda url: {"available": False, "models": []})
    assert planner.plan_needs(_tl(), None, None, per_min=6) == []


def _register_local(tmp_path: Path, entity="Eneba", what="logo", score=8.0, color=(120, 40, 220)):
    from app.studio import assets
    store.WEB_DIR.mkdir(parents=True, exist_ok=True)
    p = store.WEB_DIR / f"{entity.replace(' ', '_')}_{what}_{score}.png"
    Image.new("RGBA", (600, 300), color + (255,)).save(p)
    aid = assets._upsert(p, "broll", "image", entity, f"{entity.lower()} {what} web")
    rid = store.add(aid, entity, what, f"{entity} {what}", f"https://x.test/{p.name}", "https://x.test", entity,
                    600, 300, None, score, "", "#7828DC")
    return aid, rid


def test_find_reuses_local_index_and_variants(client, tmp_path, monkeypatch):
    a1, r1 = _register_local(tmp_path, "Énéba", "logo", 8.0)
    a2, r2 = _register_local(tmp_path, "Eneba", "logo", 7.0, (20, 200, 60))
    called = []
    monkeypatch.setattr(finder, "_web_image", lambda n: called.append(n) or None)
    need = {"entity": "eneba", "what": "logo", "query": "eneba logo", "said": "Eneba"}
    r = finder.find(need)
    assert r["reused"] and r["asset_id"] in (a1, a2) and r["fit"] == "contain" and r["kind"] == "media"
    other = finder.find(need, exclude={r["asset_id"]})
    assert other["asset_id"] != r["asset_id"]  # otra variante de la misma cosa
    again = finder.find(need, exclude={a1, a2})  # ya se usaron todas: sin internet repite una real
    assert again and again["asset_id"] in (a1, a2)
    assert store.get(r1)["used_count"] >= 1


def test_text_card_needs_no_search(client):
    r = finder.find({"entity": "Nunca llegaron", "what": "texto_clave", "label": "Eneba"})
    assert r["kind"] == "text" and r["text"] == "Nunca llegaron" and r["asset_id"] is None
    assert r["bg_color"].startswith("#") and r["color"] in ("#FFFFFF", "#111318")


def test_verification_threshold(client, tmp_path, monkeypatch):
    monkeypatch.setattr(research, "enabled", lambda: True)
    src = tmp_path / "web.png"
    cands = [{"image": f"https://img.test/{i}.png", "url": "https://xbox.com/a", "title": "Xbox logo", "width": 800,
              "height": 400} for i in range(4)]
    monkeypatch.setattr(research, "images", lambda q, n=8, **k: cands)
    colors = iter([(10, 120, 10), (200, 30, 30), (30, 30, 200), (90, 90, 90)])

    def fake_dl(url, dest, max_bytes=0):
        Image.new("RGB", (800, 400), next(colors)).save(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        return dest
    monkeypatch.setattr(research, "download", fake_dl)
    scores = iter([4.0, 7.5])
    monkeypatch.setattr(finder, "_vision", lambda: ("u", "v"))
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"score": next(scores), "shows_entity": True})
    r = finder.find({"entity": "Xbox", "what": "logo", "query": "Xbox logo", "said": "Xbox no dijo nada"})
    assert r and not r["reused"] and r["score"] == 7.5 and Path(r["path"]).exists()
    assert r["bg_color"] and r["bg_color"].startswith("#")
    # la segunda vez sale del índice (no se vuelve a descargar ni verificar)
    monkeypatch.setattr(research, "download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("descargó")))
    r2 = finder.find({"entity": "XBOX", "what": "logo", "query": "x", "said": "xbox"})
    assert r2["reused"] and r2["asset_id"] == r["asset_id"]


def test_rejects_when_verifier_says_no(client, tmp_path, monkeypatch):
    monkeypatch.setattr(research, "enabled", lambda: True)
    monkeypatch.setattr(research, "images", lambda q, n=8, **k: [
        {"image": f"https://img.test/n{i}.jpg", "url": "https://a.test", "title": "Twitch", "width": 900, "height": 600}
        for i in range(3)])

    def fake_dl(url, dest, max_bytes=0):
        dest.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (900, 600), (len(url) * 7 % 255, 50, 90)).save(dest, "PNG")
        return dest
    monkeypatch.setattr(research, "download", fake_dl)
    monkeypatch.setattr(finder, "_vision", lambda: ("u", "v"))
    n = {"c": 0}

    def judge(*a, **k):
        n["c"] += 1
        return {"score": 3, "shows_entity": False}
    monkeypatch.setattr(llm, "chat_json", judge)
    assert finder.find({"entity": "Twitch", "what": "producto", "query": "Twitch", "said": "twitch"}) is None
    assert n["c"] == finder.MAX_VERIFY  # verifica como máximo 2 candidatos


def test_logo_bg_contrast():
    green = Image.new("RGBA", (200, 100), (16, 124, 16, 255))
    assert finder.logo_bg(green, "Xbox") in ("#F4F4F5", "#111318")  # logo todo verde: fondo neutro
    mixed = Image.new("RGBA", (200, 100), (255, 255, 255, 255))
    for x in range(60):
        for y in range(100):
            mixed.putpixel((x, y), (230, 0, 18, 255))
    assert finder.logo_bg(mixed, "Nintendo") == "#E60012" or finder.logo_bg(mixed, "Nintendo").startswith("#E")


def test_gather_offline_contract(client, tmp_path, monkeypatch):
    _register_local(tmp_path, "GTA 6", "juego", 8.0, (200, 120, 40))
    _llm_ok(monkeypatch, [
        {"t0": 9.0, "t1": 13.0, "entity": "GTA 6", "what": "juego", "query": "GTA 6 gameplay", "priority": 3},
        {"t0": 13.5, "t1": 17.0, "entity": "nunca llegaron", "what": "texto_clave", "priority": 2},
        {"t0": 18.0, "t1": 22.0, "entity": "Xbox", "what": "logo", "query": "Xbox logo", "priority": 2},
    ])
    out = resources.gather(_tl(), None, None, per_min=12, mode="background")
    assert [x["need"]["t0"] for x in out] == sorted(x["need"]["t0"] for x in out)
    by = {x["need"]["entity"]: x["resource"] for x in out}
    assert by["GTA 6"]["reused"] and by["GTA 6"]["kind"] == "media"
    assert by["nunca llegaron"]["kind"] == "text"
    assert by.get("Xbox") is None or by["Xbox"]["reused"]  # sin internet: nada inventado


def test_index_search_and_router(client, tmp_path):
    aid, rid = _register_local(tmp_path, "Reddit", "logo", 9.0, (255, 69, 0))
    assert store.index_search("réddit")[0]["id"] == rid
    assert store.index_search("REDDIT", what="producto") == []
    items = client.get("/api/resources").json()["items"]
    row = next(i for i in items if i["id"] == rid)
    assert row["url"] == f"/api/resources/{rid}/file" and row["available"]
    assert client.get(row["url"]).status_code == 200
    assert client.get("/api/resources/search", params={"q": "reddit"}).json()["items"]
    path = Path(row["path"])
    assert client.delete(f"/api/resources/{rid}").json()["ok"]
    assert not path.exists() and store.get(rid) is None
    assert client.delete(f"/api/resources/{rid}").status_code == 404
