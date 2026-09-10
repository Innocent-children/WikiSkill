from __future__ import annotations

import re
from pathlib import Path

import yaml

from .config import digest
from .skills import skill_text, snapshot
from .store import dumps
from .evolution import enrich_context


def terms(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]{3,}", text.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(phrase[i:i + 2] for i in range(len(phrase) - 1))
    return words - {"the", "and", "for", "with", "this", "from", "wiki", "skill", "使用", "进行", "可以"}


class SkillGeneration:
    """Select current Wiki bodies and coordinate new Skills or explicit merges."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.store

    def pages(self, db, project: str, names: list[str]) -> list[dict]:
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
            raise ValueError("请选择一篇或多篇不同的 Wiki")
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone():
            raise ValueError("项目不存在")
        pages = []
        for name in names:
            row = db.execute("SELECT w.name,w.body,w.digest,(SELECT max(c.id) FROM wiki_changes c "
                             "WHERE c.project=w.project AND c.name=w.name AND c.digest=w.digest) id "
                             "FROM wiki w WHERE project=? AND name=?", (project, name)).fetchone()
            if row is None or row["id"] is None:
                raise ValueError("选定的 Wiki 不存在，请刷新后重选")
            pages.append(dict(row))
        return pages

    def preview(self, project: str, pages: list[str]) -> dict:
        with self.store.connect() as db:
            selected = self.pages(db, project, pages)
            targets = [dict(row) for row in db.execute("SELECT * FROM skills ORDER BY path")]
        vocabulary = terms("\n".join(p["name"] + "\n" + p["body"] for p in selected))
        candidates, unavailable = [], []
        for target in targets:
            path = Path(target["path"])
            if not self.runtime.config.permits(path, bool(target["owned"])):
                continue
            try:
                self.runtime.skills.get(target["id"])
                bundle = snapshot(path)
                text = skill_text(bundle)
                if not text:
                    continue
                front = yaml.safe_load(text.split("---", 2)[1]) if text.startswith("---\n") else {}
                description = str((front or {}).get("description", "")) if isinstance(front, dict) else ""
            except (OSError, ValueError, IndexError, yaml.YAMLError) as exc:
                unavailable.append({"name": path.name, "reason": str(exc)})
                continue
            linked = self.store.rows('SELECT name,change_id FROM skill_wiki WHERE skill=? AND project=?', (target['id'], project))
            linked_names = {r['name'] for r in linked}
            relevant = [p['name'] for p in selected if p['name'] in linked_names]
            matched = sorted(vocabulary & terms(path.name + "\n" + text))
            candidates.append({"id": target["id"], "name": path.name, "description": description,
                               "skill_md": text, "digest": digest(bundle), "matched_terms": matched[:8],
                               "linked_pages": relevant, "score": len(matched) + 1000 * len(relevant), "projects": self.store.rows(
                                   "SELECT p.id,p.name FROM projects p JOIN project_skills s ON s.project=p.id "
                                   "WHERE s.skill=? ORDER BY p.name", (target["id"],))})
        candidates.sort(key=lambda c: (-c["score"], c["name"]))
        base = (selected[0]["name"] + ("-combined" if len(selected) > 1 else ""))[:56].rstrip("-")
        name, suffix = base, 2
        existing = {Path(t["path"]).name for t in targets}
        while name in existing or (self.runtime.config.root / "skills" / name).exists():
            name = f"{base}-{suffix}"
            suffix += 1
        return {"pages": selected, "suggested_name": name, "candidates": candidates, "unavailable": unavailable}

    def enqueue(self, project: str, pages: list[str], expected: dict, name: str | None = None,
                skill: str | None = None, skill_digest: str | None = None) -> dict:
        if skill is not None:
            if not isinstance(skill, str) or name is not None or not isinstance(skill_digest, str):
                raise ValueError("合并时请选择已有 Skill 并提供其内容摘要")
            target = self.runtime.skills.get(skill)
            before = snapshot(Path(target["path"]))
            if not skill_text(before) or digest(before) != skill_digest:
                raise ValueError("Skill 内容已变化，请重新查看合并候选")
        else:
            if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) or skill_digest is not None:
                raise ValueError("Skill 名称须为小写字母、数字或连字符，最长 64 个字符")
            path = self.runtime.config.root / "skills" / name
            skill = digest(str(path))[:24]
            target, before = {"path": str(path)}, {}
        with self.store.transaction() as db:
            selected = self.pages(db, project, pages)
            if expected != {p["name"]: p["digest"] for p in selected}:
                raise ValueError("Wiki 正文已变化，请重新打开生成面板")
            if db.execute("SELECT 1 FROM jobs WHERE skill=? AND state!='done' "
                          "AND id IN (SELECT job FROM job_execution)", (skill,)).fetchone():
                raise ValueError("此 Skill 有未完成的生成记录，请到执行记录处理")
            if name is not None:
                if Path(target["path"]).exists() or Path(target["path"]).is_symlink() or db.execute(
                        "SELECT 1 FROM skills WHERE id=? OR path=?", (skill, target["path"])).fetchone():
                    raise ValueError("同名 Skill 已存在，请修改名称或选择合并")
                db.execute("INSERT INTO skills VALUES(?,?,1)", (skill, target["path"]))
            context = {"project": project, "wiki": [{"name": p["name"], "body": p["body"]} for p in selected],
                       "changes": [{k: p[k] for k in ("id", "name", "body")} for p in selected],
                       "before_bundle": before, "skill_md": skill_text(before),
                       "suggested_name": Path(target["path"]).name}
            enrich_context(db, context, skill)
            db.execute("INSERT OR IGNORE INTO project_skills VALUES(?,?)", (project, skill))
            job = self.runtime._enqueue(db, project, "skill", skill, [p["id"] for p in selected])
            db.execute("UPDATE jobs SET context=? WHERE id=?", (dumps(context), job))
        return {"job_id": job, "skill_id": skill, "state": "queued"}
