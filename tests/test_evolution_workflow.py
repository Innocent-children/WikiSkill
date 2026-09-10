import base64
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.evolution import Evolution
from wikiskill.live.skill_generation import SkillGeneration
from wikiskill.live.session_import import SessionImport
from wikiskill.live.models import ApiSession
from wikiskill.live.codex import CodexSession
from wikiskill.live.web import create_app
from wikiskill.live.skills import snapshot, skill_text
from test_live_runtime import FakeSession, seed_record
from test_live_capture import transcript


class SessionWaitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.runtime = Runtime(Config(self.root / 'data', capture_mode='automatic',
                                      session_wait_minutes=15, auto_start=False), FakeSession)
        self.key = self.runtime.store.project(str(self.project))
        seed_record(self.runtime, self.key)
        self.source = self.root / 'session.jsonl'
        self.source.write_bytes(b'{}\n')
        self.now = 2_000_000_000
        self.clock = patch('wikiskill.live.runtime.time.time', return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        with self.runtime.store.transaction() as db:
            db.execute('UPDATE trace_sources SET path=?,position=?', (str(self.source), self.source.stat().st_size))
        self.age(900)
        FakeSession.starts, FakeSession.generations, FakeSession.reports = [], [], []
        FakeSession.fail_generate = FakeSession.fail_report = False
        FakeSession.no_change = True
        FakeSession.during_generate = None
        self.addCleanup(setattr, FakeSession, 'no_change', False)

    def age(self, seconds):
        with self.runtime.store.transaction() as db:
            db.execute('UPDATE trace_records SET created=?', (self.now - seconds,))
        os.utime(self.source, (self.now - seconds, self.now - seconds))

    def ready(self):
        with self.runtime.store.transaction() as db:
            return self.runtime._automatic_session_ready(db, ('identity', 'fixture'), self.now)

    def test_wait_uses_configured_minutes_and_inclusive_cutoff(self):
        for minutes in (1, 15, 60, 120):
            with self.subTest(minutes=minutes):
                self.runtime.config = replace(self.runtime.config, session_wait_minutes=minutes)
                self.age(minutes * 60 - 1)
                self.assertFalse(self.ready())
                self.age(minutes * 60)
                self.assertTrue(self.ready())
                self.age(minutes * 60 + 1)
                self.assertTrue(self.ready())

    def test_recent_ingestion_file_activity_and_unfinished_turns_delay_analysis(self):
        self.age(900)
        with self.runtime.store.transaction() as db:
            db.execute('UPDATE trace_records SET created=?,original=?',
                       (self.now, b'{"timestamp":"2000-01-01T00:00:00Z"}'))
        self.assertFalse(self.ready())
        self.age(900)
        os.utime(self.source, (self.now, self.now))
        self.assertFalse(self.ready())
        self.age(900)
        self.source.write_bytes(b'{}\n{}\n')
        os.utime(self.source, (self.now - 900, self.now - 900))
        self.assertFalse(self.ready())
        with self.runtime.store.transaction() as db:
            db.execute('UPDATE trace_sources SET position=?', (self.source.stat().st_size,))
            db.execute('UPDATE trace_turns SET ended=0')
        self.assertFalse(self.ready())
        with self.runtime.store.transaction() as db:
            db.execute('UPDATE trace_turns SET ended=1')
        self.assertTrue(self.ready())

    def test_new_record_delays_queued_job_and_explicit_run_bypasses_wait(self):
        job_id = self.runtime.schedule()[0]
        seed_record(self.runtime, self.key, 'new')
        self.assertFalse(self.runtime.run_job(job_id))
        self.assertEqual(self.runtime.store.job(job_id)['state'], 'queued')
        self.assertEqual(FakeSession.generations, [])
        self.runtime.run_manually(job_id)
        self.runtime.run_job(job_id)
        self.assertEqual(self.runtime.store.job(job_id)['state'], 'done')

    def test_saved_wait_is_loaded_by_worker_for_queue_and_execution(self):
        from wikiskill.live.settings import save_settings
        root = self.runtime.config.root
        save_settings(root, {'session_wait_minutes': 120})
        self.runtime.worker(once=True)
        self.assertEqual(self.runtime.store.rows('SELECT id FROM jobs'), [])
        save_settings(root, {'session_wait_minutes': 15})
        job_id = self.runtime.schedule()[0]
        save_settings(root, {'session_wait_minutes': 120})
        self.runtime.worker(once=True)
        self.assertEqual(self.runtime.store.job(job_id)['state'], 'queued')
        save_settings(root, {'session_wait_minutes': 15})
        self.runtime.worker(once=True)
        self.assertEqual(self.runtime.store.job(job_id)['state'], 'done')

    def test_analysis_interval_still_limits_new_batches(self):
        self.runtime.schedule()
        with self.runtime.store.transaction() as db:
            db.execute("UPDATE jobs SET state='done'")
            db.execute("UPDATE trace_records SET consumed_by='previous'")
        seed_record(self.runtime, self.key, 'next')
        self.age(900)
        self.assertEqual(self.runtime.schedule(), [])
        with self.runtime.store.transaction() as db:
            db.execute('UPDATE automatic_analysis SET last_run=?', (self.now - 3600,))
        self.assertEqual(len(self.runtime.schedule()), 1)


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.config = Config(self.root / 'data', codex_home=str(self.root / 'codex'), auto_start=False)
        self.runtime = Runtime(self.config, FakeSession)
        self.key = self.runtime.store.project(str(self.project))
        FakeSession.starts, FakeSession.generations, FakeSession.reports = [], [], []
        FakeSession.fail_generate = FakeSession.fail_report = FakeSession.no_change = False
        FakeSession.during_generate = None

    def test_manual_selection_only_imports_selected_session_and_never_schedules(self):
        directory = Path(self.config.codex_home) / 'sessions'
        directory.mkdir(parents=True)
        for name in ('selected', 'unrelated'):
            (directory / f'{name}.jsonl').write_bytes(transcript(self.project, name))
        picker = SessionImport(self.config)
        before = self.runtime.store.rows('SELECT * FROM trace_records')
        items = picker.catalog()['items']
        self.assertEqual(len(items), 2)
        self.assertEqual(self.runtime.store.rows('SELECT * FROM trace_records'), before)
        key = next(s['id'] for s in items if s['session'] == 'selected')
        result = picker.import_selected([key], analyze=True)
        self.assertEqual(len(result['jobs']), 1)
        self.assertEqual({r['session'] for r in self.runtime.store.rows('SELECT * FROM trace_records')}, {'selected'})
        self.assertEqual(self.runtime.schedule(), [])
        self.assertEqual(self.runtime.drain(), 1)
        self.assertEqual([j['stage'] for j in self.runtime.store.rows('SELECT stage FROM jobs')], ['raw'])
        self.assertEqual(picker.import_selected([key], analyze=True)['jobs'], [])
        self.assertEqual(FakeSession.generations, [(FakeSession.starts[0], 'raw')])

    def test_automatic_interval_both_stages_and_no_duplicate_or_failure_storm(self):
        auto = Runtime(replace(self.config, capture_mode='automatic', analysis_interval_minutes=10), FakeSession)
        seed_record(auto, self.key, 'first')
        self.assertEqual(auto.drain(), 2)
        skill = auto.store.rows('SELECT * FROM skill_wiki')[0]['skill']
        self.assertIn('build', auto.skills.get(skill)['path'])
        self.assertTrue((Path(auto.skills.get(skill)['path']) / 'PURPOSE.md').exists())
        seed_record(auto, self.key, 'second')
        self.assertEqual(auto.drain(), 0)
        with auto.store.transaction() as db:
            db.execute('UPDATE automatic_analysis SET last_run=0')
        FakeSession.fail_generate = True
        self.assertEqual(auto.drain(), 1)
        self.assertEqual(auto.drain(), 0)
        self.assertEqual(len(auto.store.rows("SELECT id FROM jobs WHERE state='failed'")), 1)
        self.assertEqual(self.runtime.schedule(), [])

    def test_corrected_current_wiki_feedback_and_sources_survive_manual_edit(self):
        seed_record(self.runtime, self.key)
        self.runtime.enqueue(self.key, 'raw')
        self.runtime.drain()
        self.runtime.put_wiki(str(self.project), [{'name': 'build', 'body': 'Use B; A was wrong.'}])
        flow = SkillGeneration(self.runtime)
        preview = flow.preview(self.key, ['build'])
        result = flow.enqueue(self.key, ['build'], {'build': preview['pages'][0]['digest']}, name='build')
        self.runtime.drain()
        job = self.runtime.store.job(result['job_id'])
        self.assertEqual(job['context']['wiki'][0]['body'], 'Use B; A was wrong.')
        self.assertIn('traces/1', job['context']['documents'])
        version = self.runtime.skills.history(result['skill_id'])[0]['id']
        evolution = Evolution(self.runtime.store)
        evolution.feedback(result['skill_id'], version, 'problem', 'This command fails in production')
        self.runtime.put_wiki(str(self.project), [{'name': 'build', 'body': 'Use C for production.'}])
        self.assertTrue(evolution.detail(result['skill_id'])['sources'][0]['needs_update'])
        preview = flow.preview(self.key, ['build'])
        self.assertEqual(preview['candidates'][0]['linked_pages'], ['build'])
        next_job = flow.enqueue(self.key, ['build'], {'build': preview['pages'][0]['digest']},
                               skill=result['skill_id'], skill_digest=preview['candidates'][0]['digest'])
        context = self.runtime.store.job(next_job['job_id'])['context']
        self.assertEqual(context['feedback'][0]['body'], 'This command fails in production')
        self.assertIn('history/' + version, context['documents'])
        self.assertEqual(context['wiki'], [{'name': 'build', 'body': 'Use C for production.'}])
        with TestClient(create_app(self.config.root)) as client:
            response = client.get(f'/api/projects/{self.key}/wiki/build/sources', headers={'host':'127.0.0.1'})
            self.assertEqual(response.json()['items'][0]['id'], 1)

    def test_no_action_is_valid_for_new_skill_and_not_repeated_automatically(self):
        auto = Runtime(replace(self.config, capture_mode='automatic'), FakeSession)
        auto.put_wiki(str(self.project), [{'name': 'noise', 'body': 'Nothing reusable'}])
        FakeSession.no_change = True
        self.assertEqual(auto.drain(), 1)
        self.assertEqual(auto.drain(), 0)
        self.assertEqual(auto.store.rows('SELECT * FROM versions'), [])




    def test_rollback_records_reason_without_changing_wiki(self):
        self.runtime.put_wiki(str(self.project), [{'name':'build','body':'Use A'}])
        flow = SkillGeneration(self.runtime)
        preview = flow.preview(self.key, ['build'])
        result = flow.enqueue(self.key, ['build'], {'build':preview['pages'][0]['digest']}, name='build')
        self.runtime.drain()
        version = self.runtime.skills.history(result['skill_id'])[0]['id']
        wiki = self.runtime.store.rows('SELECT * FROM wiki')
        self.runtime.skills.rollback(result['skill_id'], version, reason='Incorrect steps')
        self.assertEqual(self.runtime.store.rows('SELECT * FROM wiki'), wiki)
        self.assertIn('Incorrect steps', Evolution(self.runtime.store).detail(result['skill_id'])['feedback'][0]['body'])

    def test_switch_to_manual_pauses_queued_automatic_jobs_until_explicit_run(self):
        auto = Runtime(replace(self.config, capture_mode='automatic'), FakeSession)
        seed_record(auto, self.key)
        job = auto.schedule()[0]
        self.assertEqual(self.runtime.drain(), 0)
        self.assertEqual(self.runtime.store.job(job)['state'], 'queued')
        self.runtime.run_manually(job)
        self.assertEqual(self.runtime.drain(), 1)
        self.assertEqual(self.runtime.store.job(job)['state'], 'done')
        self.assertEqual(self.runtime.store.rows("SELECT * FROM jobs WHERE stage='skill'"), [])

    def test_live_mode_change_during_generation_stops_further_automatic_work(self):
        from wikiskill.live.settings import save_settings
        save_settings(self.config.root, {'capture_mode':'automatic'})
        auto = Runtime(Config.load(self.config.root), FakeSession)
        seed_record(auto, self.key)
        def switch():
            FakeSession.during_generate = None
            save_settings(self.config.root, {'capture_mode':'manual'})
        FakeSession.during_generate = switch
        self.assertEqual(auto.drain(), 1)
        self.assertEqual(auto.store.rows("SELECT * FROM jobs WHERE stage='skill'"), [])

    def test_automatic_skill_name_collisions_are_isolated_and_sources_stay_stable(self):
        auto = Runtime(replace(self.config, capture_mode='automatic'), FakeSession)
        names = ['a' * 40 + '-first', 'a' * 40 + '-second']
        auto.put_wiki(str(self.project), [{'name':name,'body':'Useful steps'} for name in names])
        created = auto.schedule()
        self.assertEqual(len(created), 2)
        self.assertEqual(len({auto.store.job(j)['skill'] for j in created}), 2)
        self.assertEqual(auto.schedule(), [])

    def test_automatic_generation_uses_latest_body_and_displays_actual_input(self):
        from wikiskill.live.views import ReadView
        auto = Runtime(replace(self.config, capture_mode='automatic'), FakeSession)
        auto.put_wiki(str(self.project), [{'name':'build','body':'Old command A'}])
        job_id = auto.schedule()[0]
        auto.put_wiki(str(self.project), [{'name':'build','body':'Corrected command B'}])
        auto.run_job(job_id)
        job = auto.store.job(job_id)
        self.assertEqual(job['state'], 'done')
        self.assertEqual(job['context']['wiki'], [{'name':'build','body':'Corrected command B'}])
        self.assertEqual(ReadView(self.config.root).inputs(job_id)['items'][0]['body'], 'Corrected command B')
        self.assertEqual(auto.schedule(), [])

    def test_exact_patches_preserve_unrelated_content_and_reject_ambiguous_targets(self):
        from wikiskill.live.generation import apply_edits
        self.assertEqual(apply_edits('Keep scope.\nUse A.\nKeep tests.', [{'op':'replace','target':'Use A.','content':'Use B.'}]), 'Keep scope.\nUse B.\nKeep tests.')
        for body, target in [('A A', 'A'), ('B', 'A')]:
            with self.assertRaisesRegex(ValueError, '精确出现一次'):
                apply_edits(body, [{'op':'replace','target':target,'content':'C'}])


    def test_reading_unknown_evolution_does_not_initialize_data_directory(self):
        root = self.root / 'absent'
        with TestClient(create_app(root), base_url='http://127.0.0.1') as client:
            self.assertEqual(client.get('/api/skills/missing/evolution').status_code, 404)
        self.assertFalse(root.exists())



    def test_automatic_selection_keeps_all_large_ended_turns(self):
        seed_record(self.runtime, self.key, 'first')
        seed_record(self.runtime, self.key, 'second')
        original = b'x' * 300000
        with self.runtime.store.transaction() as db:
            db.execute('UPDATE trace_records SET original=?', (original,))
            self.assertEqual(self.runtime._raw_inputs(db, self.key, automatic=True), [1,2])
        self.assertEqual(self.runtime.store.rows('SELECT original FROM trace_records')[0]['original'], original)
