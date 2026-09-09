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
    from .wiki_transfer import WikiTransfer

    view = ReadView(root)
    transfer = WikiTransfer(view)
    app = FastAPI(title="WikiSkill", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.view = view

    @app.exception_handler(OSError)
    async def filesystem_error(request: Request, exc: OSError):
        return JSONResponse({"detail": f"文件操作失败，已有数据和恢复记录已保留：{exc}"}, status_code=409)

    def runtime():
        from .runtime import Runtime
        from .config import Config
        return Runtime(Config.load(view.root))

    @app.exception_handler(RuntimeError)
    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(BlockingIOError)
    async def busy(request: Request, exc: BlockingIOError):
        return JSONResponse({"detail": "后台正在执行，请稍后再试"}, status_code=409)

    @app.get("/api/settings")
    def settings():
        from .config import Config
        from .settings import public_settings
        return public_settings(Config.load(view.root))

    @app.put("/api/settings")
    def update_settings(values: dict):
        from .settings import save_settings
        value = save_settings(view.root, values)
        runtime().wake()
        return value

    @app.post("/api/start")
    def start():
        from .services import ensure_services
        return ensure_services(view.root)

    @app.get("/api/services")
    def services():
        from .services import service_status
        current = getattr(app.state, "services", None)
        value = current.status() if current else service_status(view.root)
        return {k: v for k, v in value.items() if k != "identity"}

    @app.post("/api/history-import")
    def history_import():
        from .capture import Collector
        current = runtime()
        result = Collector(current.config).request_history()
        current.wake()
        return result

    @app.post("/api/projects")
    def project_create(values: dict):
        if set(values) != {"path"} or not isinstance(values["path"], str):
            raise ValueError("Supply a project path")
        return {"id": runtime().store.project(values["path"])}

    @app.get("/api/projects/{project}/turns")
    def turns(project: str, offset: Offset = 0, limit: Limit = 20):
        from .traces import TraceView
        return TraceView(view.root).turns(project, offset, limit)

    @app.get("/api/projects/{project}/wiki-inputs")
    def wiki_inputs(project: str, skill: str, offset: Offset = 0, limit: Limit = 20):
        from .views import rows, page
        with view.read() as db:
            view.require_project(db, project)
            if not db.execute("SELECT 1 FROM project_skills WHERE project=? AND skill=?", (project, skill)).fetchone():
                raise KeyError("Skill 不属于当前项目")
            values = rows(db, "SELECT id,name,substr(body,1,180) excerpt FROM wiki_changes w WHERE project=? AND NOT EXISTS "
                          "(SELECT 1 FROM consumed_wiki c WHERE c.change_id=w.id AND c.skill=?) ORDER BY id LIMIT ? OFFSET ?",
                          (project, skill, limit + 1, offset))
            return page(values, offset, limit)

    @app.get("/api/projects/{project}/traces")
    def traces(project: str, turn_id: int | None = None, offset: Offset = 0, limit: Limit = 50):
        from .traces import TraceView
        return TraceView(view.root).records(project, turn_id, offset, limit)

    @app.post("/api/projects/{project}/convert")
    def convert(project: str, values: dict):
        if set(values) - {"stage", "inputs", "skill"} or "stage" not in values:
            raise ValueError("Supply stage, optional input IDs and Skill")
        current = runtime()
        result = current.enqueue(project, **values)
        current.wake()
        return result

    @app.post("/api/projects/{project}/skill-generation/preview")
    def skill_generation_preview(project: str, values: dict):
        from .skill_generation import SkillGeneration
        if set(values) != {"pages"}:
            raise ValueError("Supply selected Wiki page names")
        return SkillGeneration(runtime()).preview(project, **values)

    @app.post("/api/projects/{project}/skill-generation")
    def skill_generation(project: str, values: dict):
        from .skill_generation import SkillGeneration
        if set(values) - {"pages", "expected", "name", "skill", "skill_digest"} or not {"pages", "expected"} <= set(values):
            raise ValueError("Supply selected Wiki pages, digests and a new name or merge target")
        current = runtime()
        result = SkillGeneration(current).enqueue(project, **values)
        current.wake()
        return result

    @app.put("/api/projects/{project}/wiki")
    def write_wiki(project: str, values: dict):
        if set(values) != {"pages", "expected"}:
            raise ValueError("Supply pages and expected digests")
        current = runtime()
        found = current.store.rows("SELECT path FROM projects WHERE id=?", (project,))
        if not found:
            raise KeyError("项目不存在")
        result = current.put_wiki(found[0]["path"], **values)
        current.wake()
        return result

    @app.get("/api/projects/{project}/wiki-export")
    def export_wiki(project: str):
        return Response(transfer.export(project), media_type="application/zip",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("wiki-" + project + ".zip", safe="")})

    @app.post("/api/projects/{project}/wiki-export")
    def export_selected_wiki(project: str, values: dict):
        if set(values) != {"pages"} or not isinstance(values["pages"], list):
            raise ValueError("请提供选定 Wiki 的 pages 列表")
        return Response(transfer.export(project, values["pages"]), media_type="application/zip",
                        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote("wiki-" + project + "-selected.zip", safe="")})

    @app.post("/api/projects/{project}/wiki-import/preview")
    def preview_wiki_import(project: str, values: dict):
        if set(values) != {"archive_base64"}:
            raise ValueError("请提供 archive_base64 以预览 ZIP")
        return transfer.preview(project, **values)

    @app.post("/api/projects/{project}/wiki-import")
    def import_wiki(project: str, values: dict):
        if set(values) != {"archive_base64", "preview_token", "overwrite"}:
            raise ValueError("请提供 ZIP、预览结果和同名修改确认列表")
        current = runtime()
        result = transfer.apply(current.store, project, **values)
        if result["new_changes"]:
            current.wake()
        return result

    @app.post("/api/projects/{project}/wiki/rollback")
    def rollback_wiki(project: str, values: dict):
        if set(values) != {"change_id", "expected"} or type(values["change_id"]) is not int:
            raise ValueError("Supply change_id and expected digest")
        return runtime().rollback_wiki(project, **values)

    @app.post("/api/jobs/{job_id}/retry")
    def retry(job_id: str, values: dict):
        if set(values) != {"regenerate"} or type(values["regenerate"]) is not bool:
            raise ValueError("Supply regenerate boolean")
        current = runtime()
        result = current.retry(job_id, **values)
        current.wake()
        return result

    @app.get("/api/skills/{skill_id}/install")
    def install_preview(skill_id: str):
        from .install import Installer
        return Installer(runtime().store).preview(skill_id)

    @app.post("/api/skills/{skill_id}/install")
    def install(skill_id: str, values: dict):
        from .install import Installer
        if set(values) != {"source_digest", "target_digest", "overwrite"} or type(values["overwrite"]) is not bool:
            raise ValueError("Supply preview digests and overwrite boolean")
        return Installer(runtime().store).install(skill_id, **values)

    @app.post("/api/skills/{skill_id}/rollback")
    def rollback(skill_id: str, values: dict):
        if set(values) != {"version_id", "side"}:
            raise ValueError("Supply version_id and side")
        return runtime().skills.rollback(skill_id, **values)

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
    def wiki(project: str, offset: Offset = 0, limit: Limit = 20, q: str = ""):
        return view.wiki(project, offset, limit, q)

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
