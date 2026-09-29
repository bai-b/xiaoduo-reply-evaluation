"""No references, case IDs, external network or fitted labels enter scoring."""
from __future__ import annotations

import re
from typing import Any

WEIGHTS = {"relevance": 25, "usefulness": 35, "tone": 15, "grounding": 25}
NAMES = {"relevance": "切题与一致性", "usefulness": "问题解决有效性", "tone": "语气与情境关怀", "grounding": "事实依据"}

# Ordered intent rules: specific situations precede broader after-sales intents.
INTENTS = [
    ("操作困难", r"流程.*复杂|不知道怎么操作|搞不懂|不会操作", r"退货|操作|步骤|环节"),
    ("重复故障", r"上次.*这次|又是坏|连续.*坏", r"质量|退货|退款|故障|坏"),
    ("换货", r"换尺码|换货|一件大.*一件小", r"尺码|换货|退货|商品"),
    ("取消订单", r"取消|没发货", r"取消|订单|发货"),
    ("退款进度", r"退款.*到账|退款.*时候|退款进度", r"退款|到账|订单"),
    ("取件困难", r"快递柜|取不出来", r"快递|取件|派送"),
    ("物流异常", r"快递.*没更新|物流.*更新", r"物流|快递|包裹"),
    ("账户安全", r"异地登录|盗号|诈骗短信", r"登录|账号|短信|密码"),
    ("优惠券", r"优惠券", r"优惠券|使用条件|门槛"),
    ("商品成分", r"成分|皮肤敏感", r"成分|过敏|面膜"),
    ("商品材质", r"材质|硅胶|塑料", r"材质|硅胶|塑料|TPU|软胶"),
    ("出行适用性", r"飞机|航空", r"飞机|航空|Wh|容量|充电宝"),
    ("补货", r"补货", r"补货|到货|库存"),
    ("商品对比", r"手机.*哪个好|两款.*哪个好|对比", r"手机|预算|需求|对比|推荐"),
    ("功能建议", r"加个功能|建议.*功能|实物视频", r"建议|反馈|视频|产品团队"),
    ("服务投诉", r"客服态度|没人理|等了.*分钟", r"抱歉|久等|问题|客服"),
    ("退货运费", r"邮费|运费", r"邮费|运费|承担|退货"),
    ("产品故障", r"没声音|不工作|坏了|故障", r"耳机|机器人|故障|退货|换新|维修|退款"),
    ("多事项进度", r"退货.*快递|快递.*退货", r"退货|快递|订单|物流"),
]

def has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.I) is not None

def excerpt(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.I)
    return match.group(0) if match else text[:100]

def valid_request(text: str, objects: str) -> bool:
    # Positive requests only: a negated mention of an order ID is not clarification.
    for sentence in re.split(r"[。！？!?；;\n]", text):
        if has(r"不(?:需要|用|必)|无需|别提供|不要提供", sentence):
            continue
        if has(r"请(?:问|提供|告诉)|(?:请您|您能|能否|方便|可以).{0,8}(?:提供|告诉|发给|发来|描述|说明)|哪|什么|多少", sentence) and has(objects, sentence):
            return True
    return False

def extract_claims(reply: str, evidence: dict[str, dict]) -> list[dict]:
    patterns = [
        ("政策与时效", r"\d+(?:\s*[-—至]\s*\d+)?\s*(?:个)?(?:工作日|天|Wh|mAh)|质保期|运费.{0,10}承担"),
        ("商品属性", r"TPU|采用.{0,15}材质|不含酒精|不含香精|主要成分"),
        ("平台功能", r"开启二次验证|自动提醒|已支持.{0,6}视频|冻结账号"),
        ("服务承诺", r"已经退款|已为您退款|保证.{0,8}成功|补偿.{0,8}\d+元|补偿优惠券|\d+元.{0,8}优惠券|加强.{0,8}培训|反馈给品控|转达给产品"),
    ]
    claims = []
    for part in re.split(r"[。！!？?；;\n]", reply):
        quote = part.strip()
        if not quote:
            continue
        kinds = [name for name, pattern in patterns if has(pattern, quote)]
        if not kinds:
            continue
        record = evidence.get(quote)
        claims.append({"quote": quote, "types": kinds,
                       "status": record["status"] if record else "unverified",
                       "source": record["source"] if record else None,
                       "rationale": record["rationale"] if record else "未提供与此断言对应的业务证据；不能据此认定内容错误。"})
    return claims

