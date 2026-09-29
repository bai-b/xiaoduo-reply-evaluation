from __future__ import annotations

from .core import extract_claims, has, valid_request
from .semantic import ROOT, nonempty

FIELDS = ("auto_covered", "reference_added", "key_gaps", "improvements", "reference_caveats")


def validate_comparison(data):
    if not isinstance(data, dict) or set(data) != set(FIELDS):
        raise ValueError("对比输出字段不符合协议，必须精确包含 auto_covered/reference_added/key_gaps/improvements/reference_caveats")
    if any(not isinstance(data[k], list) or not all(nonempty(v) for v in data[k]) for k in FIELDS):
        raise ValueError("对比输出应为字符串数组")
    return data


def compare(result, reference, client=None):
    if client:
        prompt = (ROOT / "prompts" / "compare.md").read_text(encoding="utf-8")
        data = client.request(prompt, {"question": result["user_question"], "reply": result["auto_reply"],
                                      "human_reference": reference["human_reference"], "annotator_notes": reference["annotator_notes"]}, validate_comparison)
        return {"mode": "llm_pairwise", **data}
    reply, ref = result["auto_reply"], reference["human_reference"]
    auto, added = [], []
    if has(r"抱歉|感谢|您好", reply):
        auto.append("已包含礼貌、感谢或道歉表达；关键词命中不代表共情已充分。")
    if has(r"可以|请|建议|步骤", reply):
        auto.append("已提供信息或行动建议，需要结合当前诉求判断是否有效。")
    if valid_request(ref, r"订单|账号|商品|哪|尺码|环节") and not valid_request(reply, r"订单|账号|商品|哪|尺码|环节"):
        added.append("参考更明确地请求了对象或处理所需的信息。")
    if has(r"帮您|我帮|我来", ref) and not has(r"帮您|我帮|我来", reply):
        added.append("参考表达了主动协助；仍需核实实际工具权限。")
    caveats = ["该模块是离线规则差异摘要，不是 LLM 成对判断；不参与主评分或校准指标。"]
    if extract_claims(ref, {}):
        caveats.append("参考包含具体事实或服务承诺，附件缺少业务证据，不能直接当作事实金标准。")
    if "XX" in ref:
        caveats.append("参考含 XX 占位符，不能作为可交付的完整成分表。")
    return {"mode": "offline_comparison", "auto_covered": auto or ["需人工进一步识别原回复的有效覆盖。"],
            "reference_added": added or ["规则未检出明确新增服务行为；不代表两者无语义差异。"],
            "key_gaps": list(dict.fromkeys(f["reason"] for f in result["findings"])) or ["规则未检出显著缺口，仍需语义复核。"],
            "improvements": result["suggestions"], "reference_caveats": caveats}
