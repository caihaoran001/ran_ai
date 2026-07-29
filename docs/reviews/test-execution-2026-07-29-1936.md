# 当前发布包上线门禁执行记录

时间：2026-07-29 19:36 CST  
本地版本：`b201c97`  
生产版本：`d86083364c0a71eac27a692b2d472d1305ed885f`

## 范围

本次回答“是否全部通过、是否可以上线”，补跑当前发布包的前端类型、构建和浏览器 smoke，并只读核对生产基线。

生产到本地 `HEAD` 共 30 个提交、48 个文件，不只是 A 带收口，还包括交付文件整体优化、人工复核页面标记、知识检索观测和移除 `product_usage` skill 等已提交改动。

## 已有通过项

- 后端全量：`488 passed`；
- 入营聚焦：`38 passed`；
- A 带真实模型最终输出：`2 passed / 0 failed`；
- 孕期高风险真实样本：正常 D 带 + 重点人工复核；
- 本地后端健康检查：HTTP 200。

## 本次命令

### 前端类型检查

```bash
cd frontend
npx tsc --noEmit --incremental false
```

结果：通过，exit 0。

### Playwright 用例发现

```bash
cd frontend
npm run test:e2e -- --list
```

结果：通过，发现 2 个 spec、9 个用例。

### 生产构建

```bash
cd frontend
npm run build
```

结果：通过。Next.js 15.5.18 完成编译、类型检查、29 个静态页面生成和 standalone 构建产物收集。

### 浏览器 smoke

首次在受限沙箱内启动 Chromium 失败，错误为：

```text
FATAL:content/browser/sandbox_host_linux.cc:41
Check failed: . shutdown: 不允许的操作
```

这是浏览器进程权限阻塞，随后在沙箱外按同一命令重跑：

```bash
cd frontend
npm run test:e2e
```

结果：`8 passed / 1 failed`。

唯一失败：

```text
/users loads
期望一级标题：用户管理
实际一级标题：正常用户
```

页面实际已正常加载，截图和 accessibility tree 均显示：

- 面包屑与侧栏仍为“用户管理”；
- 页面主标题按当前产品分组设计为“正常用户”；
- 正常用户/活跃用户/已删除切换、搜索框和用户列表均正常呈现。

因此这是 repo smoke 断言未跟随现有页面标题更新，不是页面加载失败，也不是本次 A 带后端改动引起的产品故障。本轮按测试规范未修改测试预期。

## 生产基线

通过 ECS `/root/ran_ai/DEPLOYED_VERSION` 只读确认：

```text
commit: d86083364c0a71eac27a692b2d472d1305ed885f
subject: refactor(followup): use fixed reminder templates
deployed_at: 2026-07-28 11:13:39 +0800
worktree_dirty: no
```

当前部署 `HEAD` 会一次性带上生产之后的 30 个提交。

## 状态

- 功能回归、真实模型、类型检查和生产构建：通过；
- 浏览器 smoke：8通过、1条过时标题断言失败，页面本身正常；
- 未执行生产部署。

