"""On-demand local session discovery and explicitly selected transcript imports."""
from pathlib import Path
import time

from .capture import Collector, parse_record
from .config import digest
from .skills import file_lock


class SessionImport:
    def __init__(self, config):
        self.config = config

    def catalog(self, q='', offset=0, limit=20):
        candidates = self._files()
        items = [r for r in candidates.values() if q.lower() in (r['title'] + r['cwd'] + r['session']).lower()]
        items.sort(key=lambda r: r['modified'], reverse=True)
        return {'items': [{k: v for k, v in r.items() if k != 'path'} for r in items[offset:offset + limit]],
                'next_offset': offset + limit if offset + limit < len(items) else None}

    def _files(self):
        result = {}
        for name in ('sessions', 'archived_sessions'):
            directory = Path(self.config.codex_home) / name
            for path in directory.rglob('*.jsonl') if directory.exists() else []:
                try:
                    if not path.resolve().is_relative_to(directory.resolve()):
                        continue
                    with path.open('rb') as stream:
                        header, _ = parse_record(stream.readline(65536))
                        meta = header.get('payload', {})
                        if header.get('type') != 'session_meta' or not isinstance(meta, dict):
                            continue
                        session, cwd = meta.get('id', ''), meta.get('cwd', '')
                        if not isinstance(session, str) or not isinstance(cwd, str) or not cwd or not session:
                            continue
                        if Path(cwd).expanduser().resolve() == self.config.root:
                            continue
                        title = ''
                        for _ in range(24):
                            line = stream.readline(65536)
                            if not line:
                                break
                            item, _ = parse_record(line)
                            payload = item.get('payload', {})
                            if isinstance(payload, dict) and payload.get('type') == 'user_message':
                                title = str(payload.get('message', ''))[:240]
                                break
                    key = digest(session or str(path))[:24]
                    stat = path.stat()
                    item = {'id': key, 'session': session, 'cwd': cwd, 'title': title or Path(cwd).name,
                            'modified': stat.st_mtime, 'bytes': stat.st_size, 'path': path}
                    if key not in result or stat.st_mtime > result[key]['modified']:
                        result[key] = item
                except (OSError, ValueError):
                    continue
        return result

    def import_selected(self, ids, analyze=False):
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or len(ids) > 100:
            raise ValueError('请选择 1 至 100 个不同会话')
        if type(analyze) is not bool:
            raise ValueError('analyze 必须为布尔值')
        files = self._files()
        if any(i not in files for i in ids):
            raise ValueError('会话文件已变化，请刷新列表后重选')
        collector = Collector(self.config)
        count = 0
        with file_lock(self.config.root / 'locks/capture.lock'):
            for key in ids:
                count += collector._file(files[key]['path'], {'enabled': time.time(), 'history_requested': 1})
        jobs = []
        if analyze:
            from .runtime import Runtime
            runtime = Runtime(self.config)
            sessions = [files[i]['session'] for i in ids]
            from .store import dumps
            with runtime.store.transaction() as db:
                rows = db.execute('SELECT project,id FROM trace_records WHERE session IN (SELECT value FROM json_each(?)) AND consumed_by IS NULL ORDER BY id', (dumps(sessions),)).fetchall()
                groups = {}
                for row in rows:
                    if row['project']:
                        groups.setdefault(row['project'], []).append(row['id'])
                for project, records in groups.items():
                    jobs.extend({'job_id': job, 'state': 'queued'}
                                for job in runtime._enqueue_raw(db, project, records))
            if jobs:
                runtime.wake()
        return {'records': count, 'jobs': jobs}
