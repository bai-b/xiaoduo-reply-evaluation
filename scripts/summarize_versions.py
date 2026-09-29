"""Produce comparisons from frozen v2 results and the current v3 run."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main():
    previous=json.loads((ROOT/'experiments/v2-results.json').read_text(encoding='utf-8'))
    current=json.loads((ROOT/'output/results.json').read_text(encoding='utf-8'))
    behavior=json.loads((ROOT/'experiments/behavior-results.json').read_text(encoding='utf-8'))
    labels={name:hashlib.sha256((ROOT/'data'/name).read_bytes()).hexdigest() for name in ('issue_labels.json','review_labels.json')}
    assert labels['issue_labels.json']==previous['metadata']['issue_labels_sha256']==current['metadata']['issue_labels_sha256']
    assert labels['review_labels.json']==previous['metadata']['labels_sha256']==current['metadata']['labels_sha256']
    report={'label_files_unchanged':True,'label_sha256':labels,'scope':'已见开发数据的回顾性比较；人工构造行为测试不是独立真实业务盲测。',
            'versions':{}}
    for name,p in [('v2',previous),('v3',current)]:
        report['versions'][name]={'summary':p['summary'],'micro':p['issue_validation']['micro'],'macro_f1':p['issue_validation']['macro_f1'],
                                 'binary_service_gap_f1':p['validation']['f1'],'per_tag':p['issue_validation']['per_tag'],'behavior':behavior['summary'][name]}
    report['changes']=[{'id':r['id'],'old_score':old['total_score'],'new_score':r['total_score'],'old_tags':old['issue_tags'],'new_tags':r['issue_tags']}
                       for old,r in zip(previous['results'],current['results']) if old['id']==r['id']]
    (ROOT/'experiments/version-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    a,b=report['versions']['v2'],report['versions']['v3']
    lines=['# v3 评价标准改进结果','','## 结论','','新版减少了过度标注，但没有全面解决漏检。不能用质量均分升高证明评估器更准确。','',
           '| 指标 | v2 | v3 |','|---|---:|---:|',
           f"| Micro Precision | {a['micro']['precision']:.4f} | {b['micro']['precision']:.4f} |",
           f"| Micro Recall | {a['micro']['recall']:.4f} | {b['micro']['recall']:.4f} |",
           f"| Micro F1 | {a['micro']['f1']:.4f} | {b['micro']['f1']:.4f} |",
           f"| Macro F1 | {a['macro_f1']:.4f} | {b['macro_f1']:.4f} |",
           f"| FP / FN | {a['micro']['fp']} / {a['micro']['fn']} | {b['micro']['fp']} / {b['micro']['fn']} |",
           f"| 二元服务缺口 F1 | {a['binary_service_gap_f1']} | {b['binary_service_gap_f1']} |",
           f"| 构造行为断言通过 | {a['behavior']['passed_assertions']}/{a['behavior']['assertions']} | {b['behavior']['passed_assertions']}/{b['behavior']['assertions']} |",
           f"| 回复暂定均分（非准确率） | {a['summary']['mean_score']} | {b['summary']['mean_score']} |",'',
           '同样的20条数据、同样的冻结复核标签、同一模型 qwen3.8-max-0902。标签文件散列校验一致；没有在看到结果后改金标准。v2原始结果与提示词保留于 experiments，旧代码位于提交 b8bf47c。','',
           '## 改进发生在哪里','','- case_16 从55分调整到88.3分：承认预算和场景澄清已经有效，只将缺少具体型号视为轻微追问缺口。',
           '- case_20 新识别 intent_mismatch：用户已卡在流程，再重复流程不能充分解决。',
           '- 普通咨询不再普遍要求道歉，共情标签误报从5个降到1个。',
           '- 每条真实结果增加服务任务、情绪原文、下一步状态与问题严重程度，给人工复核更多明确依据。','',
           '## 仍未解决的问题','','共情正例检出从1个降到0个，不能只讲误报下降。新版对缺少情绪强度、主动服务和具体状态核实仍存在漏判；附带的安全建议、自助入口会让模型高估处理充分性。',
           'case_18 仍被偏严评价；部分输出把下一步标为轻微缺口、有效性却给2分。程序将这种自相矛盾加入复核列表，不静默改分。',
           '现有标签也有主观性和不完备性，FP/FN只是相对这些标签的分歧。两轮都是已见样本上的开发比较，不是独立盲测。下一步需要人工复核共情/服务推进标签，并在新增业务样本上验证。','',
           '## 行为测试','','10条预先声明的AI辅助构造样例，覆盖中性语气、明确自助、流程卡点、合理澄清、侮辱、未知承诺和回复内指令干扰。v2通过9/10条，v3通过10/10条。完整输出与失败项均在 experiments/behavior-results.json，不属于真实业务泛化证明。','',
           '## Agent 的角色','','Agent调度这些确定的评估工具，帮助查询、解释、筛选、复核、补证据和导出；不会因为增加聊天界面就自动提高裁判准确性。所有事实支持状态仍依赖外部维护的证据。','',
           '## 逐条变化','','| ID | v2分数 | v3分数 | v3标签 |','|---|---:|---:|---|']
    lines += [f"| {r['id']} | {r['old_score']} | {r['new_score']} | {', '.join(r['new_tags']) or '未检出'} |" for r in report['changes']]
    (ROOT/'docs/results-analysis.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':main()
