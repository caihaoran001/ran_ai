# 自动化测试

## 快速开始

```bash
cd backend
uv run pytest tests/ -v
```

## 目录结构

```
tests/
├── conftest.py              # 公共 fixtures：MockAIService、FastAPI test app、httpx client、数据库 session
├── fixtures/
│   ├── chat_scenarios.py    # 28天用户消息场景 + 打卡序列种子数据
│   └── llm_responses.json   # Mock LLM 响应数据
├── test_01_intake.py        # 入营链路：问卷提交 → 画像 + 两份交付文件
├── test_02_chat.py          # 聊天 + 风控：正常/高风险/退款/续购/情绪危机
├── test_03_checkin.py       # 打卡数据：单条创建、28天种子、解析、排序
├── test_04_stage_reports.py # 阶段报告自动生成：3→7→14→21→28天 + 毕业
├── test_05_evolution.py     # 知识进化：配置校验、种子数据模式、草稿列表
└── test_06_toggles.py       # 开关验证：发送策略、AI回复、服务单开关、软删除
```

## 测试架构

- **Mock LLM**：`MockAIService` 替换 `AIService`，所有 LLM 调用返回确定性数据，秒级完成
- **真实数据库**：使用项目 PostgreSQL，每个测试独立 session，自动回滚
- **FastAPI 测试客户端**：`httpx.AsyncClient` + `ASGITransport`，直接测试完整 HTTP 请求链路

## 链路覆盖

```
问卷提交 → 画像生成 + 文档生成
    ↓
聊天消息 → 风控识别（正常/高风险/退款/续购/情绪）
    ↓
打卡数据 → 28天模拟
    ↓
阶段报告 → 3→7→14→21→28天 + 毕业
    ↓
知识进化 → FAQ/案例草稿提取
    ↓
开关验证 → auto_send / ai_reply / evolution / soft_delete
```

## 纯函数测试

```bash
# intake_facts 确定性计算
uv run python -c "
from app.services.intake_facts import build_intake_fact_summary, _calculate_bmi, _weight_loss_band
assert _calculate_bmi(175, 65)  # → 21.2
assert _weight_loss_band(21.2) == '维持优化带（A带）'
assert _weight_loss_band(26.0) == '5斤带（B带）'
assert _weight_loss_band(30.0)  # → check
print('PASS')
"
```

## 常见问题

**Q: 测试报 `Future attached to a different loop`**
A: pytest-asyncio 事件循环作用域问题。已配置 `asyncio_default_test_loop_scope = "session"`，若仍有问题检查 pyproject.toml 配置。

**Q: 测试报 `duplicate key value violates unique constraint`**
A: 服务单的 `group_id` 有活跃唯一约束。使用随机 `group_id` 避免冲突，或先清理旧测试数据。

**Q: 如何只跑某个模块的测试**
```bash
uv run pytest tests/test_01_intake.py -v
uv run pytest tests/ -v -k "chat"
```
