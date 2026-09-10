import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from wikiskill.live.config import Config
from wikiskill.live.runtime import Runtime
from wikiskill.live.generation import Proposer, WIKI_SCHEMA, maintain, maintainer_prompt
from wikiskill.live.models import ApiSession
from wikiskill.live.ollama import OllamaModel
from wikiskill.live.codex import CodexSession
from wikiskill.live.web import create_app
from wikiskill.model import ChatResponse
from test_live_runtime import seed_record


def context():
    return {'project':'p','wiki':[{'name':'build','body':'Use local Python'}], 'records':[{'id':1,'original':'trace'}],
            'suggested_name':'build','skill_md':'','documents':{'traces/1':'trace'}, 'resource_inventory':[]}


def proposal():
    return {'action':'create','name':'build','skill_md':'---\nname: build\ndescription: Build project\n---\nUse local Python', 'purpose_md':'# Origin\nBuild Wiki'}


def call(name,args,key='tool'):
    return {'id':key,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


class PaperProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config=Config(self.root/'data',executor='ollama',ollama_model='qwen3.5:9b',auto_start=False)

    def test_native_ollama_format_thinking_and_object_tool_arguments(self):
        responses=[{'done':True,'done_reason':'stop','message':{'role':'assistant','content':'{}','thinking':'separate'}},
                   {'done':True,'done_reason':'stop','message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'read_file','arguments':{'path':'wiki/index.md'}}}]}},
                   {'done':True,'done_reason':'stop','message':{'role':'assistant','content':'done'}}]
        model=OllamaModel(self.config);record=Mock();model.on_response=record
        with patch('wikiskill.live.ollama.request_json',side_effect=responses) as request:
            model.complete([{'role':'user','content':'maintain'}],[],schema=WIKI_SCHEMA)
            first=model.complete([{'role':'user','content':'propose'}],[{'type':'function','function':{'name':'read_file'}}])
            model.complete([{'role':'user','content':'propose'},first.message,{'role':'tool','tool_call_id':first.message['tool_calls'][0]['id'],'content':'index'}],[])
        first_payload=request.call_args_list[0].kwargs['payload']
        self.assertEqual(request.call_args_list[0].args[0],'http://127.0.0.1:11434/api/chat')
        self.assertIs(first_payload['format'],WIKI_SCHEMA)
        self.assertFalse(first_payload['think']);self.assertFalse(first_payload['stream'])
        self.assertEqual(first_payload['options'],{'temperature':0})
        self.assertEqual(request.call_args_list[0].kwargs['retries'],0)
        self.assertNotIn('format',request.call_args_list[1].kwargs['payload'])
        self.assertEqual(request.call_args_list[2].kwargs['payload']['messages'][-1]['tool_name'],'read_file')
        self.assertEqual(record.call_count,3)

    def test_maintainer_uses_paper_fields_and_exact_patches(self):
        ctx=context()
        value={'create_patterns':[{'name':'tests.md','content':'Run tests'}], 'update_patterns':[{'name':'build.md','edits':[{'op':'append','content':'\nVerify exit code'}]}],
               'update_index':'[build](wiki/patterns/build.md)\n[tests](wiki/patterns/tests.md)','append_log':'Refined build and tests'}
        result=maintain(ctx,value)
        self.assertEqual(result['pages'][1]['body'],'Use local Python\nVerify exit code')
        self.assertEqual(result['pages'][0]['source_ids'],[1])
        self.assertIn('DEEP ANALYSIS',maintainer_prompt(ctx))
        self.assertIn('update_index',maintainer_prompt(ctx))
        with self.assertRaisesRegex(ValueError,'目录缺少'):
            maintain(ctx,{**value,'update_index':'empty'})
        with self.assertRaisesRegex(ValueError,'校验失败'):
            maintain(ctx,{**value,'append_log':None})

    def test_incremental_prompt_and_patch_keep_valid_knowledge_across_batches(self):
        ctx = context()
        ctx['wiki'][0]['body'] = 'Use Python 3.11\nKeep the project environment'
        prompt = maintainer_prompt(ctx)
        self.assertIn('Preserve valid knowledge that this batch does not mention', prompt)
        self.assertIn('later records explicitly correct an earlier conclusion', prompt)
        self.assertIn('document those differences instead of overwriting', prompt)
        self.assertIn(ctx['wiki'][0]['body'].replace('\n', '\\n'), prompt)
        result = maintain(ctx, {
            'create_patterns': [],
            'update_patterns': [{'name': 'build', 'edits': [
                {'op': 'replace', 'target': 'Use Python 3.11', 'content': 'Use Python 3.12'},
                {'op': 'append', 'content': '\nUse Python 3.11 for the legacy deployment only'}]}],
            'update_index': '[build](wiki/patterns/build.md)', 'append_log': 'Corrected version; retained deployment condition',
        })
        self.assertEqual(result['pages'][0]['body'],
                         'Use Python 3.12\nKeep the project environment\nUse Python 3.11 for the legacy deployment only')

    def test_proposer_requires_real_finish_reads_and_correct_target(self):
        p=Proposer(context(),{})
        with self.assertRaisesRegex(ValueError,'目录'):
            p.call('finish',proposal())
        for path in ('wiki/index.md','wiki/skill-impact.md','traces/1','wiki/patterns/build.md'):
            p.call('read_file',{'path':path})
        with self.assertRaisesRegex(ValueError,'不一致'):
            p.call('finish',{**proposal(),'name':'other'})
        p.call('finish',proposal())
        self.assertIn('Use local Python',p.result['skill_md'])
        self.assertIn('Origin',p.result['purpose_md'])
        with self.assertRaisesRegex(ValueError,'finish 后'):
            p.call('read_file',{'path':'wiki/index.md'})

    def test_proposer_keeps_calling_without_limit_and_propagates_failure(self):
        read=[call('read_file',{'path':path},str(i)) for i,path in enumerate(('wiki/index.md','wiki/skill-impact.md','traces/1','wiki/patterns/build.md'))]
        replies=[ChatResponse({'role':'assistant','content':'explanation','tool_calls':read})]*9
        final=ChatResponse({'role':'assistant','content':'','tool_calls':[call('finish',proposal())]})
        with ApiSession(self.config) as session:
            session.client.complete=Mock(side_effect=[*replies,final])
            result=session.generate('skill',context())
            self.assertEqual(session.metrics['calls'],10)
            self.assertIn('Use local Python',result['skill_md'])
            session.client.complete=Mock(side_effect=[*replies,RuntimeError('offline')])
            with self.assertRaisesRegex(RuntimeError,'offline'):
                session.generate('skill',context())
            self.assertEqual(session.client.complete.call_count,10)

    def test_mixed_finish_and_reads_and_text_only_finish_are_failures(self):
        with ApiSession(self.config) as session:
            session.client.complete=Mock(return_value=ChatResponse({'role':'assistant','content':'finish({})'}))
            with self.assertRaisesRegex(ValueError,'未调用 finish'):
                session.generate('skill',context())
            session.client.complete=Mock(return_value=ChatResponse({'role':'assistant','content':'','tool_calls':[call('read_file',{'path':'wiki/index.md'}),call('finish',{'action':'no_action'})]}))
            with self.assertRaisesRegex(ValueError,'单独调用'):
                session.generate('skill',context())

    def test_invalid_json_is_saved_before_failure_and_read_without_model_call(self):
        runtime=Runtime(self.config);project=self.root/'project';project.mkdir();key=runtime.store.project(str(project));seed_record(runtime,key)
        job=runtime.enqueue(key,'raw')['job_id']
        raw={'done':True,'done_reason':'stop','message':{'role':'assistant','content':'<think>not JSON</think>','thinking':'separate'},'eval_count':8}
        with patch('wikiskill.live.ollama.request_json',return_value=raw) as request:
            runtime.run_job(job)
            self.assertEqual(runtime.store.job(job)['state'],'failed')
            self.assertEqual(runtime.store.rows('SELECT * FROM wiki'),[])
            self.assertIsNone(runtime.store.rows('SELECT consumed_by FROM trace_records')[0]['consumed_by'])
            with TestClient(create_app(self.config.root),base_url='http://127.0.0.1') as client:
                result=client.get(f'/api/jobs/{job}/model-responses').json()
                self.assertEqual(result['items'][0]['response'],raw)
            self.assertEqual(request.call_count,1)
        files=list((self.config.root/'reports/model-responses'/job).glob('*.json'))
        self.assertEqual(files[0].stat().st_mode & 0o777,0o600)

    def test_codex_dynamic_tools_use_only_allowed_callbacks(self):
        session=CodexSession(self.config);session.send=Mock()
        session.request=Mock(side_effect=[{'config':{}},{'thread':{'id':'thread'}}])
        session.start('WikiSkill skill job')
        params=session.request.call_args.args[1]
        self.assertEqual({t['name'] for t in params['dynamicTools']},{'read_file','finish'})
        self.assertTrue(all(t['type']=='function' for t in params['dynamicTools']))
        session.proposer=Proposer(context(),{});session.metrics={'calls':1}
        session.messages.put({'id':4,'method':'item/tool/call','params':{'threadId':'thread','tool':'read_file','arguments':{'path':'wiki/index.md'}}})
        import time
        session.receive(time.monotonic()+2)
        self.assertTrue(session.send.call_args.args[0]['result']['success'])
        session.messages.put({'id':5,'method':'item/tool/call','params':{'threadId':'other','tool':'read_file','arguments':{'path':'wiki/index.md'}}})
        with self.assertRaisesRegex(RuntimeError,'interactive'):
            session.receive(time.monotonic()+2)

    def test_patch_schema_uses_actual_source_targets(self):
        from wikiskill.live.generation import maintainer_schema, validate
        ctx=context()
        schema=maintainer_schema(ctx)
        value={'create_patterns':[], 'update_patterns':[{'name':'build.md','edits':[{'op':'replace','target':'invented target','content':'new'}]}],
               'update_index':'[build](wiki/patterns/build.md)', 'append_log':'Changed'}
        with self.assertRaisesRegex(ValueError,'校验失败'):
            validate(value,schema,'Maintainer')
        value['update_patterns'][0]['edits'][0]['target']='Use local Python'
        self.assertEqual(maintain(ctx,value)['pages'][0]['body'],'new')

    def test_invalid_finish_body_and_encoded_edit_array_are_rejected(self):
        p=Proposer(context(),{})
        with self.assertRaisesRegex(ValueError,'校验失败'):
            p.call('finish',{**proposal(),'skill_md':'# Missing YAML'})
        with self.assertRaisesRegex(ValueError,'校验失败'):
            p.call('finish',{'action':'patch','name':'build','edits':'[]'})

    def test_index_and_log_commit_with_patterns_and_conflicts_are_atomic(self):
        runtime=Runtime(self.config);project=self.root/'project';project.mkdir();key=runtime.store.project(str(project));seed_record(runtime,key)
        job_id=runtime.enqueue(key,'raw')['job_id'];job=runtime.store.job(job_id)
        ctx=runtime._context(job)
        value={'create_patterns':[{'name':'build.md','content':'Use local Python'}], 'update_patterns':[],
               'update_index':'[build](wiki/patterns/build.md): Use local Python', 'append_log':'Observed local interpreter fix'}
        runtime.store.update_job(job_id,context=ctx,result=maintain(ctx,value))
        runtime.put_wiki(str(project),[{'name':'other','body':'Concurrent user edit'}])
        with self.assertRaisesRegex(ValueError,'changed'):
            runtime._apply(runtime.store.job(job_id))
        self.assertEqual(runtime.store.rows('SELECT * FROM wiki_documents'),[])
        self.assertEqual([p['name'] for p in runtime.store.rows('SELECT * FROM wiki')],['other'])
        self.assertIsNone(runtime.store.rows('SELECT consumed_by FROM trace_records')[0]['consumed_by'])
        ctx=runtime._context(runtime.store.job(job_id))
        value['update_index']+='\n[other](wiki/patterns/other.md): Existing knowledge'
        runtime.store.update_job(job_id,context=ctx,result=maintain(ctx,value))
        runtime._apply(runtime.store.job(job_id))
        docs={p['kind']:p['body'] for p in runtime.store.rows('SELECT * FROM wiki_documents')}
        self.assertIn('Observed local interpreter fix',docs['log'])
        self.assertIn('wiki/patterns/build.md',docs['index'])
        self.assertEqual(len(runtime.store.rows('SELECT * FROM wiki_changes')),2)
