#!/usr/bin/env python3
"""CLI somente leitura para a API v3 do Strava (OAuth2 + refresh automático).

Somente biblioteca padrão. Saída em JSON no stdout; erros em JSON no stderr.
Credenciais: $STRAVA_CONFIG_DIR/credentials.json (padrão ~/.config/strava-skill).
"""
import argparse
import getpass
import json
import os
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

API_BASE = os.environ.get("STRAVA_API_BASE", "https://www.strava.com/api/v3").rstrip("/")
OAUTH_BASE = os.environ.get("STRAVA_OAUTH_BASE", "https://www.strava.com/oauth").rstrip("/")
CONFIG_DIR = os.environ.get("STRAVA_CONFIG_DIR") or os.path.expanduser("~/.config/strava-skill")
CRED_FILE = os.path.join(CONFIG_DIR, "credentials.json")
DEFAULT_SCOPE = "read,read_all,profile:read_all,activity:read_all"
REQUIRED_SCOPES = ("activity:read_all", "profile:read_all")
DEFAULT_STREAMS = "time,distance,heartrate,altitude,velocity_smooth,cadence,watts"


class StravaError(Exception):
    def __init__(self, message, status=None, detail=None):
        super().__init__(message)
        self.status = status
        self.detail = detail


# ---------- credenciais ----------

