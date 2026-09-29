from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from run import ROOT, build, load_config
from evaluation.core import evaluate
from evaluation.semantic import LLMClient, fuse
from evaluation.reports import summarize, write_reports
from evaluation.validation import validate_labels, validate_issues
from evaluation.semantic import TAGS
from collections import Counter

NO_ARGS = {'inspect_dataset', 'get_rubric', 'get_overview', 'evaluate_batch', 'review_disagreements', 'export_report'}
TOOLS = NO_ARGS | {'find_cases','get_case','request_evidence'}


class AgentProtocolError(ValueError):
    """A model decision failed validation; no tool may execute it."""


def check_answer(answer, cited, seen):
    mentioned = set(re.findall(r'\bcase_\d+\b',answer)) | set(cited)
    if mentioned-seen:
        return '最终回答引用未查看的案例，请先取证或删除该引用。'
    for line in answer.splitlines():
        if re.search(r'切题|相关性|有效性|语气|事实依据|维度|关怀',line) and re.search(r'(?<!\d)[0-4]\s*[/／]\s*5(?!\d)',line):
            return '维度量表是0–4，工具分数必须写为x/4，不能写为x/5。请核对并修正。'
    return None


def validate_action(data):
    if not isinstance(data,dict) or not {'action','arguments'} <= set(data) or set(data)-{'action','arguments','brief'}:
        raise AgentProtocolError('Agent 决策需要 action、arguments；工具调用还需要 brief，不能添加其他字段。')
    if ('brief' not in data and data['action'] != 'finish') or ('brief' in data and (not isinstance(data['brief'],str) or len(data['brief'])>400)):
        raise AgentProtocolError('工具调用需要最多400字的 brief 字符串；finish 可以省略 brief。')
    action, args = data['action'], data['arguments']
    if not isinstance(action,str) or action not in TOOLS | {'finish'} or not isinstance(args,dict):
        raise AgentProtocolError('Agent 请求了未注册的工具或 arguments 不是对象')
    if action in NO_ARGS and args:
        raise AgentProtocolError('此工具不接受参数')
    if action in ('get_case','request_evidence') and (set(args) != {'case_id'} or not isinstance(args['case_id'],str)):
        raise AgentProtocolError('缺少合法 case_id')
    if action == 'find_cases' and (set(args) != {'filter','limit'} or args['filter'] not in ('all','low_score','high_risk','unverified','disagreement') or type(args['limit']) is not int or not 1 <= args['limit'] <= 20):
        raise AgentProtocolError('筛选条件不合法')
    if action == 'finish' and (set(args) != {'answer','case_ids'} or not isinstance(args['answer'],str) or not 1 <= len(args['answer']) <= 12000 or not isinstance(args['case_ids'],list) or not all(isinstance(x,str) for x in args['case_ids'])):
        raise AgentProtocolError('最终答复结构不合法；arguments 必须包含 answer 字符串和 case_ids 字符串数组。')
    return data


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    dataset_name: str = '题目附件 · 20 条客服回复'
    rows: list = field(default_factory=list)
    result: dict | None = None
    builtin: bool = True
    history: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    report_url: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def new_session():
    s = Session()
    s.rows = json.loads((ROOT/'task3_auto_replies.json').read_text(encoding='utf-8'))
    path = ROOT/'output/results.json'
    if path.exists():
        candidate = json.loads(path.read_text(encoding='utf-8'))
        candidate_rows = [{k:r[k] for k in ('id','user_question','auto_reply')} for r in candidate.get('results',[])]
        if candidate['metadata']['config']['prompt_version'] == load_config(ROOT/'config.json')['prompt_version'] and candidate_rows == s.rows:
            s.result = candidate
            s.report_url = '/reports/latest/index.html'
    return s


def compact_case(r):
    return {k:r[k] for k in ('id','user_question','total_score','quality_status','scores','grounding_state','issue_tags','service_gap','review_flags') if k in r}


