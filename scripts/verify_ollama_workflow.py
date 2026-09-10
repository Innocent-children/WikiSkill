"""Explicit live integration check using synthetic traces in an isolated directory.

Run: uv run python scripts/verify_ollama_workflow.py
This calls the named local model. It does not modify the user's WikiSkill data or install Skills.
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.skill_generation import SkillGeneration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3.5:9b')
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix='wikiskill-ollama-verified-'))
    project = root / 'project'
    project.mkdir()
    config = Config(root / 'data', executor='ollama', ollama_model=args.model, capture_mode='automatic',
                    codex_home=str(root / 'codex'), auto_start=False)
    runtime = Runtime(config)
    key = runtime.store.project(str(project))
    print(f'Isolated data: {root}', flush=True)
    with runtime.store.transaction() as db:
        db.execute("INSERT INTO trace_sources(id,identity,path,fingerprint,prefix_length) VALUES(1,'fixture','synthetic','',0)")
        for turn in range(1, 5):
            db.execute('INSERT INTO trace_turns(id,source,turn_key,project,ended) VALUES(?,1,?,?,1)', (turn, str(turn), key))
            records = [
                {'type':'user','content':f'测试记录 {turn}：在项目根目录运行 Python 项目测试。'},
                {'type':'tool_call','command':'pytest tests/test_math.py','cwd':'project'},
                {'type':'tool_result','exit_code':127,'output':'pytest: command not found'},
                {'type':'tool_call','command':'.venv/bin/python -m pytest tests/test_math.py','cwd':'project'},
                {'type':'tool_result','exit_code':0,'output':'3 passed'},
                {'type':'user','content':'已确认项目依赖在 .venv，后续在项目根目录直接使用 .venv/bin/python -m pytest，不要重复尝试缺失的全局 pytest。'},
            ]
            for index, record in enumerate(records):
                db.execute('INSERT INTO trace_records(source,offset,original,project,turn_id,event_type,created) VALUES(1,?,?,?,?,?,?)',
                           (turn * 100 + index, (json.dumps(record, ensure_ascii=False) + '\n').encode(), key, turn, record['type'], time.time()))
    runtime.drain()
    jobs = runtime.store.rows('SELECT id,stage,state,error FROM jobs ORDER BY created')
    print(json.dumps(jobs, ensure_ascii=False, indent=2), flush=True)
    assert jobs and all(j['state'] == 'done' for j in jobs), 'See retained model responses and failed jobs'
    published = runtime.store.rows("SELECT s.path FROM skills s WHERE EXISTS(SELECT 1 FROM versions v WHERE v.skill=s.id AND v.state='applied')")
    assert published, 'The live model did not produce a usable Skill'
    for skill in published:
        path = Path(skill['path'])
        assert (path / 'SKILL.md').read_text().startswith('---\n')
        assert (path / 'PURPOSE.md').is_file()
    # Manual no_action uses source text that contains no procedural knowledge.
    manual = Runtime(Config(root / 'manual', executor='ollama', ollama_model=args.model, auto_start=False))
    p = manual.put_wiki(str(project), [{'name':'acknowledgement','body':'这段内容只有一句“谢谢”，没有任务结果、操作步骤、问题、解决办法或可复用知识。'}])['project_id']
    flow = SkillGeneration(manual)
    preview = flow.preview(p, ['acknowledgement'])
    result = flow.enqueue(p, ['acknowledgement'], {'acknowledgement':preview['pages'][0]['digest']}, name='no-action-check')
    manual.run_job(result['job_id'])
    job = manual.store.job(result['job_id'])
    print('No action:', job['state'], job['error'], job['result'], flush=True)
    assert job['state'] == 'done' and job['result']['skill_md'] is None
    assert not manual.store.rows('SELECT * FROM versions')
    print('PASS: automatic Raw -> Wiki -> Skill and manual no_action', flush=True)


if __name__ == '__main__':
    main()
