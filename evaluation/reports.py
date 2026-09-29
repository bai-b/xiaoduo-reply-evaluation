from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path

from .core import NAMES

LIMITATIONS = [
    "规则仅覆盖有限词汇与场景；真实 LLM 也会漏判、过度解释或受表述影响。未触发扣分不代表已确认优质，mock 模式不具备真实语义评审能力。",
    "原始题目未提供订单、商品、政策或工具记录；补充证据依赖维护者确认来源。待核实不是已证实幻觉；未检出断言也不等于完全无事实风险。",
    "综合分在事实维度不可评时重新归一化，仅为暂定质量分，不能据此决定扩大自动回复覆盖。",
    "20 条样本没有抽样方案、日期和业务分布，不能外推总体质量，也不应进行上线前后的因果比较。",
    "复核标签由开发者参考已看过的定性注释整理；程序隔离避免直接读答案，但不能消除设计阶段的偏差。",
    "基线偏重明确的信息请求，可能误判隐含上下文、同义改写、已经识别的商品或具有真实工具能力的系统。",
    "礼貌词、道歉词及“我帮您”只能提供有限线索，不能证明真实共情或实际执行；尚未进行双人盲标和跨模型重复运行实验。",
]


def summarize(results):
    dimensions = {}
    for key, name in NAMES.items():
        values = [r["scores"][key] for r in results if r["scores"][key] is not None]
        dimensions[key] = {"name": name, "mean": round(statistics.mean(values) * 25, 1) if values else None,
                           "assessed": len(values), "na": len(results) - len(values),
                           "distribution": {str(i): values.count(i) for i in range(5)}}
    statuses = Counter(c["status"] for r in results for c in r["claims"])
    count = sum(statuses.values())
    verified = statuses["supported"] + statuses["contradicted"]
    return {"count": len(results), "mean_score": round(statistics.mean(r["total_score"] for r in results), 1),
            "provisional_count": sum(r["provisional"] for r in results),
            "service_gap_count": sum(r["service_gap"] for r in results),
            "capped_count": sum(r["score_capped"] for r in results),
            "uncovered_count": sum(r["rule_coverage"] == "uncovered" for r in results),
            "claim_count": count, "verified_claim_count": verified,
            "claim_coverage": round(verified / count, 4) if count else None,
            "claim_statuses": {k: statuses[k] for k in ("supported", "contradicted", "unverified")},
            "dimensions": dimensions,
            "worst_ids": [r["id"] for r in sorted(results, key=lambda r: (r["total_score"], r["id"]))[:3]],
            "common_findings": [{"rule": k, "count": n} for k, n in Counter(f["rule"] for r in results for f in r["findings"]).most_common()]}


def fmt(value, percent=False):
    return "N/A" if value is None else f"{value * 100:.1f}%" if percent else str(value)


