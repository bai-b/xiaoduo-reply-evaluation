"""Run predeclared development probes once per prompt, and report every result."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from run import load_env, load_config
from evaluation.core import evaluate
from evaluation.semantic import LLMClient, validate_judgment, fuse


def check(result, expected):
    assertions = []
    for dim, value in expected.get('min_scores', {}).items():
        assertions.append((f'{dim}>={value}', result['scores'][dim] >= value))
    for dim, value in expected.get('max_scores', {}).items():
        assertions.append((f'{dim}<={value}', result['scores'][dim] <= value))
    for tag in expected.get('required_tags', []):
        assertions.append((f'requires {tag}', tag in result['issue_tags']))
    for tag in expected.get('forbidden_tags', []):
        assertions.append((f'excludes {tag}', tag not in result['issue_tags']))
    if expected.get('grounding_null'):
        assertions.append(('grounding is N/A', result['scores']['grounding'] is None))
    if expected.get('review_required'):
        assertions.append(('manual review required', result['quality_status'] == '高风险待复核'))
    return [{'criterion': name, 'passed': bool(ok)} for name, ok in assertions]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    load_env()
    client = LLMClient(ROOT/'.cache/llm')
    rows = json.loads((ROOT/'data/behavior_cases.json').read_text(encoding='utf-8'))
    config = load_config(ROOT/'config.json')
    records = []
    for version, path in [('v2', ROOT/'experiments/judge-v2.md'), ('v3', ROOT/'prompts/judge.md')]:
        prompt = path.read_text(encoding='utf-8')
        for r in rows:
            baseline = evaluate(r)
            sem = client.request(prompt, {'question':r['user_question'], 'reply':r['auto_reply'], 'claims':baseline['claims']}, lambda x: validate_judgment(x, r))
            result = fuse(baseline, sem, config, {})
            records.append({'version':version, 'id':r['id'], 'purpose':r['purpose'], 'checks':check(result,r['expect']), 'result':result})
            print(version, r['id'], flush=True)
    summary = {}
    for version in ('v2','v3'):
        selected = [x for x in records if x['version'] == version]
        assertions = [c for r in selected for c in r['checks']]
        summary[version] = {'passed_assertions':sum(c['passed'] for c in assertions), 'assertions':len(assertions),
                            'passed_cases':sum(all(c['passed'] for c in r['checks']) for r in selected), 'cases':len(selected)}
    report = {'scope':'AI 辅助构造开发行为测试，非真实业务盲测；不用于调分挑最优结果。','summary':summary,'records':records}
    (ROOT/'experiments/behavior-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
