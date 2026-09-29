from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .core import has

ROOT = Path(__file__).resolve().parent.parent
TAGS = ("passive_service", "generic_answer", "missing_clarification", "insufficient_empathy", "intent_mismatch")
RULE_TAGS = {"U01": ["intent_mismatch"], "U02": ["generic_answer", "missing_clarification", "passive_service"],
             "U03": ["intent_mismatch"], "U04": ["intent_mismatch"], "U05": ["passive_service"],
             "U06": ["passive_service"], "U07": ["intent_mismatch"], "T02": ["insufficient_empathy"], "T03": ["insufficient_empathy"]}


def mock_judge(baseline):
    issues = []
    seen = set()
    for finding in baseline["findings"]:
        for tag in RULE_TAGS.get(finding["rule"], []):
            if tag not in seen:
                issues.append({"tag": tag, **{k: finding[k] for k in ("origin", "quote", "reason", "suggestion")}})
                seen.add(tag)
    return {"scores": dict(baseline["scores"]),
            "rationales": {k: "动态 mock：依据规则命中生成，未调用真实语义模型。" for k in baseline["scores"]},
            "issues": issues, "additional_claims": []}


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_judgment(data, row):
    if not isinstance(data, dict) or set(data) != {"scores", "rationales", "issues", "additional_claims"}:
        raise ValueError("语义输出字段不符合协议")
    keys = {"relevance", "usefulness", "tone", "grounding"}
    if not isinstance(data["scores"], dict) or set(data["scores"]) != keys:
        raise ValueError("语义评分维度缺失")
    for key, score in data["scores"].items():
        if key == "grounding" and score is None:
            continue
        if type(score) is not int or not 0 <= score <= 4:
            raise ValueError("语义分数必须为 0–4 整数（事实依据允许 null）")
    if not isinstance(data["rationales"], dict) or set(data["rationales"]) != keys or not all(nonempty(x) for x in data["rationales"].values()):
        raise ValueError("每个评分维度必须提供理由")
    if not isinstance(data["issues"], list) or not isinstance(data["additional_claims"], list):
        raise ValueError("issues 和 additional_claims 必须为数组")
    for issue in data["issues"]:
        if not isinstance(issue, dict) or set(issue) != {"tag", "origin", "quote", "reason", "suggestion"}:
            raise ValueError("问题标签字段不合法")
        if issue["tag"] not in TAGS or issue["origin"] not in ("question", "reply"):
            raise ValueError("问题标签或原文来源不合法")
        text = row["user_question"] if issue["origin"] == "question" else row["auto_reply"]
        if not all(nonempty(issue[k]) for k in ("quote", "reason", "suggestion")) or issue["quote"] not in text:
            raise ValueError("模型证据不是输入原文片段")
    for claim in data["additional_claims"]:
        if not isinstance(claim, dict) or set(claim) != {"quote", "reason"} or not all(nonempty(v) for v in claim.values()) or claim["quote"] not in row["auto_reply"]:
            raise ValueError("模型补充断言缺少原文证据")
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("模型接口发生重定向，请直接配置正确服务地址")


