import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from assistant_app.agent import QualityAgent, Session, apply_evidence, new_session, validate_action, check_answer
from assistant_app.server import AppServer, validate_upload
from run import ROOT, build


class FakePlanner:
    def __init__(self, actions): self.actions=iter(actions)
    def request(self,prompt,payload,validator): return validator(next(self.actions))


def action(name,args=None): return {'action':name,'arguments':args or {},'brief':'测试工具执行'}


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload=build(ROOT/'task3_auto_replies.json')

    def session(self):
        return Session(rows=[{k:r[k] for k in ('id','user_question','auto_reply')} for r in self.payload['results']],result=copy.deepcopy(self.payload))

    def test_tool_loop_runs_and_cites_observed_case(self):
        s=self.session()
        planner=FakePlanner([action('get_case',{'case_id':'case_11'}),action('finish',{'answer':'该回复流程混淆，需要复核。','case_ids':['case_11']})])
        result=QualityAgent(s,planner).run('解释case_11')
        self.assertEqual(result['case_ids'],['case_11'])
        self.assertEqual(result['trace'][0]['tool'],'get_case')
        self.assertEqual(len(s.history),2)

    def test_arbitrary_tool_and_extra_args_rejected(self):
        for a in [action('read_file',{'path':'.env'}),action('get_overview',{'path':'.env'}),action('find_cases',{'filter':'all','limit':True})]:
            with self.assertRaises(ValueError): validate_action(a)

    def test_unknown_citation_requires_repair(self):
        planner=FakePlanner([action('finish',{'answer':'未知案例','case_ids':['secret']}),action('finish',{'answer':'需要先确认案例编号。','case_ids':[]})])
        result=QualityAgent(self.session(),planner).run('查询不存在的案例')
        self.assertEqual(result['case_ids'],[])

    def test_wrong_dimension_denominator_is_not_delivered(self):
        self.assertIsNotNone(check_answer('有效性（3/5）：不错',[],set()))
        self.assertIsNone(check_answer('有效性（3/4）：不错',[],set()))
        self.assertIsNotNone(check_answer('case_99 很差',[],{'case_01'}))

    def test_output_check_retries_with_actual_scale(self):
        planner=FakePlanner([action('get_case',{'case_id':'case_16'}),
            action('finish',{'answer':'有效性3/5','case_ids':['case_16']}),
            action('finish',{'answer':'有效性3/4','case_ids':['case_16']})])
        answer=QualityAgent(self.session(),planner).run('解释评分')
        self.assertEqual(answer['answer'],'有效性3/4')

    def test_malformed_decision_cannot_execute_and_can_be_repaired(self):
        planner=FakePlanner([{'action':'get_case','arguments':{'case_id':'case_16'}},
            action('get_case',{'case_id':'case_16'}),
            action('finish',{'answer':'有效性3/4','case_ids':['case_16']})])
        answer=QualityAgent(self.session(),planner).run('解释评分')
        self.assertEqual(len(answer['trace']),1)
        self.assertEqual(answer['trace'][0]['tool'],'get_case')

    def test_repeated_invalid_decisions_fail_explicitly(self):
        planner=FakePlanner([action('read_file',{'path':'.env'})]*3)
        with self.assertRaises(ValueError): QualityAgent(self.session(),planner).run('无效指令')

    def test_finish_does_not_require_next_step_display_text(self):
        planner=FakePlanner([{'action':'finish','arguments':{'answer':'请指定要分析的数据。','case_ids':[]}}])
        result=QualityAgent(self.session(),planner).run('你好')
        self.assertEqual(result['trace'],[])

    def test_upload_rejects_duplicates_and_drops_extra_fields(self):
        row={'id':'x','user_question':'问题','auto_reply':'回复','secret':'not-for-evaluation'}
        self.assertNotIn('secret',validate_upload([row])[0])
        with self.assertRaises(ValueError): validate_upload([row,row])

    def test_no_result_cannot_reuse_old_numbers(self):
        with self.assertRaises(ValueError): QualityAgent(Session(),FakePlanner([])).execute('get_overview',{})

    def test_evidence_updates_only_its_session(self):
        a,b=self.session(),self.session()
        r=next(x for x in a.result['results'] if x['id']=='case_08')
        before=copy.deepcopy(b.result)
        result=apply_evidence(a,{'case_id':r['id'],'quote':r['claims'][0]['quote'],'status':'contradicted','source':'人工构造测试政策','rationale':'仅用于测试证据约束'})
        self.assertLessEqual(result['after_score'],49)
        self.assertEqual(b.result,before)
        self.assertIn('manual_evidence_scope',a.result['metadata'])
        self.assertIsNone(a.report_url)

    def test_evidence_requires_exact_case_claim(self):
        with self.assertRaises(ValueError):
            apply_evidence(self.session(),{'case_id':'case_08','quote':'不存在的断言','status':'supported','source':'测试','rationale':'测试'})

    def test_semantic_judgement_can_be_reused_without_exposing_references_to_planner_as_instructions(self):
        agent=QualityAgent(self.session(),FakePlanner([]))
        result=agent.execute('evaluate_batch',{})
        self.assertTrue(result['reused'])
        with self.assertRaises(ValueError):agent.execute('get_case',{'case_id':'../../.env'})


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=AppServer(('127.0.0.1',0),'mock')
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def bootstrap(self):
        with urllib.request.urlopen(self.base+'/api/bootstrap') as response:return json.load(response)

    def test_static_server_does_not_expose_secrets_or_paths(self):
        for path in [r'/\.env','/.env','/reports/latest/../../.env','/reports/latest/.env','/api/unknown']:
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.base+path)
            self.assertEqual(error.exception.code,404)

    def test_mutation_requires_local_token(self):
        body=json.dumps({'session_id':self.bootstrap()['session_id']}).encode()
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(self.base+'/api/reset',data=body,headers={'Content-Type':'application/json'}))
        self.assertEqual(error.exception.code,403)

    def test_upload_clears_previous_result_and_history(self):
        b=self.bootstrap()
        body={'session_id':b['session_id'],'name':'new.json','rows':[{'id':'new','user_question':'问题','auto_reply':'回复'}]}
        request=urllib.request.Request(self.base+'/api/upload',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Session-Token':b['csrf']})
        with urllib.request.urlopen(request) as response:self.assertEqual(response.status,200)
        session=self.server.sessions[b['session_id']]
        self.assertIsNone(session.result);self.assertFalse(session.builtin);self.assertEqual(session.history,[])

    def test_bad_host_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(self.base+'/api/bootstrap',headers={'Host':'evil.invalid'}))
        self.assertEqual(error.exception.code,403)


if __name__=='__main__':unittest.main()
