import base64

from .views import ReadView, page, rows


class TraceView(ReadView):
    """Read indexed transcript pages and expose exact bytes separately from display text."""

    def turns(self, project, offset=0, limit=20):
        with self.read() as db:
            self.require_project(db, project)
            items = rows(db, "SELECT t.*,count(r.id) records,sum(r.consumed_by IS NULL) pending "
                "FROM trace_turns t JOIN trace_records r ON r.turn_id=t.id WHERE t.project=? "
                "GROUP BY t.id ORDER BY t.id DESC LIMIT ? OFFSET ?", (project, limit + 1, offset))
            return page(items, offset, limit)

    def records(self, project, turn_id=None, offset=0, limit=50, record_id=None):
        with self.read() as db:
            self.require_project(db, project)
            items = rows(db, "SELECT r.*,s.path FROM trace_records r JOIN trace_sources s ON s.id=r.source "
                "WHERE r.project=?" + (" AND r.turn_id=?" if turn_id is not None else "") +
                (" AND r.id=?" if record_id is not None else "") +
                " ORDER BY r.source,r.offset LIMIT ? OFFSET ?",
                (project, *([turn_id] if turn_id is not None else []), *([record_id] if record_id is not None else []), limit + 1, offset))
            for item in items:
                original = item.pop("original")
                item["text"] = original.decode("utf-8", errors="replace")
                item["bytes_base64"] = base64.b64encode(original).decode()
            return page(items, offset, limit)
