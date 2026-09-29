# v3 评价标准改进结果

## 结论

新版减少了过度标注，但没有全面解决漏检。不能用质量均分升高证明评估器更准确。

| 指标 | v2 | v3 |
|---|---:|---:|
| Micro Precision | 0.5946 | 0.7500 |
| Micro Recall | 0.7333 | 0.6000 |
| Micro F1 | 0.6567 | 0.6667 |
| Macro F1 | 0.4576 | 0.5826 |
| FP / FN | 15 / 8 | 6 / 12 |
| 二元服务缺口 F1 | 0.6667 | 0.6667 |
| 构造行为断言通过 | 33/34 | 34/34 |
| 回复暂定均分（非准确率） | 70.9 | 79.8 |

同样的20条数据、同样的冻结复核标签、同一模型 qwen3.8-max-0902。标签文件散列校验一致；没有在看到结果后改金标准。v2原始结果与提示词保留于 experiments，旧代码位于提交 b8bf47c。

## 改进发生在哪里

- case_16 从55分调整到88.3分：承认预算和场景澄清已经有效，只将缺少具体型号视为轻微追问缺口。
- case_20 新识别 intent_mismatch：用户已卡在流程，再重复流程不能充分解决。
- 普通咨询不再普遍要求道歉，共情标签误报从5个降到1个。
- 每条真实结果增加服务任务、情绪原文、下一步状态与问题严重程度，给人工复核更多明确依据。

## 仍未解决的问题

共情正例检出从1个降到0个，不能只讲误报下降。新版对缺少情绪强度、主动服务和具体状态核实仍存在漏判；附带的安全建议、自助入口会让模型高估处理充分性。
case_18 仍被偏严评价；部分输出把下一步标为轻微缺口、有效性却给2分。程序将这种自相矛盾加入复核列表，不静默改分。
现有标签也有主观性和不完备性，FP/FN只是相对这些标签的分歧。两轮都是已见样本上的开发比较，不是独立盲测。下一步需要人工复核共情/服务推进标签，并在新增业务样本上验证。

## 行为测试

10条预先声明的AI辅助构造样例，覆盖中性语气、明确自助、流程卡点、合理澄清、侮辱、未知承诺和回复内指令干扰。v2通过9/10条，v3通过10/10条。完整输出与失败项均在 experiments/behavior-results.json，不属于真实业务泛化证明。

## Agent 的角色

Agent调度这些确定的评估工具，帮助查询、解释、筛选、复核、补证据和导出；不会因为增加聊天界面就自动提高裁判准确性。所有事实支持状态仍依赖外部维护的证据。

## 逐条变化

| ID | v2分数 | v3分数 | v3标签 |
|---|---:|---:|---|
| case_01 | 63.3 | 88.3 | missing_clarification |
| case_02 | 68.3 | 88.3 | missing_clarification |
| case_03 | 75.0 | 88.3 | missing_clarification |
| case_04 | 88.3 | 88.3 | missing_clarification |
| case_05 | 88.3 | 88.3 | 未检出 |
| case_06 | 63.3 | 68.3 | generic_answer, missing_clarification |
| case_07 | 88.3 | 88.3 | missing_clarification |
| case_08 | 80.0 | 100.0 | 未检出 |
| case_09 | 63.3 | 88.3 | 未检出 |
| case_10 | 88.3 | 100.0 | 未检出 |
| case_11 | 49 | 49 | generic_answer, missing_clarification |
| case_12 | 63.3 | 68.3 | generic_answer, missing_clarification, passive_service |
| case_13 | 63.3 | 63.3 | missing_clarification, passive_service |
| case_14 | 88.3 | 88.3 | 未检出 |
| case_15 | 68.3 | 68.3 | generic_answer, missing_clarification |
| case_16 | 55.0 | 88.3 | missing_clarification |
| case_17 | 63.3 | 88.3 | missing_clarification |
| case_18 | 63.3 | 63.3 | generic_answer, missing_clarification |
| case_19 | 63.3 | 68.3 | generic_answer, missing_clarification |
| case_20 | 75.0 | 63.3 | insufficient_empathy, intent_mismatch, missing_clarification |