class QualityAgent:
    def __init__(self, session, planner=None, mode='llm'):
        self.session = session
        self.mode = mode
        self.client = planner or LLMClient(ROOT/'.cache/agent')
        self.seen = set()
        self.export_url = None
        self.evidence_request = None

    def result(self):
        if self.session.result is None:
            raise ValueError('当前数据集尚未评估，请调用 evaluate_batch。')
        return self.session.result

    def get_case(self, case_id):
        r = next((r for r in self.result()['results'] if r['id'] == case_id), None)
        if r is None:
            raise ValueError('当前数据集不存在该案例')
        self.seen.add(case_id)
        return r

    def execute(self, action, args):
        validate_action({'action':action,'arguments':args,'brief':''})
        s = self.session
        if action == 'inspect_dataset':
            return {'name':s.dataset_name,'count':len(s.rows),'columns':['id','user_question','auto_reply'], 'evaluated':s.result is not None, 'has_reference_labels':s.builtin}
        if action == 'get_rubric':
            return {'rubric':(ROOT/'prompts/judge.md').read_text(encoding='utf-8'), 'config':load_config(ROOT/'config.json')}
        if action == 'evaluate_batch':
            if s.result is not None:
                return {'reused':True,'message':'当前会话已有该数据集的评估结果，复用并保留证据。','summary':s.result['summary']}
            if len(s.rows) > 100:
                raise ValueError('本地原型单批最多评估100条，请拆分数据集。')
            folder = ROOT/'.cache/sessions'/s.id
            folder.mkdir(parents=True,exist_ok=True)
            input_path = folder/'input.json'
            input_path.write_text(json.dumps(s.rows,ensure_ascii=False),encoding='utf-8')
            s.result = build(input_path, ROOT/'task3_human_ref.json' if s.builtin else None,
                             ROOT/'data/review_labels.json' if s.builtin else None, mode=self.mode,
                             issue_labels_path=ROOT/'data/issue_labels.json' if s.builtin else None)
            self.seen.update(s.result['summary']['worst_ids'])
            return {'reused':False,'summary':s.result['summary'],'mode':self.mode}
        if action == 'get_overview':
            p = self.result()
            self.seen.update(p['summary']['worst_ids'])
            return {'summary':p['summary'],'model':p['metadata']['model'],'mode':p['metadata']['mode'],
                    'score_scale':{'dimension_max':4,'total_max':100},
                    'grounding_case_states':dict(Counter(r['grounding_state'] for r in p['results'])),
                    'rubric_version':p['metadata']['config']['prompt_version'],
                    'calibration':p['issue_validation']['micro'] if p.get('issue_validation') else None,
                    'warning':'质量均分不是评估准确率；未知事实不能判错；一致性为回顾性标签对照。'}
        if action == 'find_cases':
            rows = self.result()['results']
            f = args['filter']
            if f == 'high_risk': rows = [r for r in rows if r['quality_status']=='高风险待复核']
            if f == 'unverified': rows = [r for r in rows if any(c['status']=='unverified' for c in r['claims'])]
            if f == 'low_score': rows = [r for r in rows if r['quality_status']=='需要改进' or r['total_score']<80]
            if f == 'disagreement':
                ids = {x['id'] for x in (self.result().get('issue_validation') or {}).get('disagreements',[])}
                rows = [r for r in rows if r['id'] in ids or r.get('review_flags')]
            rows = sorted(rows,key=lambda r:(r['total_score'],r['id']))
            selected = rows[:args['limit']]
            self.seen.update(r['id'] for r in selected)
            return {'total_matching':len(rows),'cases':[compact_case(r) for r in selected]}
        if action == 'get_case':
            return {'case':copy.deepcopy(self.get_case(args['case_id'])), 'score_scale':{'dimension_max':4,'total_max':100},
                    'warning':'维度4分即满分；事实N/A不能解释为已证实错误。'}
        if action == 'review_disagreements':
            p = self.result()
            v = p.get('issue_validation')
            if not v: return {'available':False,'reason':'当前数据没有参考标签，无法报告标签一致性。'}
            self.seen.update(r['id'] for r in v['disagreements'])
            return {'available':True,**v}
        if action == 'request_evidence':
            r = self.get_case(args['case_id'])
            self.evidence_request = r['id']
            return {'case_id':r['id'],'claims':r['claims'],'instruction':'请在证据表单选择原文断言、填写真实来源、结论及依据。结论按操作者提供的证据处理，不由Agent自动认证。'}
        if action == 'export_report':
            self.result()
            folder = ROOT/'.cache/sessions'/s.id/'report'
            write_reports(s.result,folder)
            self.export_url = f'/reports/{s.id}/index.html'
            s.report_url = self.export_url
            return {'report_url':self.export_url,'json_url':f'/reports/{s.id}/results.json','case_count':len(s.rows)}
        raise ValueError('工具不可执行')

    def run(self, message, emit=lambda x:None):
        if not isinstance(message,str) or not message.strip() or len(message)>6000:
            raise ValueError('请提供1至6000字的任务描述')
        prompt = (ROOT/'prompts/agent.md').read_text(encoding='utf-8')
        observations, trace = [], []
        tool_calls = 0
        protocol_errors = 0
        for step in range(10):
            emit({'kind':'planning','text':'正在选择下一步工具…'})
            try:
                action = self.client.request(prompt, {'user_request':message,'history':self.session.history[-6:],
                             'dataset':{'name':self.session.dataset_name,'count':len(self.session.rows),'evaluated':self.session.result is not None},
                             'observations':observations,'remaining_tool_steps':7-tool_calls}, validate_action)
            except AgentProtocolError as error:
                protocol_errors += 1
                if protocol_errors > 2:
                    raise
                observations.append({'output_check_error':str(error)})
                emit({'kind':'output_check','text':'模型决策格式未通过校验，正在要求重新输出。'})
                continue
            name,args = action['action'],action['arguments']
            if name == 'finish':
                error = check_answer(args['answer'],args['case_ids'],self.seen)
                if error:
                    observations.append({'output_check_error':error})
                    emit({'kind':'output_check','text':error})
                    continue
                answer = args['answer']
                self.session.history += [{'role':'user','content':message},{'role':'assistant','content':answer}]
                return {'answer':answer,'case_ids':args['case_ids'],'trace':trace,'report_url':self.export_url,
                        'evidence_case':self.evidence_request,'overview':self.session.result['summary'] if self.session.result else None,
                        'mode':'llm_tool_agent'}
            if tool_calls >= 7:
                break
            tool_calls += 1
            emit({'kind':'tool_start','tool':name,'text':action['brief']})
            try:
                output = self.execute(name,args)
                event = {'tool':name,'arguments':args,'status':'ok','brief':action['brief']}
            except (ValueError,OSError) as e:
                output = {'error':str(e)}
                event = {'tool':name,'arguments':args,'status':'error','brief':str(e)}
            trace.append(event)
            emit({'kind':'tool_done',**event})
            observations.append({'tool':name,'arguments':args,'result':output})
        raise ValueError('本轮工具步骤达到上限，请缩小任务范围；已完成的工具结果保留在当前会话。')