def load_creds():
    try:
        with open(CRED_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except json.JSONDecodeError as e:
        raise StravaError(f"Arquivo de credenciais inválido ({CRED_FILE}): {e}")
    if os.environ.get("STRAVA_CLIENT_ID"):
        data["client_id"] = os.environ["STRAVA_CLIENT_ID"]
    if os.environ.get("STRAVA_CLIENT_SECRET"):
        data["client_secret"] = os.environ["STRAVA_CLIENT_SECRET"]
    return data


def save_creds(data):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".cred-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CRED_FILE)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def persistable(creds):
    """Remove segredos vindos de env para não gravá-los em disco sem pedido."""
    out = dict(creds)
    for key, env in (("client_id", "STRAVA_CLIENT_ID"), ("client_secret", "STRAVA_CLIENT_SECRET")):
        if os.environ.get(env) and out.get(key) == os.environ[env]:
            stored = _read_stored().get(key)
            if stored:
                out[key] = stored
            else:
                out.pop(key, None)
    return out


def _read_stored():
    try:
        with open(CRED_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---------- HTTP ----------

def _http(url, data=None, headers=None, method=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except urllib.error.URLError as e:
        raise StravaError(f"Falha de rede: {e.reason}")


def _parse(raw):
    try:
        return json.loads(raw.decode() or "null")
    except (ValueError, UnicodeDecodeError):
        return raw.decode(errors="replace")


def token_request(creds, payload):
    for key in ("client_id", "client_secret"):
        if not creds.get(key):
            raise StravaError(f"{key} ausente. Rode: strava.py auth")
    payload = dict(payload, client_id=creds["client_id"], client_secret=creds["client_secret"])
    status, _, raw = _http(f"{OAUTH_BASE}/token", data=payload, method="POST")
    body = _parse(raw)
    if status != 200:
        raise StravaError("Falha ao obter/renovar token", status, body)
    return body


def apply_token(creds, tok):
    creds["access_token"] = tok["access_token"]
    creds["refresh_token"] = tok["refresh_token"]
    creds["expires_at"] = tok["expires_at"]
    if tok.get("athlete", {}).get("id"):
        creds["athlete_id"] = tok["athlete"]["id"]


def refresh(creds):
    if not creds.get("refresh_token"):
        raise StravaError("Sem refresh_token. Rode: strava.py auth")
    tok = token_request(creds, {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"]})
    apply_token(creds, tok)
    save_creds(persistable(creds))


LAST_RATE = {}


def check_rate_limit(headers):
    try:
        limit = [int(x) for x in headers.get("X-RateLimit-Limit", "").split(",")]
        usage = [int(x) for x in headers.get("X-RateLimit-Usage", "").split(",")]
    except ValueError:
        return
    if len(limit) == 2 and len(usage) == 2:
        LAST_RATE.update({"limit_15min": limit[0], "limit_day": limit[1],
                          "usage_15min": usage[0], "usage_day": usage[1]})
    for name, lim, use in zip(("15min", "dia"), limit, usage):
        if lim and use / lim >= 0.8:
            print(f"[aviso] rate limit ({name}): {use}/{lim}", file=sys.stderr)


def api_get(path, params=None, creds=None):
    creds = creds if creds is not None else load_creds()
    if not creds.get("access_token"):
        raise StravaError("Não autenticado. Rode: strava.py auth (veja references/setup.md)")
    if creds.get("expires_at", 0) - 60 < time.time():
        refresh(creds)
    query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{API_BASE}{path}" + (f"?{query}" if query else "")
    for attempt in (0, 1):
        status, headers, raw = _http(url, headers={"Authorization": f"Bearer {creds['access_token']}"})
        if status == 401 and attempt == 0:
            refresh(creds)
            continue
        break
    check_rate_limit(headers)
    body = _parse(raw)
    if status == 200:
        return body
    hints = {
        401: "Token inválido/revogado. Rode: strava.py auth",
        403: "Acesso negado: escopo insuficiente ou recurso privado. Reautorize com: strava.py auth",
        404: "Recurso não encontrado (ou sem permissão para vê-lo).",
        429: "Rate limit excedido (200 req/15min, 2000/dia). Aguarde antes de tentar de novo.",
    }
    raise StravaError(hints.get(status, f"Erro HTTP {status}"), status, body)


def athlete_id(creds):
    if not creds.get("athlete_id"):
        creds["athlete_id"] = api_get("/athlete", creds=creds)["id"]
        save_creds(persistable(creds))
    return creds["athlete_id"]


# ---------- OAuth ----------

class _CallbackHandler(BaseHTTPRequestHandler):
    result = {}

    def do_GET(self):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in qs or "error" in qs:
            type(self).result = {k: v[0] for k, v in qs.items()}
            msg = "Autorizado! Pode fechar esta aba." if "code" in qs else "Autorização negada."
            self.send_response(200)
        else:
            msg = "Aguardando callback do Strava."
            self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(msg.encode())

    def log_message(self, *args):
        pass


def wait_for_callback(port, timeout):
    _CallbackHandler.result = {}
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 1
    deadline = time.time() + timeout
    while not _CallbackHandler.result and time.time() < deadline:
        server.handle_request()
    server.server_close()
    return _CallbackHandler.result


def ask(prompt):
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def cmd_auth(args):
    creds = load_creds()
    creds["client_id"] = args.client_id or creds.get("client_id") or ask("Client ID: ")
    creds["client_secret"] = (
        args.client_secret or creds.get("client_secret") or getpass.getpass("Client Secret: ").strip()
    )
    redirect = f"http://localhost:{args.port}/exchange_token"
    state = secrets.token_urlsafe(16)
    url = f"{OAUTH_BASE}/authorize?" + urllib.parse.urlencode({
        "client_id": creds["client_id"], "response_type": "code", "redirect_uri": redirect,
        "approval_prompt": "force", "scope": args.scope, "state": state,
    })
    print(f"Abra no navegador e autorize:\n{url}\n", file=sys.stderr)

    if args.manual:
        pasted = ask("Cole a URL final (ou apenas o code): ")
        if pasted.startswith("http"):
            qs = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).items()}
        else:
            qs = {"code": pasted, "state": state}
    else:
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        print(f"Aguardando callback em {redirect} (timeout {args.timeout}s)...", file=sys.stderr)
        qs = wait_for_callback(args.port, args.timeout)

    if not qs:
        raise StravaError("Tempo esgotado sem callback. Tente: strava.py auth --manual")
    if qs.get("error"):
        raise StravaError(f"Autorização negada: {qs['error']}")
    if qs.get("state") != state:
        raise StravaError("State inválido no callback (possível CSRF). Abortado.")
    granted = (qs.get("scope") or args.scope).replace(" ", ",").split(",")
    tok = token_request(creds, {"grant_type": "authorization_code", "code": qs["code"]})
    apply_token(creds, tok)
    creds["scope"] = ",".join(granted)
    save_creds(persistable(creds))
    missing = [s for s in REQUIRED_SCOPES if s not in granted]
    out = {"ok": True, "athlete": tok.get("athlete", {}).get("username") or creds.get("athlete_id"),
           "scope": granted, "credentials_file": CRED_FILE}
    if missing:
        out["warning"] = f"Escopos não concedidos: {missing}. Reautorize marcando todas as caixas."
    return out


def cmd_status(args):
    c = load_creds()
    exp = c.get("expires_at")
    return {
        "credentials_file": CRED_FILE,
        "client_configured": bool(c.get("client_id") and c.get("client_secret")),
        "authenticated": bool(c.get("refresh_token")),
        "athlete_id": c.get("athlete_id"),
        "scope": c.get("scope"),
        "access_token_expires_at": datetime.fromtimestamp(exp).isoformat() if exp else None,
        "access_token_expired": bool(exp and exp < time.time()),
    }


def cmd_logout(args):
    c = _read_stored()
    for k in ("access_token", "refresh_token", "expires_at", "athlete_id", "scope"):
        c.pop(k, None)
    save_creds(c)
    return {"ok": True, "detail": "Tokens removidos (client_id/secret mantidos)."}


# ---------- helpers de dados ----------

def parse_when(s):
    """Aceita epoch, 'Nd' (N dias atrás) ou data/ISO (naive = fuso local)."""
    if s is None:
        return None
    s = str(s).strip()
    if s.isdigit():
        return int(s)
    if s.endswith("d") and s[:-1].isdigit():
        return int((datetime.now() - timedelta(days=int(s[:-1]))).timestamp())
    try:
        return int(datetime.fromisoformat(s).astimezone().timestamp())
    except ValueError:
        raise StravaError(f"Data inválida: {s!r} (use YYYY-MM-DD, ISO, epoch ou Nd)")


def fmt_pace(speed):
    if not speed:
        return None
    total = round(1000 / speed)
    return f"{total // 60}:{total % 60:02d}/km"


def compact(a):
    speed = a.get("average_speed")
    return {
        "id": a.get("id"), "name": a.get("name"),
        "sport_type": a.get("sport_type") or a.get("type"),
        "start_date_local": a.get("start_date_local"),
        "distance_km": round(a.get("distance", 0) / 1000, 2),
        "moving_time_min": round(a.get("moving_time", 0) / 60, 1),
        "elapsed_time_min": round(a.get("elapsed_time", 0) / 60, 1),
        "elevation_gain_m": a.get("total_elevation_gain"),
        "avg_speed_kmh": round(speed * 3.6, 2) if speed else None,
        "avg_pace": fmt_pace(speed),
        "avg_heartrate": a.get("average_heartrate"),
        "max_heartrate": a.get("max_heartrate"),
        "avg_watts": a.get("average_watts"),
        "gear_id": a.get("gear_id"),
    }


# ---------- comandos de leitura ----------

def cmd_athlete(args):
    return api_get("/athlete")


def cmd_stats(args):
    return api_get(f"/athletes/{athlete_id(load_creds())}/stats")


def cmd_zones(args):
    return api_get("/athlete/zones")


def cmd_activities(args):
    creds = load_creds()
    params = {"after": parse_when(args.after), "before": parse_when(args.before)}
    out, page = [], args.page
    while True:
        batch = api_get("/athlete/activities", {**params, "page": page, "per_page": args.per_page}, creds)
        out.extend(batch)
        if not args.all or len(batch) < args.per_page or page - args.page + 1 >= args.max_pages:
            break
        page += 1
    if args.type:
        wanted = {t.lower() for t in args.type.split(",")}
        out = [a for a in out if (a.get("sport_type") or "").lower() in wanted or (a.get("type") or "").lower() in wanted]
    if args.limit:
        out = out[: args.limit]
    return [compact(a) for a in out] if args.compact else out


def cmd_activity(args):
    return api_get(f"/activities/{args.id}", {"include_all_efforts": "true" if args.all_efforts else None})


def cmd_laps(args):
    return api_get(f"/activities/{args.id}/laps")


def cmd_activity_zones(args):
    return api_get(f"/activities/{args.id}/zones")


def cmd_streams(args):
    data = api_get(f"/activities/{args.id}/streams", {"keys": args.keys, "key_by_type": "true"})
    if args.every > 1 and isinstance(data, dict):
        for v in data.values():
            if isinstance(v, dict) and isinstance(v.get("data"), list):
                v["data"] = v["data"][:: args.every]
                v["downsampled_every"] = args.every
    return data


def cmd_gear(args):
    return api_get(f"/gear/{args.id}")


def cmd_routes(args):
    creds = load_creds()
    return api_get(f"/athletes/{athlete_id(creds)}/routes", {"page": args.page, "per_page": args.per_page}, creds)


def cmd_clubs(args):
    return api_get("/athlete/clubs")


def cmd_starred(args):
    return api_get("/segments/starred", {"page": args.page, "per_page": args.per_page})


def cmd_raw(args):
    params = {}
    for kv in args.param or []:
        if "=" not in kv:
            raise StravaError(f"--param deve ser k=v (recebido {kv!r})")
        k, v = kv.split("=", 1)
        params[k] = v
    path = args.path if args.path.startswith("/") else "/" + args.path
    return api_get(path, params)


# ---------- ferramentas (mesmos nomes/parâmetros do conector Strava) ----------
# Cobrem apenas o que a API v3 oferece; parâmetros sem suporte são ignorados com aviso em "notes".

STREAM_ALIASES = {
    "heart_rate": "heartrate", "speed": "velocity_smooth", "gps": "latlng", "power": "watts",
    "temperature": "temp", "elevation": "altitude", "grade": "grade_smooth",
}
GEAR_PREFIX_TYPE = {"b": "Bike", "g": "Shoe"}


def tool_params(args, allowed, ignored=()):
    p = {}
    if args.json:
        raw = sys.stdin.read() if args.json == "-" else args.json
        try:
            p = json.loads(raw)
        except ValueError as e:
            raise StravaError(f"--json inválido: {e}")
        if not isinstance(p, dict):
            raise StravaError("--json deve ser um objeto")
    for kv in args.param or []:
        k, sep, v = kv.partition("=")
        if not sep:
            raise StravaError(f"--param deve ser k=v (recebido {kv!r})")
        try:
            v = json.loads(v)
        except ValueError:
            pass
        p[k] = v
    unknown = [k for k in p if k not in allowed and k not in ignored]
    if unknown:
        raise StravaError(f"Parâmetros desconhecidos: {unknown}. Aceitos: {sorted(allowed)}")
    notes = [f"'{k}' não é suportado pela API v3 do Strava e foi ignorado" for k in ignored if k in p]
    return p, notes


def need(p, key):
    if p.get(key) in (None, ""):
        raise StravaError(f"Parâmetro obrigatório ausente: {key}")
    return str(p[key])


def as_int(p, key, default, lo=1, hi=200):
    try:
        v = int(p.get(key, default))
    except (TypeError, ValueError):
        raise StravaError(f"{key} deve ser inteiro")
    return max(lo, min(hi, v))


def as_bool(v):
    return v is True or str(v).lower() in ("1", "true", "yes", "sim")


def tool(fn, allowed, ignored=()):
    def handler(args):
        p, notes = tool_params(args, allowed, ignored)
        out = fn(p)
        if notes:
            out = {**out, "notes": notes}
        return out
    return handler


def granted_scopes(creds):
    return [s for s in (creds.get("scope") or "").replace(" ", ",").split(",") if s]


def t_eligibility(p):
    creds = load_creds()
    if not creds.get("refresh_token"):
        return {"eligible": False, "reason": "not_authenticated", "detail": "Rode: strava.py auth"}
    a = api_get("/athlete", creds=creds)
    subscriber = bool(a.get("summit") or a.get("premium"))
    scopes = granted_scopes(creds)
    missing = [s for s in REQUIRED_SCOPES if s not in scopes]
    reason = "ok" if subscriber and not missing else ("not_subscriber" if not subscriber else "missing_scopes")
    return {"eligible": subscriber and not missing, "reason": reason, "subscriber": subscriber,
            "scopes_granted": scopes, "missing_scopes": missing}


def t_health(p):
    creds = load_creds()
    if not creds.get("refresh_token"):
        raise StravaError("Não autenticado. Rode: strava.py auth")
    a = api_get("/athlete", creds=creds)
    return {"status": "ok", "api_reachable": True, "athlete_id": a.get("id"), "rate_limit": dict(LAST_RATE)}


def t_profile(p):
    a = api_get("/athlete")

    def gear(g):
        return {"id": g.get("id"), "name": g.get("name"), "distance_km": round(g.get("distance", 0) / 1000, 1)}

    return {
        "id": a.get("id"), "username": a.get("username"),
        "firstname": a.get("firstname"), "lastname": a.get("lastname"),
        "city": a.get("city"), "state": a.get("state"), "country": a.get("country"),
        "sex": a.get("sex"), "weight_kg": a.get("weight"), "ftp": a.get("ftp"),
        "measurement_preference": a.get("measurement_preference"),
        "subscriber": bool(a.get("summit") or a.get("premium")),
        "created_at": a.get("created_at"), "bio": a.get("bio"),
        "follower_count": a.get("follower_count"), "friend_count": a.get("friend_count"),
        "bikes": [gear(g) for g in a.get("bikes") or []],
        "shoes": [gear(g) for g in a.get("shoes") or []],
    }


def t_zones(p):
    z = api_get("/athlete/zones")
    return {"heart_rate": z.get("heart_rate"), "power": z.get("power")}


def iso_bound(value, end=False):
    if not value:
        return None
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise StravaError(f"Data inválida: {s!r} (use ISO, ex.: 2026-09-01T00:00:00)")
    if len(s) == 10 and end:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=None)


def activity_item(a, polyline, tags):
    speed = a.get("average_speed")
    out = {
        "id": a.get("id"), "name": a.get("name"), "sport_type": a.get("sport_type") or a.get("type"),
        "start_date_local": a.get("start_date_local"),
        "distance_m": a.get("distance"), "distance_km": round(a.get("distance", 0) / 1000, 2),
        "moving_time_s": a.get("moving_time"), "elapsed_time_s": a.get("elapsed_time"),
        "elevation_gain_m": a.get("total_elevation_gain"),
        "average_speed_kmh": round(speed * 3.6, 2) if speed else None, "average_pace": fmt_pace(speed),
        "max_speed_kmh": round(a["max_speed"] * 3.6, 2) if a.get("max_speed") else None,
        "average_heartrate": a.get("average_heartrate"), "max_heartrate": a.get("max_heartrate"),
        "average_watts": a.get("average_watts"),
        "relative_effort": a.get("suffer_score"),
        "kudos_count": a.get("kudos_count"), "achievement_count": a.get("achievement_count"),
        "pr_count": a.get("pr_count"), "comment_count": a.get("comment_count"),
    }
    if polyline:
        out["polyline"] = (a.get("map") or {}).get("summary_polyline")
    if tags:
        out.update({k: a.get(k) for k in ("commute", "trainer", "manual", "private", "workout_type", "gear_id")})
    return out


def t_list_activities(p):
    start, end = iso_bound(p.get("range_start")), iso_bound(p.get("range_end"), end=True)
    first = as_int(p, "first", 30)
    ordering = p.get("ordering", "StartDateLocalDesc")
    if ordering not in ("StartDateLocalDesc", "StartDateLocalAsc"):
        raise StravaError("ordering deve ser StartDateLocalDesc ou StartDateLocalAsc")
    desc = ordering == "StartDateLocalDesc"
    try:
        offset = int(p.get("after") or 0)
    except ValueError:
        raise StravaError("after deve ser o cursor retornado em page_info.end_cursor")
    day = 86400
    params = {
        "after": int(start.replace(tzinfo=timezone.utc).timestamp()) - day if start else None,
        "before": int(end.replace(tzinfo=timezone.utc).timestamp()) + day if end else None,
    }
    # a API devolve do mais antigo ao mais novo quando há "after"; caso contrário, do mais novo ao mais antigo
    natural_desc = start is None
    needed = offset + first + 1
    creds, items, page = load_creds(), [], 1
    while page <= 10:
        batch = api_get("/athlete/activities", {**params, "page": page, "per_page": 200}, creds)
        for a in batch:
            local = (a.get("start_date_local") or "")[:19]
            if (start and local < start.isoformat()) or (end and local > end.isoformat()):
                continue
            items.append(a)
        page += 1
        if len(batch) < 200 or (natural_desc == desc and len(items) >= needed):
            break
    items.sort(key=lambda a: a.get("start_date_local") or "", reverse=desc)
    window = items[offset: offset + first]
    has_next = len(items) > offset + first or (page <= 10 and natural_desc == desc and len(items) >= needed)
    return {
        "activities": [activity_item(a, as_bool(p.get("include_polyline")), as_bool(p.get("include_tags"))) for a in window],
        "page_info": {"has_next_page": has_next, "end_cursor": str(offset + len(window)) if has_next else None},
    }


def t_performance(p):
    aid = need(p, "activity_id")
    a = api_get(f"/activities/{aid}", {"include_all_efforts": "true"})
    try:
        laps = api_get(f"/activities/{aid}/laps")
    except StravaError as e:
        if e.status != 404:
            raise
        laps = []
    speed = a.get("average_speed")
    return {
        "id": a.get("id"), "name": a.get("name"), "sport_type": a.get("sport_type") or a.get("type"),
        "start_date_local": a.get("start_date_local"), "description": a.get("description"),
        "distance_m": a.get("distance"), "moving_time_s": a.get("moving_time"),
        "elapsed_time_s": a.get("elapsed_time"), "elevation_gain_m": a.get("total_elevation_gain"),
        "average_speed_kmh": round(speed * 3.6, 2) if speed else None, "average_pace": fmt_pace(speed),
        "average_heartrate": a.get("average_heartrate"), "max_heartrate": a.get("max_heartrate"),
        "average_watts": a.get("average_watts"), "weighted_average_watts": a.get("weighted_average_watts"),
        "max_watts": a.get("max_watts"), "average_cadence": a.get("average_cadence"),
        "calories": a.get("calories"), "kilojoules": a.get("kilojoules"),
        "perceived_exertion": a.get("perceived_exertion"), "relative_effort": a.get("suffer_score"),
        "pr_count": a.get("pr_count"), "achievement_count": a.get("achievement_count"),
        "device_name": a.get("device_name"), "gear_id": a.get("gear_id"),
        "segment_efforts": [
            {k: s.get(k) for k in ("name", "distance", "elapsed_time", "moving_time", "pr_rank", "kom_rank",
                                   "average_heartrate", "max_heartrate", "average_watts")} | {"segment_id": (s.get("segment") or {}).get("id")}
            for s in a.get("segment_efforts") or []
        ],
        "best_efforts": [
            {k: b.get(k) for k in ("name", "distance", "elapsed_time", "moving_time", "pr_rank")}
            for b in a.get("best_efforts") or []
        ],
        "laps": [
            {k: l.get(k) for k in ("lap_index", "name", "distance", "elapsed_time", "moving_time", "total_elevation_gain",
                                   "average_speed", "average_heartrate", "max_heartrate", "average_cadence", "average_watts")}
            for l in laps
        ],
    }


def t_streams(p):
    aid = need(p, "activity_id")
    requested = p.get("streams") or DEFAULT_STREAMS.split(",")
    if isinstance(requested, str):
        requested = requested.split(",")
    keys = [STREAM_ALIASES.get(k.strip(), k.strip()) for k in requested]
    raw = api_get(f"/activities/{aid}/streams", {"keys": ",".join(keys), "key_by_type": "true"})
    data = {k: v.get("data", []) for k, v in raw.items() if isinstance(v, dict)}
    size = max((len(v) for v in data.values()), default=0)
    resolution = as_int(p, "resolution", size or 1, lo=2, hi=100000) if p.get("resolution") else None
    if resolution and size > resolution:
        idx = [round(i * (size - 1) / (resolution - 1)) for i in range(resolution)]
        data = {k: [v[i] for i in idx if i < len(v)] for k, v in data.items()}
    return {"activity_id": aid, "original_points": size,
            "returned_points": max((len(v) for v in data.values()), default=0), "streams": data}


def t_gear(p):
    types = p.get("gear_types") or ["Bike", "Shoe"]
    if isinstance(types, str):
        types = [types]
    types = {t.capitalize() for t in types}
    creds = load_creds()
    athlete = api_get("/athlete", creds=creds)
    ids = [g["id"] for g in (athlete.get("bikes") or []) + (athlete.get("shoes") or []) if g.get("id")]
    if not ids:  # contas sem equipamento no perfil: descobrir pelos gear_id das atividades recentes
        recent = api_get("/athlete/activities", {"per_page": 200}, creds)
        ids = sorted({a["gear_id"] for a in recent if a.get("gear_id")})
    out = []
    for gid in ids:
        gtype = GEAR_PREFIX_TYPE.get(gid[:1], "Unknown")
        if gtype not in types:
            continue
        g = api_get(f"/gear/{gid}", creds=creds)
        item = {"id": gid, "type": gtype, "name": g.get("name"), "primary": g.get("primary"),
                "distance_m": g.get("distance"), "distance_km": round(g.get("distance", 0) / 1000, 1),
                "retired": g.get("retired"), "brand_name": g.get("brand_name"), "model_name": g.get("model_name"),
                "description": g.get("description")}
        if gtype == "Bike":
            item.update({"frame_type": g.get("frame_type"), "weight": g.get("weight")})
        out.append(item)
    return {"gear": out}


def t_clubs(p):
    first = as_int(p, "clubs_first", 10)
    try:
        offset = int(p.get("clubs_after") or 0)
    except ValueError:
        raise StravaError("clubs_after deve ser o cursor retornado em page_info.end_cursor")
    creds = load_creds()
    clubs = api_get("/athlete/clubs", {"per_page": 200}, creds)
    window = clubs[offset: offset + first]
    out = []
    for c in window:
        if c.get("member_count") is None:
            c = {**c, **api_get(f"/clubs/{c['id']}", creds=creds)}
        out.append({k: c.get(k) for k in ("id", "name", "sport_type", "city", "state", "country", "member_count", "private", "url")})
    has_next = len(clubs) > offset + first
    return {"clubs": out, "page_info": {"has_next_page": has_next, "end_cursor": str(offset + len(window)) if has_next else None}}


TOOLS = {
    "eligibility": (tool(t_eligibility, set()), "Verifica assinatura e escopos da conta"),
    "health": (tool(t_health, set()), "Verifica credenciais e conectividade com a API"),
    "get_athlete_profile": (tool(t_profile, set()), "Perfil do atleta"),
    "list_activities": (tool(t_list_activities, {"range_start", "range_end", "first", "ordering", "include_polyline", "include_tags", "after"}),
                        "Lista atividades (filtro de datas, ordenação, cursor)"),
    "get_activity_performance": (tool(t_performance, {"activity_id"}), "Desempenho de uma atividade (FC, potência, voltas, segmentos)"),
    "get_activity_streams": (tool(t_streams, {"activity_id", "streams", "resolution"}), "Séries temporais de uma atividade"),
    "get_athlete_zones": (tool(t_zones, set(), {"as_of_date"}), "Zonas de FC e potência atuais"),
    "get_gear": (tool(t_gear, {"gear_types"}), "Bikes e tênis"),
    "get_club_info": (tool(t_clubs, {"clubs_first", "clubs_after"}, {"events_first"}), "Clubes do atleta"),
}


# ---------- CLI ----------

def build_parser():
    p = argparse.ArgumentParser(description="Strava API v3 (somente leitura)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        return sp

    a = add("auth", cmd_auth, "Autoriza o app (OAuth) e salva tokens")
    a.add_argument("--client-id")
    a.add_argument("--client-secret")
    a.add_argument("--scope", default=DEFAULT_SCOPE)
    a.add_argument("--port", type=int, default=8754)
    a.add_argument("--timeout", type=int, default=300)
    a.add_argument("--manual", action="store_true", help="Colar a URL de callback manualmente")
    a.add_argument("--no-browser", action="store_true")
    add("status", cmd_status, "Mostra estado da autenticação (sem rede)")
    add("logout", cmd_logout, "Remove tokens locais")
    add("athlete", cmd_athlete, "Perfil do atleta")
    add("stats", cmd_stats, "Estatísticas (recent/ytd/all-time)")
    add("zones", cmd_zones, "Zonas de FC/potência do atleta")

    s = add("activities", cmd_activities, "Lista atividades")
    s.add_argument("--after", help="YYYY-MM-DD, ISO, epoch ou Nd (ex.: 30d)")
    s.add_argument("--before")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--per-page", type=int, default=30)
    s.add_argument("--all", action="store_true", help="Paginar automaticamente")
    s.add_argument("--max-pages", type=int, default=10)
    s.add_argument("--type", help="Filtro por sport_type/type, ex.: Run,Ride")
    s.add_argument("--limit", type=int)
    s.add_argument("--compact", action="store_true", help="Campos resumidos com unidades legíveis")

    s = add("activity", cmd_activity, "Detalhes de uma atividade")
    s.add_argument("id")
    s.add_argument("--all-efforts", action="store_true")
    for name, fn, h in (("laps", cmd_laps, "Voltas"), ("activity-zones", cmd_activity_zones, "Zonas da atividade"),
                        ("gear", cmd_gear, "Equipamento (ex.: b12345)")):
        add(name, fn, h).add_argument("id")
    s = add("streams", cmd_streams, "Séries temporais da atividade")
    s.add_argument("id")
    s.add_argument("--keys", default=DEFAULT_STREAMS)
    s.add_argument("--every", type=int, default=1, help="Amostrar 1 a cada N pontos")
    for name, fn, h in (("routes", cmd_routes, "Rotas do atleta"), ("starred-segments", cmd_starred, "Segmentos favoritos")):
        s = add(name, fn, h)
        s.add_argument("--page", type=int, default=1)
        s.add_argument("--per-page", type=int, default=30)
    add("clubs", cmd_clubs, "Clubes do atleta")
    s = add("raw", cmd_raw, "GET em qualquer endpoint (ex.: /athlete/activities)")
    s.add_argument("path")
    s.add_argument("--param", action="append", help="k=v (repetível)")

    for name, (fn, help_) in TOOLS.items():
        sp = sub.add_parser(name, aliases=[name.replace("_", "-")], help=f"[ferramenta] {help_}")
        sp.set_defaults(fn=fn)
        sp.add_argument("--json", help="Parâmetros em JSON ('-' lê do stdin)")
        sp.add_argument("--param", action="append", help="k=v (repetível; valor aceita JSON)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = args.fn(args)
    except StravaError as e:
        print(json.dumps({"error": str(e), "status": e.status, "detail": e.detail}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
