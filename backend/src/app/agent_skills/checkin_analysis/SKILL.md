# checkin_analysis

描述: 处理体重、打卡、趋势变化和阶段执行反馈。

## 适用场景

- 用户问“最近体重怎么样”“为什么体重没变化”。
- 用户问“我最近打卡怎么样”“我是不是执行得还可以”。
- 用户同时问服务第几天和近期数据。

## 使用原则

- 涉及服务天数必须调用 `get_service_progress()`。
- 涉及体重变化必须调用 `get_weight_trend(days)`。
- 涉及执行过程必须调用 `get_recent_checkins(days)`。
- 数据不足时明确说“数据还不够判断趋势”，不能猜。
- 解释趋势时优先看连续性，不只看单日数字。
- 回复只给一个最小动作，避免一次布置太多任务。

## 推荐工具

- `get_service_progress()`
- `get_weight_trend(days=7)`
- `get_recent_checkins(days=7)`

详细指标解释见 `references/metric_rules.md`。
