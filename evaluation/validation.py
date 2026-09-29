"""Retrospective service-gap agreement; never used by the evaluator."""
from __future__ import annotations


def safe_ratio(a, b):
    return round(a / b, 4) if b else None


def validate_labels(results: list[dict], labels: list[dict]) -> dict:
    by_id = {r["id"]: r for r in results}
    counts = dict(tp=0, fp=0, fn=0, tn=0)
    excluded, details = [], []
    for label in labels:
        case_id = label["id"]
        if case_id not in by_id:
            raise ValueError(f"复核标签引用不存在的 id: {case_id}")
        gold = label["service_gap"]
        if gold is None:
            excluded.append(label)
            continue
        predicted = by_id[case_id]["service_gap"]
        outcome = "tp" if gold and predicted else "fn" if gold else "fp" if predicted else "tn"
        counts[outcome] += 1
        details.append({**label, "predicted": predicted, "outcome": outcome})
    tp, fp, fn, tn = (counts[k] for k in ("tp", "fp", "fn", "tn"))
    return {"scope": "服务缺口与回顾性整理标签的一致性；不属于独立盲测或事实核验准确率。",
            "counts": counts, "included": len(details), "excluded": excluded,
            "unlabeled_ids": sorted(set(by_id) - {x["id"] for x in labels}),
            "precision": safe_ratio(tp, tp + fp), "recall": safe_ratio(tp, tp + fn),
            "f1": safe_ratio(2 * tp, 2 * tp + fp + fn), "agreement": safe_ratio(tp + tn, len(details)),
            "details": details}


def validate_issues(results, labels, tags):
    by_id = {r["id"]: r for r in results}
    stats = {tag: dict(tp=0, fp=0, fn=0, tn=0, excluded=0) for tag in tags}
    disagreements = []
    for row in labels:
        if row["id"] not in by_id:
            raise ValueError(f"多标签复核 id 不存在：{row['id']}")
        if not isinstance(row.get("positive"), list) or not isinstance(row.get("unknown"), list):
            raise ValueError("positive / unknown 必须为数组")
        if set(row["positive"] + row["unknown"]) - set(tags) or set(row["positive"]) & set(row["unknown"]):
            raise ValueError("多标签名称无效或正例与未知相互冲突")
        predicted = set(by_id[row["id"]]["issue_tags"])
        for tag in tags:
            if tag in row["unknown"]:
                stats[tag]["excluded"] += 1
                continue
            gold, pred = tag in row["positive"], tag in predicted
            category = "tp" if gold and pred else "fn" if gold else "fp" if pred else "tn"
            stats[tag][category] += 1
            if category in ("fp", "fn"):
                disagreements.append({"id": row["id"], "tag": tag, "kind": category, "basis": row["basis"]})
    def metrics(c):
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        return {**c, "precision": safe_ratio(tp, tp + fp), "recall": safe_ratio(tp, tp + fn),
                "f1": safe_ratio(2 * tp, 2 * tp + fp + fn), "support": tp + fn}
    per_tag = {tag: metrics(c) for tag, c in stats.items()}
    micro = metrics({key: sum(s[key] for s in stats.values()) for key in ("tp", "fp", "fn", "tn", "excluded")})
    defined = [s["f1"] for s in per_tag.values() if s["f1"] is not None]
    return {"scope": "回顾性多标签校准；未知标签对从分母排除；未列为 positive/unknown 的标签视为负例。",
            "per_tag": per_tag, "micro": micro, "macro_f1": round(sum(defined) / len(defined), 4) if defined else None,
            "macro_defined_labels": len(defined), "disagreements": disagreements,
            "unlabeled_ids": sorted(set(by_id) - {x["id"] for x in labels})}