def evaluate(row: dict, evidence: dict[str, dict] | None = None) -> dict[str, Any]:
    question, reply = row["user_question"], row["auto_reply"]
    intent, topic = next(((name, rp) for name, qp, rp in INTENTS if has(qp, question)), ("未覆盖场景", ""))
    scores = {"relevance": 4, "usefulness": 4, "tone": 4, "grounding": None}
    findings: list[dict] = []

    def deduct(dimension: str, points: int, rule: str, quote: str, reason: str, suggestion: str, origin: str = "reply"):
        scores[dimension] = max(0, scores[dimension] - points)
        findings.append({"dimension": dimension, "rule": rule, "quote": quote,
                         "origin": origin, "deduction": points, "reason": reason, "suggestion": suggestion})

    off_topic = bool(topic and not has(topic, reply))
    if off_topic:
        deduct("relevance", 4, "R01", reply, "未命中用户诉求对应的主题；规则可能漏掉同义表达。", "先直接回应用户的问题，再提供对应操作或追问。")
        deduct("usefulness", 4, "U01", reply, "未识别到与当前问题相关的解决路径。", "围绕当前诉求给出具体下一步。")
    elif intent == "未覆盖场景":
        deduct("relevance", 1, "R02", question, "场景不在基线覆盖范围，无法充分评价切题性。", "转交语义裁判或人工复核。", "question")

    contradiction = (has(r"先退货再重新下单", reply) and has(r"收到后.*发出|收到后.*换新", reply))
    if contradiction:
        deduct("relevance", 2, "R03", reply, "同时要求退货后重新下单，又承诺收到退件后发出换货，流程相互混淆。", "明确选择退货重购或换货其中一种流程，并核实政策。")

    required = {
        "取件困难": r"订单|运单|单号|取件|柜号",
        "退款进度": r"订单|单号|退款方式|退款时间",
        "物流异常": r"订单|运单|单号",
        "优惠券": r"券编号|订单|单号|优惠券.*(?:截图|规则)|截图",
        "账户安全": r"账号|登录记录|提醒.*截图",
        "商品成分": r"哪款|商品|链接|产品|名称",
        "出行适用性": r"哪款|型号|商品|容量|额定|参数|链接",
        "补货": r"哪款|商品|链接|名称|型号",
        "换货": r"哪两件|商品|尺码|订单",
    }
    if not off_topic and intent in required and not valid_request(reply, required[intent]):
        direct_specific = ((intent == "商品成分" and has(r"成分(?:为|是)|主要成分", reply)) or
                           (intent == "出行适用性" and has(r"这款.*\d+.*(?:Wh|mAh)", reply)))
        if not direct_specific:
            deduct("usefulness", 2, "U02", question, "这是需要具体对象或状态的信息请求，回复未请求必要信息，主要提供通用说明。", "在授权流程内收集最少必要的订单/商品信息，再查询；无查询能力时说明限制并给出明确入口。", "question")

    if not off_topic and intent == "操作困难" and not valid_request(reply, r"环节|哪一步|哪里|步骤|截图"):
        deduct("usefulness", 3, "U03", reply, "用户已经不会操作，回复继续重复完整流程，未先定位卡点。", "先询问卡在哪一步，再分步指导或转接人工。")
    # A conditional invitation to send a screenshot is weaker than locating the stuck step.
    elif not off_topic and intent == "操作困难" and not has(r"请问.*(?:环节|哪一步|哪里)|卡在(?:哪|什么)|哪一步.*(?:问题|不会)", reply):
        deduct("usefulness", 2, "U04", reply, "虽然提供了截图求助渠道，但没有主动定位操作卡点，仍以重复流程为主。", "先询问具体卡点，再给对应步骤。")

    if not off_topic and intent == "产品故障" and has(r"才买|刚买", question) and has(r"建议您尝试|尝试以下", reply) and has(r"如果问题仍然", reply):
        deduct("usefulness", 2, "U05", excerpt(r"建议您尝试.*?如果问题仍然存在", reply), "将多项自助排查放在售后选择之前，可能增加刚购买即故障用户的操作负担。", "先说明可核实的售后选择，由用户决定是否尝试简短排查。")
    if not off_topic and intent == "物流异常" and has(r"耐心等待\d", reply):
        deduct("usefulness", 1, "U06", excerpt(r"耐心等待[^，。]*", reply), "已停更后继续建议等待，未提供当前异常核查行动。", "先核查物流节点与承运商承诺时效，再决定是否等待或催办。")
    if not off_topic and intent == "商品成分" and has(r"购买前|先购买小样", reply) and has(r"我买的", question):
        deduct("usefulness", 1, "U07", excerpt(r"购买前[^。]*", reply), "用户已购买并请求成分表，回复偏向购买前建议，未满足当前资料需求。", "先确认具体商品并提供有来源的完整成分表，不作未经证实的安全保证。")
    if not off_topic and scores["usefulness"] == 4 and intent not in ("功能建议", "商品材质", "退货运费") and not has(r"请|建议|可以|能否|方便|告诉|订单|步骤|处理|查看|查询", reply):
        deduct("usefulness", 2, "U08", reply, "未识别到清晰的下一步或信息请求。", "提供与诉求相符的可执行下一步。")

    if has(r"烦死|活该|蠢|关我什么事|别来烦", reply):
        deduct("tone", 4, "T01", excerpt(r"烦死|活该|蠢|关我什么事|别来烦", reply), "存在不尊重用户的表达。", "改用尊重、明确且可执行的表达。")
    elif has(r"太差|没人理|坏的|不工作|没声音|复杂|困扰|取不出来", question) and not has(r"抱歉|对不起|理解|别担心|不便|困扰|确实|帮您", reply):
        deduct("tone", 1, "T02", question, "用户存在困难或不满，未识别到回应情绪或主动协助的表达。", "简短承认用户的困扰，并紧接解决步骤。", "question")
    if intent == "账户安全" and not has(r"别担心|不要担心|理解|帮您|保障|保护", reply):
        deduct("tone", 1, "T03", reply, "提供了安全建议，但缺少对账户风险担忧的安抚。", "在保留不要点击可疑链接等建议的同时，说明可通过官方渠道核实。")

    claims = extract_claims(reply, evidence or {})
    statuses = [claim["status"] for claim in claims]
    if "contradicted" in statuses:
        scores["grounding"] = 0
        grounding_state = "存在证据矛盾"
    elif "unverified" in statuses:
        grounding_state = "待核实"
    elif claims:
        scores["grounding"] = 4
        grounding_state = "已支持"
    else:
        grounding_state = "未检出可核查断言"
    known = {k: v for k, v in scores.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in known)
    total = round(sum(WEIGHTS[k] * v / 4 for k, v in known.items()) / weight * 100, 1)
    capped = contradiction or "contradicted" in statuses
    if capped:
        total = min(total, 49.0)
    suggestions = list(dict.fromkeys(f["suggestion"] for f in findings))
    if "unverified" in statuses:
        suggestions.append("将待核实断言关联到真实政策、商品参数或工具记录；核实前避免作确定性承诺。")
    return {**row, "intent": intent, "scores": scores, "total_score": total,
            "provisional": scores["grounding"] is None, "assessed_weight": weight,
            "score_capped": capped, "service_gap": scores["usefulness"] <= 2,
            "grounding_state": grounding_state, "claims": claims,
            "findings": findings, "suggestions": suggestions or ["保留当前表达，并在真实业务场景核实工具能力及事实来源。"],
            "rule_coverage": "uncovered" if intent == "未覆盖场景" else "covered"}
