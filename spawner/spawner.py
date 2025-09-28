import os, time, threading, re
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, PlainTextResponse
import docker, requests

INACTIVITY = int(os.getenv("INACTIVITY_SECONDS", "900"))
TRAEFIK_MW = os.getenv("TRAEFIK_MIDDLEWARE", "forward-auth@file")
EDGE_NETWORK = os.getenv("EDGE_NETWORK", "edge")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1")
IMG = "jlesage/firefox:latest"
PORT = 5800

cli = docker.from_env()
app = FastAPI()
last_seen = {}  # name -> ts

# Ждём путь вида /u/<sub>/...
RE_SUB = re.compile(r"^/u/(?P<sub>[a-zA-Z0-9._-]+)($|/.*)")

def sub_from_path(path: str):
    m = RE_SUB.match(path or "")
    return m.group("sub") if m else None

def cname(sub): return f"browser_{sub}"

def ensure_container(sub: str):
    name = cname(sub)
    try:
        c = cli.containers.get(name)
        if c.status != "running":
            c.start()
    except docker.errors.NotFound:
        labels = {
          "traefik.enable": "true",
          # Роут на уникальный префикс /u/<sub>
          f"traefik.http.routers.{sub}.rule": f"PathPrefix(`/u/{sub}`)",
          f"traefik.http.routers.{sub}.entrypoints": "web",
          # Снимаем /u/<sub> перед отдачей в сервис
          f"traefik.http.routers.{sub}.middlewares": f"{TRAEFIK_MW},strip-{sub}",
          f"traefik.http.middlewares.strip-{sub}.stripprefix.prefixes": f"/u/{sub}",
          f"traefik.http.services.{sub}.loadbalancer.server.port": str(PORT),
        }
        c = cli.containers.run(
            IMG, name=name, detach=True, labels=labels,
            network=EDGE_NETWORK,
            environment={
                "FF_OPEN_URL": f"{APP_BASE_URL}/u/{sub}/",  # старт сразу в свой префикс
                "WEB_AUTHENTICATION": "1",
                "WEB_AUTHENTICATION_USERNAME": sub,
                "WEB_AUTHENTICATION_PASSWORD": "set-strong-pass",
                "SECURE_CONNECTION": "1",
                "DISPLAY_WIDTH": "1920", "DISPLAY_HEIGHT": "1080",
            }
        )
    # ждём готовность
    for _ in range(50):
        try:
            requests.get(f"http://{name}:{PORT}", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)

@app.get("/")
def spawn_root(request: Request):
    # Fallback-роутер даёт сюда любые /u/<sub>*, берём sub из пути
    path = request.url.path
    sub = sub_from_path(path)
    if not sub:
        return PlainTextResponse("use /u/<sub>", status_code=400)
    ensure_container(sub)
    # редиректим на тот же URL — теперь сработает роутер контейнера
    return RedirectResponse(url=str(request.url), status_code=302)

@app.get("/auth")
def auth(request: Request):
    # ForwardAuth присылает оригинальный путь в X-Forwarded-Uri
    path = request.headers.get("x-forwarded-uri", request.url.path)
    sub = sub_from_path(path)
    if not sub: return PlainTextResponse("forbidden", status_code=403)
    last_seen[cname(sub)] = time.time()
    return PlainTextResponse("ok", status_code=200)

def reaper():
    while True:
        now = time.time()
        for name, ts in list(last_seen.items()):
            if now - ts > INACTIVITY:
                try:
                    c = cli.containers.get(name)
                    c.stop(timeout=10); c.remove()
                except Exception:
                    pass
                last_seen.pop(name, None)
        time.sleep(30)

threading.Thread(target=reaper, daemon=True).start()