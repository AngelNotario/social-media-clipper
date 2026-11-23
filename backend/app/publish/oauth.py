"""Conexión de cuentas con el OAuth oficial de cada red (apps de desarrollador propias del usuario).

- YouTube: cliente OAuth de Google tipo "App de escritorio" → redirección por loopback (cualquier puerto).
- TikTok: Login Kit para Desktop → localhost con PKCE (reto en SHA-256 hexadecimal, como pide TikTok).
- Instagram: Facebook Login for Business → cuenta profesional de Instagram vinculada a una página de Facebook.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx

from .. import db
from . import store


class OAuthError(Exception):
    pass


def http() -> httpx.Client:
    return httpx.Client(timeout=60, follow_redirects=True)


GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
YT_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TT_SCOPES = "user.info.basic,video.upload,video.publish"
IG_SCOPES = "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement,business_management"


def graph_version() -> str:
    return db.get_setting("publish_meta_graph_version") or "v25.0"


def graph(path: str) -> str:
    return f"https://graph.facebook.com/{graph_version()}/{path.lstrip('/')}"


def redirect_uri(base_url: str, platform: str) -> str:
    """URL de regreso que se registra en la app de cada red. TikTok exige la barra final."""
    base = base_url.rstrip("/")
    if platform == "youtube":  # clientes de escritorio de Google: loopback por IP (recomendado por Google)
        base = base.replace("://localhost", "://127.0.0.1")
    if platform == "instagram":  # Meta solo acepta localhost sin HTTPS; 127.0.0.1 lo rechaza
        base = base.replace("://127.0.0.1", "://localhost")
    return f"{base}/api/publish/oauth/{platform}/callback" + ("/" if platform == "tiktok" else "")


# estado de cada inicio de sesión en curso (solo en memoria; caduca a los 15 min)
_pending: dict[str, dict] = {}


def _remember(platform: str, redirect: str, **extra) -> str:
    now = time.time()
    for k in [k for k, v in _pending.items() if now - v["t"] > 900]:
        _pending.pop(k, None)
    state = secrets.token_urlsafe(24)
    _pending[state] = {"platform": platform, "redirect": redirect, "t": now, **extra}
    return state


def pkce_pair(hex_challenge: bool) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = digest.hex() if hex_challenge else base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def start(platform: str, base_url: str) -> str:
    if platform not in store.PLATFORMS:
        raise OAuthError("Red no soportada.")
    if not store.configured(platform):
        raise OAuthError(f"Primero pega las credenciales de tu app de {store.PLATFORM_NAMES[platform]}.")
    c = store.get_creds(platform)
    redirect = redirect_uri(base_url, platform)
    if platform == "youtube":
        verifier, challenge = pkce_pair(False)
        state = _remember(platform, redirect, verifier=verifier)
        q = {"client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": YT_SCOPES,
             "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true", "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256"}
        return f"{GOOGLE_AUTH}?{urlencode(q)}"
    if platform == "tiktok":
        verifier, challenge = pkce_pair(True)
        state = _remember(platform, redirect, verifier=verifier)
        q = {"client_key": c["client_key"], "response_type": "code", "scope": TT_SCOPES, "redirect_uri": redirect,
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        return f"{TT_AUTH}?{urlencode(q)}"
    state = _remember(platform, redirect)
    q = {"client_id": c["app_id"], "redirect_uri": redirect, "state": state, "response_type": "code"}
    if c.get("config_id"):  # Facebook Login for Business con configuración
        q["config_id"] = c["config_id"]
    else:
        q["scope"] = IG_SCOPES
    return f"https://www.facebook.com/{graph_version()}/dialog/oauth?{urlencode(q)}"


def _check(r: httpx.Response, what: str) -> dict:
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400 or (isinstance(d, dict) and d.get("error") and not d.get("access_token")):
        err = d.get("error") if isinstance(d, dict) else None
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code")
        else:
            msg = d.get("error_description") or d.get("message") or err or r.text[:300]
        raise OAuthError(f"{what}: {msg}")
    return d


def finish(state: str, code: str) -> dict:
    """Canjea el código por tokens y guarda la(s) cuenta(s). Devuelve {platform, accounts:[nombres]}."""
    p = _pending.pop(state, None)
    if not p:
        raise OAuthError("El inicio de sesión caducó o no coincide. Vuelve a pulsar «Conectar».")
    platform, c = p["platform"], store.get_creds(p["platform"])
    with http() as cl:
        if platform == "youtube":
            t = _check(cl.post(GOOGLE_TOKEN, data={
                "code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
                "redirect_uri": p["redirect"], "grant_type": "authorization_code", "code_verifier": p["verifier"]}),
                "Google rechazó el código")
            tokens = _google_tokens(t)
            ch = _check(cl.get("https://www.googleapis.com/youtube/v3/channels",
                               params={"part": "snippet", "mine": "true"},
                               headers={"Authorization": f"Bearer {tokens['access_token']}"}), "No se pudo leer el canal")
            items = ch.get("items") or []
            if not items:
                raise OAuthError("Esa cuenta de Google no tiene canal de YouTube.")
            sn = items[0]["snippet"]
            store.save_account("youtube", items[0]["id"], sn.get("title") or "Canal",
                               tokens, (sn.get("thumbnails") or {}).get("default", {}).get("url"))
            return {"platform": platform, "accounts": [sn.get("title")]}
        if platform == "tiktok":
            t = _check(cl.post(TT_TOKEN, data={
                "client_key": c["client_key"], "client_secret": c["client_secret"], "code": code,
                "grant_type": "authorization_code", "redirect_uri": p["redirect"], "code_verifier": p["verifier"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"}), "TikTok rechazó el código")
            tokens = _tiktok_tokens(t)
            u = _check(cl.get("https://open.tiktokapis.com/v2/user/info/",
                              params={"fields": "open_id,display_name,avatar_url"},
                              headers={"Authorization": f"Bearer {tokens['access_token']}"}), "No se pudo leer el perfil")
            user = (u.get("data") or {}).get("user") or {}
            store.save_account("tiktok", user.get("open_id") or t.get("open_id"), user.get("display_name") or "TikTok",
                               tokens, user.get("avatar_url"), {"scope": t.get("scope")})
            return {"platform": platform, "accounts": [user.get("display_name")]}
        # Instagram (vía Facebook Login for Business)
        t = _check(cl.get(graph("oauth/access_token"), params={
            "client_id": c["app_id"], "client_secret": c["app_secret"], "redirect_uri": p["redirect"], "code": code}),
            "Meta rechazó el código")
        ll = _check(cl.get(graph("oauth/access_token"), params={
            "grant_type": "fb_exchange_token", "client_id": c["app_id"], "client_secret": c["app_secret"],
            "fb_exchange_token": t["access_token"]}), "No se pudo obtener el token de larga duración")
        token = ll["access_token"]
        exp = time.time() + float(ll.get("expires_in") or 60 * 86400)
        pages = _check(cl.get(graph("me/accounts"), params={
            "fields": "name,instagram_business_account{id,username,profile_picture_url}", "access_token": token}),
            "No se pudieron leer tus páginas de Facebook")
        names = []
        for pg in pages.get("data") or []:
            ig = pg.get("instagram_business_account")
            if not ig:
                continue
            store.save_account("instagram", ig["id"], "@" + (ig.get("username") or ig["id"]),
                               {"access_token": token, "expires_at": exp}, ig.get("profile_picture_url"),
                               {"page": pg.get("name"), "page_id": pg.get("id")})
            names.append("@" + (ig.get("username") or ig["id"]))
        if not names:
            raise OAuthError("No encontré ninguna cuenta profesional de Instagram vinculada a tus páginas de Facebook. "
                             "Cambia tu Instagram a cuenta profesional y vincúlala a una página.")
        return {"platform": platform, "accounts": names}


def _google_tokens(t: dict, old: dict | None = None) -> dict:
    out = dict(old or {})
    out["access_token"] = t["access_token"]
    out["expires_at"] = time.time() + float(t.get("expires_in") or 3600)
    if t.get("refresh_token"):
        out["refresh_token"] = t["refresh_token"]
    return out


def _tiktok_tokens(t: dict) -> dict:
    now = time.time()
    return {"access_token": t["access_token"], "refresh_token": t.get("refresh_token"),
            "expires_at": now + float(t.get("expires_in") or 86400),
            "refresh_expires_at": now + float(t.get("refresh_expires_in") or 365 * 86400), "open_id": t.get("open_id")}


def access_token(account: dict) -> str:
    """Token vigente; renueva si faltan menos de 5 min (YouTube 1 h, TikTok 24 h; Instagram dura 60 días)."""
    tok = account["tokens"]
    margin = 7 * 86400 if account["platform"] == "instagram" else 300  # el de Instagram se extiende antes de caducar
    if time.time() < float(tok.get("expires_at") or 0) - margin:
        return tok["access_token"]
    c = store.get_creds(account["platform"])
    with http() as cl:
        if account["platform"] == "youtube":
            if not tok.get("refresh_token"):
                raise OAuthError("La sesión de YouTube caducó: vuelve a conectar la cuenta.")
            t = _check(cl.post(GOOGLE_TOKEN, data={"client_id": c["client_id"], "client_secret": c["client_secret"],
                                                   "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"}),
                       "La sesión de YouTube caducó (vuelve a conectar la cuenta)")
            new = _google_tokens(t, tok)
        elif account["platform"] == "tiktok":
            t = _check(cl.post(TT_TOKEN, data={"client_key": c["client_key"], "client_secret": c["client_secret"],
                                               "grant_type": "refresh_token", "refresh_token": tok.get("refresh_token")},
                               headers={"Content-Type": "application/x-www-form-urlencoded"}),
                       "La sesión de TikTok caducó (vuelve a conectar la cuenta)")
            new = _tiktok_tokens(t)
        else:
            if time.time() > float(tok.get("expires_at") or 0):
                raise OAuthError("La sesión de Instagram caducó (dura 60 días): vuelve a conectar la cuenta.")
            t = _check(cl.get(graph("oauth/access_token"), params={
                "grant_type": "fb_exchange_token", "client_id": c["app_id"], "client_secret": c["app_secret"],
                "fb_exchange_token": tok["access_token"]}), "No se pudo renovar la sesión de Instagram")
            new = {"access_token": t["access_token"], "expires_at": time.time() + float(t.get("expires_in") or 60 * 86400)}
    store.update_tokens(account["id"], new)
    account["tokens"] = new
    return new["access_token"]
