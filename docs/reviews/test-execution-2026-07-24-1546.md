# 测试执行记录：QiWe 启动时 UNKNOWN 出站对账

- 执行时间：2026-07-24 15:46（Asia/Shanghai）
- 实现提交：`9d5353979dff1e9b45cc533656c6b05dc1c005da`
- 部署环境：生产 ECS + 阿里云 RDS
- 真实渠道授权：用户明确指定蔡浩然服务单群为专用实验群，并授权本次真实发送、重启和对账验证

## 自动化测试

| 范围 | 状态 | 结果 |
| --- | --- | --- |
| QiWe 通道与启动对账聚焦测试 | 通过 | `59 passed in 2.47s` |
| 聊天、QiWe、checkpoint 与新对账回归 | 通过 | `99 passed in 3.26s` |
| 后端全量 Mock 测试 | 通过 | `429 passed in 13.21s` |
| Python 语法检查 | 通过 | `main.py`、QiWe 通道、engine、对账模块与测试文件均通过 `py_compile` |
| 本地后端重启 | 通过 | `docker restart ran-ai-backend` 后 `GET /health` 返回 `{"status":"ok"}` |

覆盖场景包括：

- 启动瞬间冻结固定消息 ID。
- 服务恢复后新产生的 `SENDING` 不在冻结名单中，状态保持不变。
- 无冻结消息时不调用 QiWe 历史接口。
- 唯一、一对一精确匹配后修正为 `SENT`。
- 错误发送者、错误群、正文不完全一致、超出 3 分钟窗口均不匹配。
- 零匹配、多匹配、同一历史消息被两条 UNKNOWN 竞争、接口失败和缺少起始 `seq` 时均保留 `UNKNOWN`。
- 历史查询固定单页 `limit=50`。

## 生产部署

- `DEPLOYED_VERSION`：`9d5353979dff1e9b45cc533656c6b05dc1c005da`
- 前端本地预构建：通过。
- 生产 checkpoint 初始化：通过。
- Docker Compose 重建：通过。
- 实测部署停机：111.23 秒。
- 部署后公网 `GET /health`：通过。

## 真实重启对账

安全目标经过双重校验：

- 服务单：`RAI-20260622-502EB2`
- 群名：`NAR -高燃人生-0518线下`
- 两条测试消息均明确标注“系统联调测试、无需回复”
- 测试数据库记录 `service_case_id=NULL`、`purpose=MANUAL`，不会进入服务单聊天历史

受控数据：

| 记录 | 重启前状态 | 已知数据 |
| --- | --- | --- |
| `messages.id=31742` | `SENT` | 历史起点 `seq=8817510` |
| `messages.id=31743` | `UNKNOWN` | 数据库故意不保存发送回执；企微实际回执 `seq=8817511` |

重启后结果：

- 后端容器第 4 次、约 6 秒后通过容器内健康检查。
- 启动日志只冻结 `messages.id=31743`。
- 历史接口从 `seq=8817510` 查询，返回 1 条，`hasMore=false`。
- `messages.id=31743` 自动修正为 `SENT`。
- 补回的 `msgUniqueIdentifier`、`msgServerId=2026567`、`seq=8817511`、`timestamp=1784879045` 与发送接口真实回执完全一致。
- 对账日志：`confirmed=1 not_found=0 ambiguous=0`。
- 重启后公网 `GET /health`：通过。

## 永久测试数据

- 两条测试消息会保留在蔡浩然实验群中。
- RDS 中保留两条不绑定服务单的审计记录 `31742`、`31743`，未执行物理删除。
- 本轮未调用真实 LLM。

