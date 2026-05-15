# CHANGELOG

## 版本信息

**接手版本：** `3d01d84` - feat: 燃AI 1.0 初始版本
**修改完成版本：** 本次提交（2026-05-14 21:30）

**接手时状态：**
- 初始版本，基础功能已实现
- 问卷提交后需要手动触发生成文档
- 前端页面布局粗糙，功能分散
- 文档审核页面404

**修改完成后状态：**
- 问卷提交后自动生成画像和文档
- 全局自动发送配置（文档审核页面）
- 前端页面美化重构（侧边栏、用户管理、数据看板）
- 文档编辑器简化（自动保存、所见即所得）
- 聊天记录和问卷折叠组件
- Markdown预览渲染

---

## 2026-05-14 18:30 - 自动化流程优化

### 修改内容

**问题：** 用户提交问卷后需要运营人员手动点击多个按钮才能生成和发送文档，流程繁琐。

**解决方案：** 问卷提交后自动生成画像和文档，通过服务单级别的配置控制是否自动发送。

### 具体修改

#### 1. 后端修改

**backend/src/app/models/service_case.py**
- 新增字段：`auto_send_documents` (Boolean, 默认 False)
- 每个服务单独立控制是否自动发送文档

**backend/src/app/api/routes/questionnaires.py**
- 修改问卷提交逻辑，提交后自动触发：
  1. 生成用户画像
  2. 生成两份交付文件
- 根据服务单的 `auto_send_documents` 字段决定是否自动发送