def markdown(payload):
    s, metadata = payload["summary"], payload["metadata"]
    modes = {'llm': '真实 LLM + 规则融合', 'mock': '离线 mock 混合演示（未调用 LLM）', 'rules': '离线规则基线'}
    lines = ["# 自动回复质量评估报告", "", f"运行模式：{modes[metadata['mode']]} · 版本 {metadata['version']} · 输入 {s['count']} 条", "",
             f"模型：{metadata.get('model') or '无'} · 本次接口请求：{metadata['runtime_llm_calls']} · 缓存命中：{metadata['cache_hits']}", "",
             "## 核心结论", "",
             f"整体已评维度均分为 **{s['mean_score']}/100**，其中 **{s['provisional_count']}/{s['count']}** 条为暂定分。",
             f"识别到 **{s['service_gap_count']}** 条服务解决缺口；抽取 **{s['claim_count']}** 个句段级断言，其中 **{s['claim_statuses']['unverified']}** 个待核实，证据覆盖率 **{fmt(s['claim_coverage'], True)}**。",
             "这说明需要优先补充个性化处理闭环与业务证据，不能仅凭当前分数决定扩大自动回复覆盖。", "",
             "断言单位为句段，可能含多个原子事实；覆盖率只针对抽取器检出的断言，不是全部事实覆盖率。", "",
             "## 指标与分布", "", "| 指标 | 均分 /100 | 已评 | N/A | 0/1/2/3/4 分计数 |", "|---|---:|---:|---:|---|" ]
    for d in s["dimensions"].values():
        lines.append(f"| {d['name']} | {fmt(d['mean'])} | {d['assessed']} | {d['na']} | {' / '.join(str(n) for n in d['distribution'].values())} |")
    lines += ["", "综合分按 25/35/15/25 权重计算；N/A 维度不计入分母。明确内部流程冲突或外部证据矛盾时封顶 49。所有阈值为启发式。", "", "## 最差 3 条", ""]
    for case_id in s["worst_ids"]:
        r = next(r for r in payload["results"] if r["id"] == case_id)
        lines += [f"### {case_id} · {r['intent']} · {r['total_score']} 分", "", f"用户：{r['user_question']}", "", f"回复：{r['auto_reply']}", ""]
        lines += [f"- [语义/{i['tag']}] {i['reason']} 证据：{i['quote']}" for i in r['semantic']['issues']]
        lines += [f"- [规则/{f['rule']}] {f['reason']} 证据（{'用户问题' if f['origin'] == 'question' else '自动回复'}）：{f['quote']}" for f in r["findings"]]
        lines += [f"- 建议：{a}" for a in r["suggestions"]]
        lines += [f"- 量表一致性待复核：{a}" for a in r.get('review_flags',[])]
        lines += [f"- 事实核验：{r['grounding_state']}；该状态不表示已确认幻觉。", ""]
    v = payload.get("validation")
    lines += ["## 人工参考对照", ""]
    if v:
        lines += [v["scope"], "", f"纳入 {v['included']} 条，争议排除 {len(v['excluded'])} 条，未标注 {len(v['unlabeled_ids'])} 条。",
                  f"TP={v['counts']['tp']}，FP={v['counts']['fp']}，FN={v['counts']['fn']}，TN={v['counts']['tn']}。",
                  f"Precision={fmt(v['precision'], True)}；Recall={fmt(v['recall'], True)}；F1={fmt(v['f1'])}；一致率={fmt(v['agreement'], True)}。", "",
                  "这些指标只描述服务缺口二元判断的一致性，不是四维评分准确率。标签与规则均在看过附件后整理，即便一致率很高也不能代表泛化效果。", "",
                  "### 争议条目", ""]
        lines += [f"- {x['id']}：{x['basis']}" for x in v["excluded"]]
        lines += ["", "### 误报与漏报", ""]
        errors = [x for x in v["details"] if x["outcome"] in ("fp", "fn")]
        lines += [f"- {x['id']}：{x['outcome'].upper()}，{x['basis']}" for x in errors] or ["- 当前纳入的回顾性标签上未出现不一致；不代表未见样本零错误，见行为测试中的已知局限。"]
    else:
        lines += ["本次未提供复核标签，未计算一致性指标。"]
    iv = payload.get('issue_validation')
    if iv:
        lines += ['', '## 多标签校准', '', iv['scope'], '',
                  f"Micro Precision={fmt(iv['micro']['precision'], True)}；Micro Recall={fmt(iv['micro']['recall'], True)}；Micro F1={fmt(iv['micro']['f1'])}；Macro F1={fmt(iv['macro_f1'])}。", '',
                  '| 标签 | TP | FP | FN | Precision | Recall | F1 | 未知排除 |', '|---|---:|---:|---:|---:|---:|---:|---:|']
        for tag, x in iv['per_tag'].items():
            lines.append(f"| {tag} | {x['tp']} | {x['fp']} | {x['fn']} | {fmt(x['precision'], True)} | {fmt(x['recall'], True)} | {fmt(x['f1'])} | {x['excluded']} |")
        lines += ['', '### 标签分歧（预测相对于复核标签，不预设哪方必然正确）', '']
        lines += [f"- {x['id']} / {x['tag']} / {x['kind'].upper()}：{x['basis']}" for x in iv['disagreements']] or ['- 暂无分歧。']
    lines += ["", "## 逐条结果", "", "| ID | 场景 | 暂定/综合分 | 服务缺口 | 事实状态 |", "|---|---|---:|---|---|"]
    lines += [f"| {r['id']} | {r['intent']} | {r['total_score']} | {'是' if r['service_gap'] else '未检出'} | {r['grounding_state']} |" for r in payload["results"]]
    flags = [(r['id'], flag) for r in payload['results'] for flag in r.get('review_flags',[])]
    lines += ['', '## 量表内部一致性复核', '']
    lines += [f'- {case_id}：{flag}' for case_id, flag in flags] or ['未检出已定义的评分/状态冲突；不代表人工已确认无误。']
    lines += ["", "## 局限性", ""] + [f"- {x}" for x in LIMITATIONS]
    lines += ["", "## 下一步", "", "1. 接入有版本与来源的政策、商品和工具记录，优先核实退款时效、质保、运费、补偿及平台功能。",
              "2. 针对通用回复建立澄清/查询/自助/转人工的能力边界，避免无能力的代操作承诺。",
              "3. 新增未见样本与双人标注，保留分歧，再比较规则与结构化 LLM 裁判，报告成本和稳定性。", "",
              "## 可复现信息", "", f"- 规则版本：{metadata['version']}", f"- 输入 SHA-256：`{metadata['input_sha256']}`",
              f"- 评分实现 SHA-256：`{metadata['scorer_sha256']}`", f"- 外部业务证据：{metadata['evidence_count']} 条",
              f"- AI 工具：Codex 辅助设计、实现、复核与文档；运行模式为 {metadata['mode']}，LLM 模型为 {metadata.get('model') or '无'}。", ""]
    return "\n".join(lines)


