"""Assemble selected knowledge, trace provenance and human feedback for skill evolution."""
from __future__ import annotations

import base64
import json
import time

from .store import dumps, Store


def source_ids(db, project, name, change_id=None):
    change = db.execute("SELECT id,job_id FROM wiki_changes WHERE project=? AND name=? " +
                        ("AND id=? " if change_id is not None else "") + "ORDER BY id DESC LIMIT 1",
                        (project, name, *([change_id] if change_id is not None else []))).fetchone()
    if not change:
        return []
    ids = [r[0] for r in db.execute("SELECT record_id FROM wiki_sources WHERE change_id=?", (change['id'],))]
    # Existing generated pages can be traced through their saved batch inputs.
    job = db.execute("SELECT inputs FROM jobs WHERE id=? AND stage='raw'", (change['job_id'],)).fetchone()
    if not ids and job:
        ids = json.loads(job['inputs'])
    return ids


def record_sources(db, project, name, change_id, ids):
    previous = db.execute("SELECT id FROM wiki_changes WHERE project=? AND name=? AND id<? ORDER BY id DESC LIMIT 1",
                          (project, name, change_id)).fetchone()
    inherited = source_ids(db, project, name, previous[0]) if previous else []
    db.executemany("INSERT OR IGNORE INTO wiki_sources VALUES(?,?)", [(change_id, i) for i in set(ids + inherited)])


def enrich_context(db, context, skill=None):
    """Freeze an allowlisted library; model requests can only read these documents."""
    documents = {}
    project = context['project']
    context['wiki_documents'] = {r['kind']:r['body'] for r in db.execute('SELECT kind,body FROM wiki_documents WHERE project=?', (project,))}
    if 'records' in context:
        for page in context['wiki']:
            documents[f"wiki/{page['name']}"] = page['body']
        log = [dict(r) for r in db.execute("SELECT id,report FROM jobs WHERE project=? AND stage='raw' "
                                         "AND state='done' ORDER BY created DESC", (project,))]
        for r in log:
            documents[f"history/wiki/{r['id']}"] = dumps({'job': r['id'], 'summary': json.loads(r['report'] or '{}').get('summary', '')})
    else:
        ids = set()
        for page in context.get('changes', []):
            ids.update(source_ids(db, project, page['name'], page['id']))
        if ids:
            groups = {}
            for r in db.execute("SELECT id,original,turn_id FROM trace_records WHERE project=? AND id IN (SELECT value FROM json_each(?)) ORDER BY source,offset", (project, dumps(sorted(ids)))):
                groups.setdefault(str(r['turn_id'] or r['id']), []).append(r['original'].decode('utf-8', errors='replace'))
            for key, records in groups.items():
                documents[f'traces/{key}'] = ''.join(records)
        for name, entry in context.get('before_bundle', {}).items():
            if entry.get('type') == 'file':
                try:
                    body = base64.b64decode(entry['data'], validate=True).decode('utf-8')
                except (ValueError, UnicodeError):
                    continue
                documents[f"skills/{context['suggested_name']}/{name}"] = body
        if skill:
            versions = [dict(r) for r in db.execute("SELECT id,job_id,created,diff,after_bundle FROM versions WHERE skill=? ORDER BY created DESC", (skill,))]
            context['skill_history'] = [{'version': r['id'], 'job': r['job_id'], 'created': r['created'],
                                        'document': f"history/{r['id']}"} for r in versions]
            for r in versions:
                from .skills import skill_text
                documents[f"history/{r['id']}"] = dumps({'version':r['id'], 'diff':r['diff'], 'candidate_skill':skill_text(json.loads(r['after_bundle']))})
            context['feedback'] = [dict(r) for r in db.execute("SELECT version_id,kind,body,created FROM skill_feedback WHERE skill=? ORDER BY id", (skill,))]
    context['documents'] = documents
    return context


def purpose_bundle(bundle, job, summary, proposal_purpose=None):
    """Package source Wiki versions and the actual modification reason alongside SKILL.md."""
    entry = bundle.get('PURPOSE.md', {})
    previous = base64.b64decode(entry.get('data', '')).decode('utf-8', errors='replace')
    if not previous:
        previous = '# Skill 来源与演化记录\n\n记录生成依据；内容变化不等于已经验证有效。\n'
    text = previous + f"\n## {job['id']}\n\n{summary}\n\n"
    if proposal_purpose:
        text += proposal_purpose + "\n\n"
    for p in job['context'].get('changes', []):
        text += f"- Wiki：{job['project']}/{p['name']}，版本 {p['id']}\n"
    result = dict(bundle)
    result['PURPOSE.md'] = {'type': 'file', 'mode': entry.get('mode', 0o644),
                            'data': base64.b64encode(text.encode()).decode()}
    return result


class Evolution:
    def __init__(self, store):
        self.store = store

    def feedback(self, skill, version_id, kind, body):
        if not isinstance(version_id, str) or not isinstance(kind, str) or kind not in {'useful', 'problem', 'rejected', 'note', 'rollback'} or not isinstance(body, str) or not body.strip() or len(body) > 8000:
            raise ValueError('请选择反馈类型并填写具体原因（最多 8000 字）')
        with self.store.transaction() as db:
            version = db.execute("SELECT id FROM versions WHERE skill=? AND id=? AND state='applied'", (skill, version_id)).fetchone()
            if not version:
                raise ValueError('请选择此 Skill 已发布的版本')
            key = db.execute("INSERT INTO skill_feedback(skill,version_id,kind,body,created) VALUES(?,?,?,?,?)",
                             (skill, version_id, kind, body.strip(), time.time())).lastrowid
            Store.event(db, 'skill.feedback', skill=skill, version_id=version_id)
        return {'id': key}

    def detail(self, skill):
        return self.read_detail(self.store.config.root, skill)

    @staticmethod
    def read_detail(root, skill):
        from .views import ReadView, rows
        view = ReadView(root)
        with view.read() as db:
            view.require_skill(db, skill)
            sources = rows(db, 'SELECT s.*,w.body,w.digest,(SELECT max(id) FROM wiki_changes WHERE project=s.project AND name=s.name) current_version '
                           'FROM skill_wiki s LEFT JOIN wiki w ON w.project=s.project AND w.name=s.name WHERE skill=?', (skill,))
            for source in sources:
                previous = db.execute('SELECT body FROM wiki_changes WHERE id=?', (source['change_id'],)).fetchone()
                source['needs_update'] = not previous or previous['body'] != source['body']
            return {'sources': sources, 'feedback': rows(db, 'SELECT * FROM skill_feedback WHERE skill=? ORDER BY id DESC', (skill,))}
