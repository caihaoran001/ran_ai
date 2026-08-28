# 回复 Agent 检索边界提示词收敛：测试执行记录

时间：2026-08-28 14:48（北京时间）
基线提交：`dded3970bd484046bdba563e188201d3bce21926`
本次改动：将意图识别提示中的题面示例改为抽象边界；保留回复 Agent 的产品规则障碍答复规则。未调用真实模型。

## 1. 工作区与范围

- 改动文件：`backend/src/app/services/ai_service/client.py`、`backend/tests/test_02_chat.py`。
- 仅收敛措辞，不改变检索类别、业务题库或回复流程。
- 既有未跟踪资料未修改；未部署、未发送消息、未写生产业务数据。

## 2. 实际命令与结果

```bash
cd backend
UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests/test_02_chat.py tests/test_07_qiwe_channel.py -q
PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache uv run python -m compileall src scripts
UV_CACHE_DIR=/tmp/uv-cache PYTHONPYCACHEPREFIX=/tmp/ran_ai_pycache \
  uv run pytest tests -q
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check \
  src/app/services/ai_service/client.py tests/test_02_chat.py
```

| 项目 | 结果 | 状态 |
|---|---:|---|
| 聊天与 QiWe 相关回归 | 151/151 | 通过 |
| 后端 compileall | 完成 | 通过 |
| 后端全量 pytest | 1023/1023，41.63 秒 | 通过 |
| 改动源文件 Ruff | 通过 | 通过 |
| 改动测试文件 Ruff | 3 个既有 F401 未使用导入 | 未修复的既有问题 |
| 真实 LLM / Embedding / 消息通道 | 0 次 | 未执行（符合本次范围） |
| 费用与生产写入 | ¥0 / 0 | 通过 |

## 3. 回归断言

- 保留通用边界：当前问题依赖前文明确的产品/服务规则时，执行、替代或隐私障碍仍保留规则检索。
- 保留澄清边界：上下文无法确定对象或动作主题时输出空检索问题，不猜主题。
- 新增防回退断言：提示词不再包含“怎么弄/怎么办/这个呢”等具体题面示例。
- 提示词长度仍低于既有 4500 字上限。

## 4. 未覆盖与后续

- 本次未做真实模型回归，因此不能单凭本次本地测试证明所有模型输出均无回归。
- RA-030 的召回排序观察项仍未处理；后续如继续，只应针对召回链路做小范围修复和定向验证，不自动重跑 80 题。