def write_reports(payload, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "report.md").write_text(markdown(payload), encoding="utf-8")
    if payload.get("validation"):
        (outdir / "validation.json").write_text(json.dumps(payload["validation"], ensure_ascii=False, indent=2), encoding="utf-8")
    if payload.get('issue_validation'):
        (outdir / 'issue_validation.json').write_text(json.dumps(payload['issue_validation'], ensure_ascii=False, indent=2), encoding='utf-8')
    with (outdir / "scores.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "场景", "综合分", "是否暂定", *NAMES.values(), "服务缺口", "事实状态"])
        for r in payload["results"]:
            # Neutralise spreadsheet formulas in external string columns.
            values = [r["id"], r["intent"], r["total_score"], r["provisional"], *[r['scores'][key] for key in NAMES], r["service_gap"], r["grounding_state"]]
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in values])
    template = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    (outdir / "index.html").write_text(template.replace("__REPORT_DATA__", data), encoding="utf-8")
    comparison_lines = ['# 独立参考回复差异分析', '', '该模块在主评分结束后运行，不修改分数、不参与标签一致性指标。参考回复本身也需要业务证据。', '']
    for r in payload['results']:
        if 'comparison' not in r:
            continue
        c = r['comparison']
        comparison_lines += [f"## {r['id']} · {r['user_question']}", '', f"模式：{c['mode']}", '']
        for key, name in [('auto_covered','自动回复已覆盖'), ('reference_added','人工参考新增'), ('key_gaps','关键差距'), ('improvements','改进方向'), ('reference_caveats','参考局限')]:
            comparison_lines += [f'### {name}', ''] + [f'- {x}' for x in c[key]] + ['']
    (outdir / 'comparison.md').write_text('\n'.join(comparison_lines), encoding='utf-8')
