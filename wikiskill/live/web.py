from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .views import ChangeCursor, ReadView


Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


async def event_stream(view: ReadView, request: Request):
    cursor = ChangeCursor(view)
    previous = None
    ticks = 0
    try:
        while not await request.is_disconnected():
            try:
                revision, seq = cursor.poll()
                if revision != previous:
                    yield f"id: {seq}\nevent: change\ndata: {json.dumps({'cursor': seq})}\n\n"
                    previous = revision
                elif ticks % 10 == 0:
                    yield ": heartbeat\n\n"
            except (OSError, sqlite3.Error) as exc:
                yield "event: unavailable\ndata: " + json.dumps({"message": str(exc)}) + "\n\n"
                cursor.close()
                previous = None
            ticks += 1
            await asyncio.sleep(1)
    finally:
        cursor.close()


def create_app(root: str | Path = "~/.wikiskill") -> FastAPI:
    view = ReadView(root)
    app = FastAPI(title="WikiSkill", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.view = view

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "仅允许本机地址"}, status_code=403)
        origin = request.headers.get("origin")
        if request.url.path.startswith("/api/") and (
            request.headers.get("sec-fetch-site") == "cross-site" or
            (origin is not None and origin != f"{request.url.scheme}://{host}")
        ):
            return JSONResponse({"detail": "仅允许从当前页面读取数据"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError):
        return JSONResponse({"detail": str(exc.args[0])}, status_code=404)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request: Request, exc: sqlite3.Error):
        return JSONResponse({"detail": f"暂时无法读取状态数据库：{exc}"}, status_code=503)

    @app.get("/api/snapshot")
    def snapshot():
        return view.snapshot()

    @app.get("/api/events")
    async def events(request: Request):
        return StreamingResponse(event_stream(view, request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/api/jobs")
    def jobs(project: str | None = None, state: Literal["queued", "generating", "prepared", "applied", "done", "failed"] | None = None,
             stage: Literal["raw", "skill"] | None = None, offset: Offset = 0, limit: Limit = 20):
        return view.jobs(project, state, stage, offset, limit)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return view.job(job_id)

    @app.get("/api/jobs/{job_id}/inputs")
    def inputs(job_id: str, offset: Offset = 0, limit: Limit = 50):
        return view.inputs(job_id, offset, limit)

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str, offset: Offset = 0, limit: Limit = 50):
        return view.events(job_id, offset, limit)

    @app.get("/api/jobs/{job_id}/report")
    def report(job_id: str):
        value = view.job(job_id)["report"]
        if value is None:
            raise KeyError("报告尚未生成")
        return Response(json.dumps(value, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(job_id + ".json", safe="")})

    @app.get("/api/projects/{project}/raw")
    def raw(project: str, offset: Offset = 0, limit: Limit = 20):
        return view.raw(project, offset, limit)

    @app.get("/api/projects/{project}/raw/{raw_id}")
    def raw_item(project: str, raw_id: str):
        return view.raw_item(project, raw_id)

    @app.get("/api/projects/{project}/wiki")
    def wiki(project: str, offset: Offset = 0, limit: Limit = 20):
        return view.wiki(project, offset, limit)

    @app.get("/api/projects/{project}/wiki/{name}")
    def wiki_item(project: str, name: str, offset: Offset = 0, limit: Limit = 20):
        return view.wiki_item(project, name, offset, limit)

    @app.get("/api/skills/{skill_id}")
    def skill(skill_id: str, offset: Offset = 0, limit: Limit = 20):
        return view.skill(skill_id, offset, limit)

    @app.get("/api/skills/{skill_id}/versions/{version_id}")
    def version(skill_id: str, version_id: str):
        return view.version(skill_id, version_id)

    @app.get("/api/skills/{skill_id}/versions/{version_id}/file")
    def version_file(skill_id: str, version_id: str, side: Literal["before", "after"], name: str):
        content = view.version_file(skill_id, version_id, side, name)
        return Response(content, media_type="application/octet-stream",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(Path(name).name, safe="")})

    assets = Path(__file__).with_name("web_assets")
    if (assets / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=assets / "assets"), name="assets")

    @app.get("/")
    def index():
        if not (assets / "index.html").is_file():
            return JSONResponse({"detail": "WebUI 资源尚未构建，请在 web 目录执行 npm ci && npm run build"}, status_code=503)
        return FileResponse(assets / "index.html")

    return app


def serve_web(root: str | Path, host: str = "127.0.0.1", port: int = 8765):
    import uvicorn

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("WebUI binds to localhost only")
    if not 1 <= port <= 65535:
        raise ValueError("Use a port between 1 and 65535")
    address = f"[{host}]" if ":" in host else host
    print(f"WikiSkill WebUI: http://{address}:{port}", flush=True)
    uvicorn.run(create_app(root), host=host, port=port, log_level="warning", timeout_graceful_shutdown=2)
