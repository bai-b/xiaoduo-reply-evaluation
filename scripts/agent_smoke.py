"""Real model tool-loop acceptance. Saves only dataset-specific, non-secret traces."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from run import load_env
from assistant_app.agent import QualityAgent,new_session


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    load_env();session=new_session();records=[]
    tasks=[
        '分析当前这批回复，说明主要问题并生成一份报告。',
        'case_16 为什么得到这个分数？结合原文说明，不用重新评估整批数据。',
        '这次评价和人工标签有哪些分歧？目前能据此扩大自动回复覆盖吗？',
        '我想补充 case_08 的材质证据，请打开它的证据表单。',
    ]
    for task in tasks:
        agent=QualityAgent(session)
        response=agent.run(task,lambda e:print(e.get('tool') or e.get('text'),flush=True))
        records.append({'request':task,'response':response})
    required=[{'get_overview','export_report'},{'get_case'},{'review_disagreements'},{'request_evidence'}]
    for record, expected in zip(records,required):
        used={t['tool'] for t in record['response']['trace'] if t['status']=='ok'}
        if not expected<=used:raise AssertionError(f'Missing tools: {expected-used}')
    # A genuinely unevaluated dataset must execute evaluation rather than reuse old scores.
    imported=new_session();imported.builtin=False;imported.result=None;imported.history=[]
    imported.dataset_name='人工构造的新增验收样本'
    imported.rows=[{'id':'new_01','user_question':'订单编号在哪里看？','auto_reply':'请打开订单列表，进入对应订单详情，即可查看订单编号。'}]
    task='评估这份新导入的数据并生成报告。'
    response=QualityAgent(imported).run(task,lambda e:print(e.get('tool') or e.get('text'),flush=True))
    if not {'evaluate_batch','export_report'}<={t['tool'] for t in response['trace'] if t['status']=='ok'}:
        raise AssertionError('New dataset was not evaluated and exported')
    if imported.result['summary']['count']!=1:raise AssertionError('Old dataset leaked into new evaluation')
    records.append({'request':task,'response':response})
    output={'mode':'real_qwen_tool_agent','session_id':session.id,'all_checks_passed':True,'turns':records}
    (ROOT/'experiments/agent-smoke.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Real agent smoke checks passed.')


if __name__=='__main__':main()
