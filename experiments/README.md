# 实验材料说明

- `v2-results.json`、`judge-v2.md`、`v2-analysis.md`：改进前冻结结果、提示词及分析；对应旧代码提交 `b8bf47c`。
- `version-comparison.json`：相同标签的散列校验、两版指标和逐条变化；由 `python scripts/summarize_versions.py` 生成。
- `behavior-results.json`：10条预先构造样例、两版共20份真实模型输出及34项行为断言。不是独立真实业务盲测。
- `agent-smoke.json`：当前版本5轮真实 Agent 工具调用与答复；包含对新数据执行评估。报告链接属于运行时本地会话，重启后不保证可打开。
- `agent-smoke-before-output-check.json`：早期验收记录，仅验证了工具链路。文件中的 `all_checks_passed` 只指当时的工具检查，不能证明解释正确；人工检查随后发现4分制被说成4/5。该记录留作失败分析，不是最终验收结果。

所有样本已经参与开发观察，标签由已有人工注释归纳且包含主观性。请结合 `docs/results-analysis.md` 阅读，不能把这些实验当上线收益或未见数据泛化证明。模型缓存、密钥和新上传的业务数据均不在本目录发布。
