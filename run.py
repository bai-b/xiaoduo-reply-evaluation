"""Usage: python run.py --output output"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from evaluation import VERSION
from evaluation.core import evaluate
from evaluation.reports import LIMITATIONS, summarize, write_reports
from evaluation.validation import validate_labels
from evaluation.validation import validate_issues
from evaluation.semantic import LLMClient, TAGS, fuse, mock_judge, validate_judgment
from evaluation.comparison import compare

ROOT = Path(__file__).resolve().parent


def load_list(path: Path, required: tuple[str, ...], unique=True):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path.name}: 必须是非空 JSON 数组")
    seen = set()
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}[{i}]: 必须是对象")
        for field in required:
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{path.name}[{i}]: {field} 必须是非空字符串")
        if unique:
            if row["id"] in seen:
                raise ValueError(f"{path.name}: 重复 id {row['id']}")
            seen.add(row["id"])
    return data


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                if key.strip() in {'DASHSCOPE_API_KEY', 'LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL', 'LLM_EXTRA_BODY'}:
                    os.environ.setdefault(key.strip(), value.strip())


def load_config(path):
    config = json.loads(path.read_text(encoding='utf-8'))
    if set(config.get('weights', {})) != {'relevance', 'usefulness', 'tone', 'grounding'}:
        raise ValueError('配置必须包含四个维度权重')
    if any(type(x) not in (int, float) or not 0 < x <= 100 for x in config['weights'].values()) or sum(config['weights'].values()) != 100:
        raise ValueError('权重必须为正数且合计100')
    for name, maximum in [('hard_gate_cap', 100), ('acceptable_threshold', 100), ('service_gap_max_score', 4)]:
        if type(config.get(name)) not in (int, float) or not 0 <= config[name] <= maximum:
            raise ValueError(f'配置阈值无效: {name}')
    return config


def build(input_path, reference_path=None, labels_path=None, evidence_path=None, mode='mock',
          issue_labels_path=None, config_path=ROOT / 'config.json', pairwise=False, client=None):
    rows = load_list(input_path, ("id", "user_question", "auto_reply"))
    evidence = {}
    if evidence_path:
        for item in load_list(evidence_path, ("quote", "status", "source", "rationale"), unique=False):
            if item["status"] not in ("supported", "contradicted"):
                raise ValueError("业务证据 status 仅支持 supported / contradicted")
            if item["quote"] in evidence:
                raise ValueError("业务证据存在重复断言，请先合并并解决来源冲突")
            evidence[item["quote"]] = item
    config = load_config(config_path)
    if mode not in ('rules', 'mock', 'llm'):
        raise ValueError('未知评估模式')
    if mode == 'llm' and client is None:
        client = LLMClient(ROOT / '.cache' / 'llm')
    results = []
    # Score first. The scorer never receives either references or labels.
    for row in rows:
        clean = {k: row[k] for k in ('id', 'user_question', 'auto_reply')}
        baseline = evaluate(clean, evidence)
        semantic = client.judge(clean, baseline) if mode == 'llm' else mock_judge(baseline)
        validate_judgment(semantic, clean)
        result = fuse(baseline, semantic, config, evidence)
        result['semantic_mode'] = mode
        results.append(result)
        if mode == 'llm':
            print(f"语义评分完成: {row['id']}", flush=True)
    validation = None
    if labels_path:
        labels = load_list(labels_path, ("id", "basis"))
        for item in labels:
            if "service_gap" not in item or (item["service_gap"] is not None and type(item["service_gap"]) is not bool):
                raise ValueError("复核标签 service_gap 必须为 true / false / null")
        validation = validate_labels(results, labels)
    issue_validation = validate_issues(results, load_list(issue_labels_path, ('id', 'basis')), TAGS) if issue_labels_path else None
    if reference_path:
        references = load_list(reference_path, ("id", "human_reference", "annotator_notes"))
        ref_map = {r["id"]: r for r in references}
        if set(ref_map) != {r["id"] for r in results}:
            raise ValueError("人工参考与输入 id 集合不一致")
        for result in results:
            result["reference"] = ref_map[result["id"]]
            result['comparison'] = compare(result, result['reference'], client if pairwise else None)
            if pairwise:
                print(f"参考对比完成: {result['id']}", flush=True)
    return {"metadata": {"version": VERSION, "mode": mode, "model": client.model if client else None, "input_file": input_path.name,
                         "input_sha256": digest(input_path), "scorer_sha256": digest(ROOT / "evaluation" / "core.py"),
                         "code_sha256": {p.name: digest(p) for p in sorted((ROOT / 'evaluation').glob('*.py'))},
                         "reference_sha256": digest(reference_path) if reference_path else None,
                         "labels_sha256": digest(labels_path) if labels_path else None,
                         "evidence_sha256": digest(evidence_path) if evidence_path else None,
                         "issue_labels_sha256": digest(issue_labels_path) if issue_labels_path else None,
                         "prompt_sha256": digest(ROOT / 'prompts' / 'judge.md'),
                         "compare_prompt_sha256": digest(ROOT / 'prompts' / 'compare.md'),
                         "config": config, "config_sha256": digest(config_path),
                         "evidence_count": len(evidence), "runtime_llm_calls": client.calls if client else 0,
                         "cache_hits": client.cache_hits if client else 0, "usage": client.usage if client else None,
                         "pairwise": bool(pairwise)},
            "summary": summarize(results), "results": results, "validation": validation,
            "issue_validation": issue_validation, "limitations": LIMITATIONS}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description="客服自动回复质量评估：可复现离线基线")
    parser.add_argument("--input", type=Path, default=ROOT / "task3_auto_replies.json")
    parser.add_argument("--reference", type=Path, help="仅用于评分后的人工参考并列展示")
    parser.add_argument("--labels", type=Path, help="用于服务缺口一致性验证")
    parser.add_argument("--evidence", type=Path, help="有来源的业务证据，见 README")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    parser.add_argument("--no-reference", action="store_true", help="不载入默认人工参考和复核标签")
    parser.add_argument('--mode', choices=['rules', 'mock', 'llm'], default='mock')
    parser.add_argument('--issue-labels', type=Path)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    parser.add_argument('--pairwise', action='store_true', help='额外调用 LLM 做独立参考对比')
    args = parser.parse_args()
    bundled = args.input.resolve() == (ROOT / "task3_auto_replies.json").resolve()
    reference = args.reference or (ROOT / "task3_human_ref.json" if bundled and not args.no_reference else None)
    labels = args.labels or (ROOT / "data" / "review_labels.json" if bundled and not args.no_reference else None)
    issue_labels = args.issue_labels or (ROOT / 'data' / 'issue_labels.json' if bundled and not args.no_reference else None)
    try:
        load_env()
        if args.pairwise and (args.mode != 'llm' or reference is None):
            raise ValueError('--pairwise 需要 llm 模式和人工参考文件')
        payload = build(args.input, reference, labels, args.evidence, args.mode, issue_labels, args.config, args.pairwise)
        write_reports(payload, args.output)
    except (ValueError, OSError) as error:
        print(f"评估失败：{error}", file=sys.stderr)
        return 1
    s = payload["summary"]
    print(f"模式: {args.mode} / v{VERSION} / 本次 LLM 请求: {payload['metadata']['runtime_llm_calls']} / 缓存命中: {payload['metadata']['cache_hits']}")
    print(f"评估完成: {s['count']} 条 | 均分: {s['mean_score']} | 暂定: {s['provisional_count']} 条")
    print(f"服务缺口: {s['service_gap_count']} 条 | 待核实断言: {s['claim_statuses']['unverified']} 个")
    print(f"最差 3 条: {', '.join(s['worst_ids'])}")
    print(f"打开看板: {(args.output / 'index.html').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
