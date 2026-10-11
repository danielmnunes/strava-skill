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
from datetime import datetime, timedelta
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


def check_rate_limit(headers):
    try:
        limit = [int(x) for x in headers.get("X-RateLimit-Limit", "").split(",")]
        usage = [int(x) for x in headers.get("X-RateLimit-Usage", "").split(",")]
    except ValueError:
        return
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
