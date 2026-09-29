# v3 验证记录

环境：Windows，Python 3.12.4，Microsoft Edge。核心运行仅 Python 标准库；截图验证使用 Playwright 1.63.0。

## 已执行

- 实际调用 `qwen3.8-max-0902`，完成 v3 的20条主评分。独立参考对比模块未改动，复用 v2 已完成的20条真实对比缓存；主评分不读取人工参考。
- 新版最终报告完整缓存回放，重新生成 JSON、CSV、Markdown 和 HTML。元数据记录本次 API 请求0、缓存命中40，不能理解为从未实际调用模型。
- `python -m unittest discover -s tests -v`：41项通过，原始记录见 `test-results.txt`。
- `python -m compileall -q evaluation assistant_app run.py serve.py tests scripts`：通过。
- `python scripts/browser_check.py`：12 项浏览器行为检查通过，详见 `browser-check.json`。
- `python scripts/compare_rubrics.py`：10条构造样例分别实际调用两版提示词，共20份模型判断。v2通过33/34项断言，v3通过34/34项；记录见 `experiments/behavior-results.json`。
- `python scripts/summarize_versions.py`：确认两版使用同一份标签散列，生成可复核对照。没有修改旧标签来迎合新版。
- `python scripts/agent_smoke.py`：5轮真实工具 Agent 调用通过；涵盖已有结果分析导出、单案例解释、标签分歧、证据表单、新数据真实评估与导出。记录见 `experiments/agent-smoke.json`。
- `python scripts/agent_browser_check.py`：13项真实 Agent 网页检查通过，详见 `agent-browser-check.json`。
- 已查看桌面、实际工具回答和移动端截图，检查文字、卡片、布局和证据状态显示。

测试覆盖：未知事实不判错、支持/矛盾证据分流、无断言不奖励事实满分、否定请求、自查与追问的区分、合理自助、流程矛盾门控、LLM 原文证据校验、分数类型边界、模型不能自证事实、风险承诺复核、参考文件变更不影响主分数、id/顺序无关、无效输入、N/A 分母、未知标签掩码、HTML 注入转义、主裁判请求不含参考、全量 mock 链路、断言包含去重。

浏览器检查：20 条展示、真实模式标识、桌面无横向溢出、搜索、展开、空状态、风险筛选、分数排序、校准页、方法页、手机无横向溢出、无 JavaScript 错误。

新增 Agent 测试覆盖：受限工具与参数校验、未取证案例引用拦截、4/5误写纠正、无效动作拒绝及有限重试、结束动作协议、证据按会话隔离、精确原文匹配、上传清除旧结果、Host/来源令牌校验、禁止任意文件下载。浏览器使用真实模型解释评分、打开证据表单，填入醒目标识的构造测试来源验证重算，然后清空/恢复数据；测试证据不会写入正式报告。

## 真实运行发现与处理

1. 规则把“请您检查优惠券使用规则”误认为追问信息：收紧规则并加入回归测试。
2. 规则与 LLM 抽取的断言存在包含重复：按文本包含关系去重，公开句段级计数口径。
3. 最后一条参考对比首次返回结构不合协议：拒绝使用，重跑时仅请求失败条目，其他 39 个结果走缓存。
4. 多标签一致性一般、个别排序偏严：保留真实结果与分歧，不调标签制造高分；详见 `results-analysis.md`。
5. v3标准修订后误报15→6，但漏检8→12，共情正例检出降为0。该结果完整保留，不宣称全面提升。
6. Agent 工具链路首次通过后，人工阅读发现将4分制描述成“4/5”：补充工具分母、提示词和最终答复检测，再次真实验收。早期记录 `agent-smoke-before-output-check.json` 仅表示当时工具检查通过，不代表答复正确。
7. 真实模型在结束动作省略 brief：将无下一步时的展示字段设为可选；没有放宽执行工具参数或补造回答。其余结构失败有限重试，仍失败明确停止。
8. 补充证据或上传新数据后旧报告入口可能滞留：修改为失效并等待重新导出，新增浏览器检查。

## 截图

- `screenshots/01-overview.png`：真实结果总览。
- `screenshots/02-case-evidence.png`：换货案例的原文、评分、规则和模型依据、事实状态及独立对比。
- `screenshots/03-calibration.png`：多标签指标与分歧。
- `screenshots/04-method.png`：方法和运行边界。
- `screenshots/05-mobile.png`：手机布局。
- `screenshots/06-agent-home.png`：Agent 工作台与当前数据概览。
- `screenshots/07-agent-tools.png`：真实自然语言评分解释、工具轨迹和案例依据。
- `screenshots/08-agent-evidence.png`：业务证据录入表单。
- `screenshots/09-agent-mobile.png`：Agent 手机布局。

不声称已做的验证：独立人工盲标、外部业务事实验证、未见样本泛化、多模型稳定性对比、线上效果或定期调度运行。当前项目可通过 CLI 交给调度器运行，但没有创建实际自动化任务。

运行脚本需要本地 `.env`；纯工程测试无需网络。浏览器检查另需 Playwright 与 Edge，真实 Agent 检查会消耗模型调用额度。核心评估与服务不依赖 Playwright。
