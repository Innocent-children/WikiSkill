import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.settings import save_settings


class RecordingSession:
    contexts = []
    fail = None

    def __init__(self, config):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start(self, title):
        return None

    def resume(self, thread):
        pass

    def generate(self, stage, context):
        type(self).contexts.append(context)
        ids = [r['id'] for r in context['records']]
        if self.fail in ids:
            raise RuntimeError('model rejected input')
        old = next((p['body'] for p in context['wiki'] if p['name'] == 'lesson'), '')
        return {'summary': 'processed', 'pages': [{'name': 'lesson',
                'body': old + '\n' + ','.join(map(str, ids)), 'source_ids': ids}]}

    def report(self, report):
        return report['summary']


class TurnBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.runtime = Runtime(Config(self.root / 'data', auto_start=False, max_turns_per_batch=2), RecordingSession)
        self.key = self.runtime.store.project(str(self.project))
        RecordingSession.contexts, RecordingSession.fail = [], None

    def turn(self, name, session='a', ended=True, count=2, body=None):
        source_path = self.root / (session + '.jsonl')
        source_path.write_text('{}\n')
        os.utime(source_path, (time.time() - 7200,) * 2)
        with self.runtime.store.transaction() as db:
            db.execute('INSERT OR IGNORE INTO trace_sources(identity,path,session,project,cwd,position,fingerprint,prefix_length) '
                       'VALUES(?,?,?,?,?,3,?,0)', (session, str(source_path), session, self.key, str(self.project), ''))
            source = db.execute('SELECT id FROM trace_sources WHERE identity=?', (session,)).fetchone()[0]
            db.execute('INSERT INTO trace_turns(source,turn_key,project,session,ended) VALUES(?,?,?,?,?)',
                       (source, name, self.key, session, int(ended)))
            turn_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            offset = db.execute('SELECT coalesce(max(offset),0) FROM trace_records WHERE source=?', (source,)).fetchone()[0]
            ids = []
            for index in range(count):
                original = body if body is not None else (json.dumps({'turn': name, 'index': index}) + '\n').encode()
                result = db.execute('INSERT INTO trace_records(source,offset,original,project,session,turn_id,event_type,created) '
                                    'VALUES(?,?,?,?,?,?,?,?)',
                                    (source, offset + index + 1, original, self.key, session, turn_id, 'fixture', time.time() - 7200))
                ids.append(result.lastrowid)
        return ids

    def enqueue(self, inputs=None):
        return self.runtime.enqueue(self.key, 'raw', inputs=inputs)['job_ids']

    def input_ids(self, job):
        return self.runtime.store.job(job)['inputs']

    def test_complete_turns_split_two_two_one_and_keep_original_bytes(self):
        turns = [self.turn(str(i)) for i in range(5)]
        before = self.runtime.store.rows('SELECT id,original FROM trace_records')
        jobs = self.enqueue()
        self.assertEqual([self.input_ids(j) for j in jobs], [sum(turns[:2], []), sum(turns[2:4], []), turns[4]])
        self.assertEqual(self.runtime.drain(), 3)
        self.assertEqual(self.runtime.store.rows('SELECT id,original FROM trace_records'), before)
        sent = [r for c in RecordingSession.contexts for r in c['records']]
        self.assertEqual([r['original'] for r in sent], [r['original'].decode() for r in before])
        self.assertEqual(len({r['id'] for r in sent}), len(before))
        self.assertEqual(self.runtime.store.rows('SELECT id FROM trace_records WHERE consumed_by IS NULL'), [])

    def test_partial_selection_expands_whole_turn_and_waits_for_unfinished(self):
        first = self.turn('first')
        unfinished = self.turn('unfinished', ended=False)
        jobs = self.enqueue([first[-1], unfinished[0]])
        self.assertEqual(self.input_ids(jobs[0]), first)
        self.runtime.drain()
        with self.assertRaisesRegex(ValueError, '完整轮次'):
            self.enqueue(unfinished)
        self.assertEqual([r['id'] for r in self.runtime.store.rows('SELECT id FROM trace_records WHERE consumed_by IS NULL')], unfinished)

    def test_unassigned_prefix_is_included_and_unassigned_only_forms_batch(self):
        prefix = self.turn('unassigned', ended=False)
        first = self.turn('first')
        orphan = self.turn('unassigned', session='b', ended=False)
        jobs = self.enqueue()
        self.assertEqual([self.input_ids(j) for j in jobs], [prefix + first, orphan])
        self.assertEqual(self.runtime.drain(), 2)

    def test_each_batch_reads_latest_wiki_and_preserves_other_pages(self):
        self.runtime.config = replace(self.runtime.config, max_turns_per_batch=1)
        self.runtime.put_wiki(str(self.project), [{'name': 'untouched', 'body': 'Keep this procedure'}])
        self.turn('first')
        self.turn('second')
        self.enqueue()
        self.runtime.drain()
        first, second = RecordingSession.contexts
        self.assertEqual([p['name'] for p in first['wiki']], ['untouched'])
        self.assertEqual({p['name'] for p in second['wiki']}, {'lesson', 'untouched'})
        self.assertEqual(self.runtime.store.rows("SELECT body FROM wiki WHERE name='untouched'")[0]['body'], 'Keep this procedure')
        sources = self.runtime.store.rows("SELECT record_id FROM wiki_sources WHERE change_id=(SELECT max(id) FROM wiki_changes WHERE name='lesson')")
        self.assertEqual({r['record_id'] for r in sources}, {r['id'] for c in RecordingSession.contexts for r in c['records']})

    def test_failure_blocks_same_session_including_manual_run_but_other_session_continues(self):
        self.runtime.config = replace(self.runtime.config, max_turns_per_batch=1)
        first = self.turn('first')
        self.turn('second')
        self.turn('other', session='b')
        jobs = self.enqueue()
        RecordingSession.fail = first[0]
        self.assertEqual(self.runtime.drain(), 2)
        self.assertEqual([self.runtime.store.job(j)['state'] for j in jobs], ['failed', 'queued', 'done'])
        self.assertFalse(self.runtime.run_job(jobs[1]))
        with self.assertRaisesRegex(ValueError, '前序批次'):
            self.runtime.run_manually(jobs[1])
        RecordingSession.fail = None
        self.runtime.retry(jobs[0], regenerate=True)
        self.assertEqual(self.runtime.drain(), 2)
        self.assertEqual([self.runtime.store.job(j)['state'] for j in jobs], ['done'] * 3)

    def test_regenerate_splits_failed_batch_before_existing_later_batch(self):
        turns = [self.turn(str(i)) for i in range(3)]
        jobs = self.enqueue()
        RecordingSession.fail = turns[0][0]
        self.runtime.drain()
        self.runtime.config = replace(self.runtime.config, max_turns_per_batch=1)
        split = self.runtime.retry(jobs[0], regenerate=True)['job_ids']
        self.assertEqual(len(split), 2)
        self.assertEqual([self.input_ids(j) for j in split], turns[:2])
        self.assertFalse(self.runtime.run_job(jobs[1]))
        RecordingSession.contexts, RecordingSession.fail = [], None
        self.assertEqual(self.runtime.drain(), 3)
        self.assertEqual([[r['id'] for r in c['records']] for c in RecordingSession.contexts], turns)

    def test_queued_inputs_stay_fixed_when_settings_or_source_change(self):
        self.runtime.config = replace(self.runtime.config, capture_mode='automatic')
        first = self.turn('first')
        job = self.runtime.schedule()[0]
        second = self.turn('second')
        save_settings(self.runtime.config.root, {'max_turns_per_batch': 1})
        self.runtime.config = Config.load(self.runtime.config.root)
        self.runtime.run_job(job)
        self.assertEqual(self.input_ids(job), first)
        self.assertEqual([r['id'] for r in RecordingSession.contexts[0]['records']], first)
        self.assertEqual([r['id'] for r in self.runtime.store.rows('SELECT id FROM trace_records WHERE consumed_by IS NULL')], second)

    def test_automatic_partition_and_unassigned_records(self):
        self.runtime.config = replace(self.runtime.config, capture_mode='automatic')
        prefix = self.turn('unassigned', ended=False)
        turns = [self.turn(str(i)) for i in range(3)]
        jobs = self.runtime.schedule()
        self.assertEqual([self.input_ids(j) for j in jobs], [prefix + sum(turns[:2], []), turns[2]])

    def test_failure_does_not_consume_or_publish_and_large_single_turn_is_retained(self):
        original = ('large output\n' * 100000).encode()
        ids = self.turn('large', count=1, body=original)
        job = self.enqueue()[0]
        RecordingSession.fail = ids[0]
        self.runtime.drain()
        self.assertEqual(self.runtime.store.job(job)['state'], 'failed')
        self.assertEqual(self.runtime.store.rows('SELECT original,consumed_by FROM trace_records'),
                         [{'original': original, 'consumed_by': None}])
        self.assertEqual(self.runtime.store.rows('SELECT * FROM wiki'), [])
        self.assertEqual(RecordingSession.contexts[0]['records'][0]['original'].encode(), original)

    def test_failed_wiki_transaction_keeps_inputs_pending(self):
        self.turn('first')
        job_id = self.enqueue()[0]
        job = self.runtime.store.job(job_id)
        context = self.runtime._context(job)
        self.runtime.put_wiki(str(self.project), [{'name': 'lesson', 'body': 'Concurrent edit'}])
        job.update(context=context, result={'summary': 'change', 'pages': [
            {'name': 'lesson', 'body': 'Model change', 'source_ids': job['inputs']}]})
        with self.assertRaisesRegex(ValueError, 'changed during generation'):
            self.runtime._apply(job)
        self.assertEqual(len(self.runtime.store.rows('SELECT id FROM trace_records WHERE consumed_by IS NULL')), 2)
        self.assertEqual(self.runtime.store.rows("SELECT body FROM wiki WHERE name='lesson'")[0]['body'], 'Concurrent edit')