class LLMClient:
    def __init__(self, cache_dir: Path | None = None):
        self.base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
        self.model = os.getenv("LLM_MODEL", "")
        self.key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("LLM_API_KEY")
        if not self.base_url or not self.model or not self.key:
            raise ValueError("真实模式需要 LLM_BASE_URL、LLM_MODEL 和 DASHSCOPE_API_KEY（或 LLM_API_KEY）")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("模型服务地址必须为无凭据、无查询参数的 HTTPS URL")
        self.cache_dir = cache_dir
        self.calls = 0
        self.cache_hits = 0
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self.extra = json.loads(os.getenv("LLM_EXTRA_BODY", '{"enable_thinking": false}'))
        if not isinstance(self.extra, dict) or set(self.extra) - {"enable_thinking"}:
            raise ValueError("LLM_EXTRA_BODY 当前只允许 enable_thinking")

    def request(self, prompt, payload, validator):
        request_data = {"model": self.model, "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                        "response_format": {"type": "json_object"}, "temperature": 0, **self.extra}
        raw = json.dumps(request_data, ensure_ascii=False).encode("utf-8")
        key = hashlib.sha256(self.base_url.encode() + raw).hexdigest()
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path and path.exists():
            result = validator(json.loads(path.read_text(encoding="utf-8")))
            self.cache_hits += 1
            return result
        opener = urllib.request.build_opener(NoRedirect)
        for attempt in range(3):
            request = urllib.request.Request(self.base_url + "/chat/completions", data=raw,
                       headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}, method="POST")
            self.calls += 1
            try:
                with opener.open(request, timeout=50) as response:
                    body = json.load(response)
                break
            except urllib.error.HTTPError as error:
                if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise ValueError(f"模型接口 HTTP {error.code}；检查地址、模型及配额。未自动降级，服务端正文未写入日志。") from None
            except (urllib.error.URLError, TimeoutError):
                raise ValueError("模型接口连接失败或超时，未自动降级；可使用缓存重试。") from None
        try:
            choice = body["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("模型输出未完整结束，拒绝使用截断结果")
            parsed_content = json.loads(choice["message"]["content"])
            try:
                result = validator(parsed_content)
            except ValueError:
                if self.cache_dir:
                    error_path = self.cache_dir.parent / 'errors' / f'{key}.json'
                    error_path.parent.mkdir(parents=True, exist_ok=True)
                    error_path.write_text(json.dumps(parsed_content, ensure_ascii=False, indent=2), encoding='utf-8')
                raise
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise ValueError("模型响应不是完整的合法结构化结果，未自动降级") from None
        for field in self.usage:
            value = (body.get("usage") or {}).get(field, 0)
            if type(value) is int and value >= 0:
                self.usage[field] += value
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def judge(self, row, baseline):
        prompt = (ROOT / "prompts" / "judge.md").read_text(encoding="utf-8")
        return self.request(prompt, {"question": row["user_question"], "reply": row["auto_reply"], "claims": baseline["claims"]},
                            lambda data: validate_judgment(data, row))


def fuse(baseline, semantic, config, evidence):
    result = {**baseline, "baseline_scores": dict(baseline["scores"]), "semantic": semantic,
              "scores": dict(semantic["scores"]), "claims": [dict(c) for c in baseline["claims"]]}
    for claim in semantic["additional_claims"]:
        if any(c["quote"] in claim["quote"] or claim["quote"] in c["quote"] for c in result["claims"]):
            continue
        proof = evidence.get(claim["quote"])
        result["claims"].append({"quote": claim["quote"], "types": ["语义补充"],
                                 "status": proof["status"] if proof else "unverified",
                                 "source": proof["source"] if proof else None,
                                 "rationale": proof["rationale"] if proof else claim["reason"]})
    statuses = [c["status"] for c in result["claims"]]
    # Only evidence can assert factual support/contradiction; never a model's opinion.
    grounding = 0 if "contradicted" in statuses else None if "unverified" in statuses or not statuses else 4
    result["scores"]["grounding"] = grounding
    result["grounding_state"] = "存在证据矛盾" if grounding == 0 else "已支持" if grounding == 4 else "待核实" if statuses else "未检出可核查断言"
    result["fusion_actions"] = []
    if grounding != semantic["scores"]["grounding"]:
        result["fusion_actions"].append("按业务证据状态覆盖模型事实分，禁止模型自证事实正确。")
    if any(f["rule"] == "R03" for f in baseline["findings"]):
        result["scores"]["relevance"] = min(2, result["scores"]["relevance"])
        result["fusion_actions"].append("明确流程冲突：切题与一致性最多 2 分，触发硬门控。")
    if any(f["rule"] == "T01" for f in baseline["findings"]):
        result["scores"]["tone"] = 0
        result["fusion_actions"].append("命中攻击性表达，语气维度归零。")
    weights = config["weights"]
    known = {k: v for k, v in result["scores"].items() if v is not None}
    denominator = sum(weights[k] for k in known)
    total = round(sum(weights[k] * v / 4 for k, v in known.items()) / denominator * 100, 1)
    result["score_capped"] = baseline["score_capped"] or grounding == 0
    result["total_score"] = min(config["hard_gate_cap"], total) if result["score_capped"] else total
    result["provisional"] = grounding is None
    result["assessed_weight"] = denominator
    result["service_gap"] = result["scores"]["usefulness"] <= config["service_gap_max_score"]
    result["issue_tags"] = sorted({issue["tag"] for issue in semantic["issues"]})
    risk_quotes = [c["quote"] for c in result["claims"] if c["status"] == "unverified" and
                   has(r"已经退款|已为您退款|保证.{0,8}成功|补偿.{0,8}\d+元|\d+元.{0,8}优惠券|补偿优惠券|质保期内|运费由我们承担", c["quote"])]
    result["risk_quotes"] = risk_quotes
    result["quality_status"] = "高风险待复核" if result["score_capped"] or risk_quotes else "需要改进" if result["total_score"] < config["acceptable_threshold"] or result["service_gap"] else "可接受（暂定）" if result["provisional"] else "可接受"
    result["suggestions"] = list(dict.fromkeys([i["suggestion"] for i in semantic["issues"]] + baseline["suggestions"]))
    return result