def apply_evidence(session, record):
    fields = {'case_id','quote','status','source','rationale'}
    if not isinstance(record,dict) or set(record) != fields or not all(isinstance(v,str) and v.strip() for v in record.values()):
        raise ValueError('证据需要案例、原文、结论、来源和说明')
    if record['status'] not in ('supported','contradicted'):
        raise ValueError('证据结论只能是支持或矛盾')
    if len(record['source'])>1000 or len(record['rationale'])>3000:
        raise ValueError('证据来源或说明过长')
    if session.result is None:
        raise ValueError('请先评估当前数据集')
    rows = session.result['results']
    old = next((r for r in rows if r['id']==record['case_id']),None)
    if old is None or record['quote'] not in {c['quote'] for c in old['claims']}:
        raise ValueError('请选择当前案例已抽取的断言')
    # Scope evidence by case as the same sentence may refer to different orders/products.
    evidence = session.evidence.setdefault(old['id'],{})
    evidence[record['quote']] = {k:record[k] for k in ('quote','status','source','rationale')}
    baseline = evaluate(old,evidence)
    new = fuse(baseline,old['semantic'],load_config(ROOT/'config.json'),evidence)
    # Apply to retained LLM-only claims too; reusing a semantic response does not certify it.
    for claim in new['claims']:
        if claim['quote'] in evidence:
            claim.update({k:evidence[claim['quote']][k] for k in ('status','source','rationale')})
    # Re-fuse once with the updated claims as the authoritative baseline.
    new = fuse(new,old['semantic'],load_config(ROOT/'config.json'),evidence)
    for key in ('reference','comparison','semantic_mode'):
        if key in old: new[key]=old[key]
    new['baseline_scores'] = old['baseline_scores']
    rows[rows.index(old)] = new
    session.result['summary'] = summarize(rows)
    session.result['metadata']['manual_evidence'] = copy.deepcopy(session.evidence)
    session.result['metadata']['evidence_count'] = sum(len(e) for e in session.evidence.values())
    session.result['metadata']['manual_evidence_scope'] = '操作者提供的来源和结论，未自动验证来源真实性；仅当前会话生效。'
    session.report_url = None
    return {'case':compact_case(new),'before_score':old['total_score'],'after_score':new['total_score'],'overview':session.result['summary'],
            'note':'已按操作者提供的业务证据重新计算；没有调用模型自动证明该来源。'}