**backend/alembic/versions/**
- 新增迁移：添加 `auto_send_documents` 字段到 `service_cases` 表

#### 2. 前端修改

**frontend/src/app/cases/[id]/case-actions.tsx**
- 添加勾选框：控制文档生成后是否自动发送
- 保留"确认发送两份文件"按钮（仅在未勾选自动发送时显示）

**frontend/src/lib/api.ts**
- ServiceCase 类型添加 `auto_send_documents` 字段

#### 3. 使用说明

**操作流程：**
1. 创建服务单后，在服务单详情页勾选"文档生成后自动发送到群"
2. 用户提交问卷后，系统自动生成画像和文档
3. 如果勾选了自动发送，文档会直接发送到群
4. 如果未勾选，需要手动点击"确认发送两份文件"

### 新流程

**自动发送模式（勾选自动发送）：**
```
用户提交问卷
    ↓
自动生成画像 + 生成文档 + 自动发送
    ↓
用户直接在群里收到文档
```

**人工审核模式（未勾选自动发送）：**
```
用户提交问卷
    ↓
自动生成画像 + 生成文档
    ↓
运营点击"确认发送两份文件"
```

### 待 Codex 接力

1. **测试新流程**：验证勾选框是否正常工作
2. **优化错误处理**：如果自动生成失败，需要有重试机制
3. **添加状态提示**：在前端显示文档生成状态（生成中/已完成/失败）
4. **审核页面优化**：在服务单详情页显示文档审核状态
5. **消息通知**：文档生成完成后通知运营人员

## 2026-05-14 19:00 - 聊天记录和问卷折叠

### 修改内容

#### 1. 新增功能

**聊天记录显示**
- 新增组件：`frontend/src/app/cases/[id]/chat-messages.tsx`
- 显示用户和机器人的聊天记录
- 区分消息方向（用户/机器人/运营）
- 支持展开/收起（默认显示最近 10 条）
- 标记打卡消息

**问卷结果折叠**
- 新增组件：`frontend/src/app/cases/[id]/questionnaire-section.tsx`
- 问卷结果默认折叠，点击展开
- 显示提交时间和所有答案

#### 2. 前端修改

**frontend/src/lib/api.ts**
- 新增 Message 类型定义

**frontend/src/app/cases/[id]/page.tsx**
- 集成聊天记录和问卷折叠组件
- 获取聊天记录数据

#### 3. 布局优化

- 问卷结果：折叠显示，点击展开
- 交付文件：保持原样
- 最近打卡：保持原样
- 聊天记录：新增，显示在最后

## 2026-05-14 19:30 - 前端页面美化重构

### 修改内容

**问题：** 前端页面布局粗糙，功能分散，用户体验不佳。

**解决方案：** 重新设计页面布局和导航结构，合并相关功能，美化 UI。

### 具体修改

#### 1. 侧边栏导航重构

**frontend/src/components/layout-shell.tsx**
- 重新设计侧边栏：更宽、带图标、激活状态高亮
- 导航项精简为 4 个：用户管理、文档审核、问卷配置、数据看板
- 移除独立的服务单、打卡记录导航（合并到其他页面）
- 添加底部版权信息

#### 2. 用户管理页面重构

**frontend/src/app/users/page.tsx**
- 卡片式布局展示用户
- 每个用户卡片内嵌服务单列表
- 服务单显示状态标签（彩色）
- 点击服务单直接进入详情页
- 合并了原"/cases"列表功能

#### 3. 数据看板重构

**frontend/src/app/dashboard/page.tsx**
- 统计卡片使用彩色背景区分
- 添加图标和更好的视觉层次
- 内嵌最近打卡记录列表
- 合并了原"/checkins"功能
- 移除独立打卡记录页面

#### 4. 服务单详情页美化

**frontend/src/app/cases/[id]/page.tsx**
- 状态标签彩色显示
- 信息卡片添加图标
- 交付文件区域添加空状态提示
- 打卡记录使用头像+内容布局
- 整体使用圆角卡片和阴影

### 导航结构变化

**之前：**
- 用户 / 服务单 / 问卷配置 / 打卡记录 / 基础看板

**现在：**
- 用户管理（含服务单）
- 文档审核
- 问卷配置
- 数据看板（含打卡记录）

### 待 Codex 接力

1. **响应式设计**：确保移动端显示正常
2. **加载状态**：添加骨架屏或加载动画
3. **空状态优化**：更多页面的空状态提示
4. **动画效果**：添加页面切换和交互动画
5. **深色模式**：支持深色主题切换

## 2026-05-14 20:00 - UI优化和问题修复

### 修复内容

#### 1. 文档发送按钮移至文档审核页面

**frontend/src/app/cases/[id]/case-actions.tsx**
- 移除"确认发送两份文件"按钮
- 仅保留"自动发送"勾选框配置

**frontend/src/app/documents/[caseId]/document-editor.tsx**
- 添加"确认发送"按钮
- 添加"审核通过"按钮
- 添加预览/编辑切换功能
- 预览模式使用 react-markdown 渲染 HTML

**frontend/src/app/documents/page.tsx**（新增）
- 创建文档审核列表页
- 显示所有服务单的交付文件
- 按状态筛选（待审核/已批准/已发送）
- 修复 404 问题

#### 2. 问卷结果默认显示第一个问题

**frontend/src/app/cases/[id]/questionnaire-section.tsx**
- 折叠状态显示第一个问题摘要
- 显示"还有 N 项..."提示
- 展开按钮显示"展开全部 N 项"

#### 3. 打卡空状态图标修复

**frontend/src/app/cases/[id]/page.tsx**
- 空状态图标从 ✅ 改为 📋
- 文字改为"暂无打卡记录"

#### 4. Markdown 渲染支持

**frontend/src/components/markdown-preview.tsx**（新增）
- 使用 react-markdown + remark-gfm 渲染
- 自动将 Markdown 语法转换为 HTML 展示
- 文档预览模式使用此组件

### 文档生成逻辑说明

当前 docx 生成逻辑（backend/src/app/services/docx_service.py）：
1. AI 生成结构化 JSON（包含标题、段落、列表等）
2. 后端将 JSON 转换为 Markdown 格式
3. Markdown 通过 python-docx 库直接渲染为 docx
4. 使用微软雅黑字体，支持中文

注意：当前不是模板填充，而是动态生成完整文档内容。

## 2026-05-14 20:30 - 全局自动发送设置

### 修改内容

**问题：** "自动发送"设置放在单个服务单中不合理，应该是全局配置。

**解决方案：** 将自动发送改为全局设置，放到文档审核页面。

### 具体修改

#### 1. 后端修改

**backend/src/app/api/routes/questionnaires.py**
- 问卷提交后使用 `get_settings().auto_send_documents`（全局配置）

**backend/src/app/api/routes/settings.py**（新增）
- 新增 API：`GET/POST /api/settings/auto-send`
- 获取/设置全局自动发送配置

**backend/src/app/api/router.py**
- 注册 settings 路由

#### 2. 前端修改

**frontend/src/app/cases/[id]/case-actions.tsx**
- 移除勾选框（不再是个体配置）
- 简化为空组件

**frontend/src/app/documents/page.tsx**
- 添加全局"自动发送"开关（切换按钮样式）
- 显示当前模式说明

**frontend/src/app/documents/auto-send-toggle.tsx**（新增）
- 切换按钮组件
- 调用 `/api/settings/auto-send` API

#### 3. 配置说明

```bash
# 环境变量控制（重启生效）
AUTO_SEND_DOCUMENTS=false  # 默认，需要人工审核
AUTO_SEND_DOCUMENTS=true   # 自动发送到群
```

## 2026-05-14 21:00 - 文档审核页面优化

### 修改内容

**问题：** 
1. 文档审核列表页缺少用户姓名和手机号，难以区分
2. 点击文档进入详情页时显示该服务单的所有文档，而不是单个文档
3. 文档编辑器按钮太多，流程复杂

**解决方案：** 
1. 列表页显示用户姓名、手机号
2. 路由改为 `/documents/[documentId]`，只显示单个文档
3. 简化编辑器，移除多余按钮，改为自动保存

### 具体修改

#### 1. 文档审核列表页优化

**frontend/src/app/documents/page.tsx**
- 显示用户姓名和手机号：`{user_name} · {user_phone} · {case_no}`
- 链接改为 `/documents/${doc.id}`（文档ID而非服务单ID）

#### 2. 文档详情页路由重构

**frontend/src/app/documents/[documentId]/page.tsx**（新增）
- 新路由：`/documents/[documentId]`
- 使用 `GET /documents/{documentId}` API 获取单个文档
- 只显示当前文档，不再显示服务单的所有文档

#### 3. 文档编辑器简化

**frontend/src/app/documents/[caseId]/document-editor.tsx**
- 移除"审核通过"按钮
- 移除"保存"按钮（改为自动保存，debounce 1.5秒）
- 移除"预览"按钮（默认显示渲染后的内容）
- 只保留两个按钮：**确认发送**、**下载 docx**
- 点击内容进入编辑模式，修改后自动保存
- 编辑模式显示"Markdown 编辑模式（修改后自动保存）"提示

### 新流程

**文档审核列表页：**
- 显示：文档标题、用户姓名、手机号、服务单编号、状态
- 点击进入单个文档的详情页

**文档详情页：**
- 默认显示渲染好的文档内容（所见即所得）
- 点击内容进入编辑模式（Markdown源码）
- 修改后自动保存（1.5秒延迟）
- 只有两个按钮：确认发送、下载 docx

### 待 Codex 接力

1. **测试自动保存功能**：验证修改后是否自动保存
2. **测试单文档路由**：验证 `/documents/[documentId]` 是否正常工作
3. **优化 Markdown 渲染样式**：进一步美化文档预览效果
4. **添加返回按钮**：在文档详情页添加返回列表的按钮
