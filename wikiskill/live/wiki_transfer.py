from __future__ import annotations

import base64
import binascii
import difflib
import hashlib
import hmac
import io
import re
import secrets
import stat
import uuid
import zipfile
import zlib

from .config import digest
from .runtime import validate_pages, write_pages
from .store import Store, dumps
from .views import ReadView, rows


MAX_ZIP_BYTES = 10 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_PAGES = 1000


def read_archive(encoded: str) -> tuple[bytes, list[dict]]:
    """Read a bounded ZIP in memory and validate every page before returning it."""
    if not isinstance(encoded, str):
        raise ValueError("请提供 ZIP 文件的 base64 内容")
    if len(encoded) > 4 * ((MAX_ZIP_BYTES + 2) // 3):
        raise ValueError("ZIP 文件超过 10 MiB 限制")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("无效 ZIP：文件内容不是合法 base64") from exc
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("ZIP 文件超过 10 MiB 限制")
    pages, names, total = [], set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if not entries:
                raise ValueError("ZIP 中没有 Markdown 页面")
            if len(entries) > MAX_PAGES:
                raise ValueError("ZIP 最多包含 1000 个页面")
            for entry in entries:
                match = re.fullmatch(r"([a-z0-9][a-z0-9-]{0,100})\.md", entry.orig_filename)
                if match is None:
                    raise ValueError(f"非法页面名称：{entry.orig_filename!r}；请使用根目录的小写字母、数字或连字符名称.md，名称最长 101 字符")
                name = match[1]
                if name in names:
                    raise ValueError(f"ZIP 中存在重复页面：{name}")
                names.add(name)
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if entry.is_dir() or mode not in (0, stat.S_IFREG):
                    raise ValueError(f"{entry.filename} 必须是普通 Markdown 文件")
                if entry.flag_bits & 1:
                    raise ValueError(f"不支持加密 ZIP：{entry.filename}")
                if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError("ZIP 仅支持 Stored 或 Deflate 压缩")
                if entry.file_size > MAX_PAGE_BYTES:
                    raise ValueError(f"{entry.filename} 超过单页 2 MiB 限制")
                total += entry.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("ZIP 解压后的正文总量超过 20 MiB 限制")
                with archive.open(entry) as stream:
                    content = stream.read(MAX_PAGE_BYTES + 1)
                if len(content) > MAX_PAGE_BYTES:
                    raise ValueError(f"{entry.filename} 超过单页 2 MiB 限制")
                try:
                    body = content.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValueError(f"{entry.filename} 不是 UTF-8 正文") from exc
                if not body.lstrip("\ufeff").strip():
                    raise ValueError(f"{entry.filename} 的正文为空")
                pages.append({"name": name, "body": body})
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError, UnicodeError, zlib.error) as exc:
        raise ValueError("无效 ZIP：无法读取文件或校验失败") from exc
    validate_pages(pages)
    return data, sorted(pages, key=lambda page: page["name"])


class WikiTransfer:
    """Own archive previews and imports; Store owns transactions and write_pages owns history."""

    def __init__(self, view: ReadView):
        self.view = view
        self._key = secrets.token_bytes(32)

    def export(self, project: str, names: list[str] | None = None) -> bytes:
        if names is not None and (not isinstance(names, list) or not names or
                any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,100}", name) for name in names) or
                len(set(names)) != len(names)):
            raise ValueError("请选择一个或多个合法且不重复的 Wiki 页面")
        with self.view.read() as db:
            self.view.require_project(db, project)
            where, args = "project=?", [project]
            if names is not None:
                where += " AND name IN (SELECT value FROM json_each(?))"
                args.append(dumps(names))
            pages = rows(db, "SELECT name,body FROM wiki WHERE " + where + " ORDER BY name", args)
            if names is not None and len(pages) != len(names):
                raise ValueError("选定的 Wiki 页面已不存在，请刷新后重新选择")
        validate_pages(pages)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for page in pages:
                archive.writestr(page["name"] + ".md", page["body"].encode("utf-8"))
        return output.getvalue()

    def _preview(self, db, project: str, data: bytes, pages: list[dict]) -> dict:
        self.view.require_project(db, project)
        current = {row["name"]: row for row in rows(db, "SELECT name,body FROM wiki WHERE project=? ORDER BY name", (project,))}
        # The event sequence also detects edits that restore the previous body.
        revision = db.execute("SELECT coalesce(max(seq),0) FROM runtime_events WHERE project=? AND kind='wiki.updated'", (project,)).fetchone()[0]
        expected = {name: digest(page["body"]) for name, page in current.items()}
        archive_digest = hashlib.sha256(data).hexdigest()
        signed = dumps({"project": project, "archive": archive_digest, "expected": expected, "revision": revision})
        token = hmac.new(self._key, signed.encode("utf-8"), hashlib.sha256).hexdigest()
        items = []
        for page in pages:
            name, body = page["name"], page["body"]
            before = current[name]["body"] if name in current else None
            status = "added" if before is None else "unchanged" if before == body else "modified"
            items.append({"name": name, "status": status, "body": body, "before": before,
                          "expected_digest": expected.get(name),
                          "diff": "".join(difflib.unified_diff((before or "").splitlines(keepends=True),
                                    body.splitlines(keepends=True), fromfile=name + " (当前)", tofile=name + " (导入)")) if status != "unchanged" else ""})
        return {"pages": items, "preview_token": token,
                "counts": {status: sum(p["status"] == status for p in items) for status in ("added", "modified", "unchanged")}}

    def preview(self, project: str, archive_base64: str) -> dict:
        data, pages = read_archive(archive_base64)
        with self.view.read() as db:
            return self._preview(db, project, data, pages)

    def apply(self, store: Store, project: str, archive_base64: str, preview_token: str, overwrite: list[str]) -> dict:
        data, pages = read_archive(archive_base64)
        if not isinstance(preview_token, str) or not re.fullmatch(r"[a-f0-9]{64}", preview_token):
            raise ValueError("请先预览 ZIP，再明确执行导入")
        if not isinstance(overwrite, list) or any(not isinstance(name, str) for name in overwrite) or len(set(overwrite)) != len(overwrite):
            raise ValueError("请逐页确认所有同名修改")
        with store.transaction() as db:
            preview = self._preview(db, project, data, pages)
            if not hmac.compare_digest(preview_token, preview["preview_token"]):
                raise ValueError("项目 Wiki、ZIP 或预览已变化；本次整批未导入，请重新预览并确认")
            modified = {page["name"] for page in preview["pages"] if page["status"] == "modified"}
            if set(overwrite) != modified:
                raise ValueError("请查看并逐页确认所有同名修改后再导入")
            changes = [{"name": page["name"], "body": page["body"]} for page in preview["pages"] if page["status"] != "unchanged"]
            count = write_pages(db, project, changes, {"source": "wiki-zip"}, "manual-import-" + uuid.uuid4().hex,
                                record_body_changes=True)
        return {"project_id": project, "new_changes": count, "counts": preview["counts"]}
