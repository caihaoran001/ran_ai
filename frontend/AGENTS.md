# FRONTEND KNOWLEDGE BASE

**Stack:** Next.js 15 + React 19 + TypeScript + Tailwind CSS

## STRUCTURE

```
frontend/
├── src/app/                # Next.js App Router 页面
│   ├── cases/              # 服务单管理
│   ├── users/              # 用户管理 (支持软删除)
│   ├── documents/          # 交付文件审核
│   ├── q/[token]/          # H5 问卷 (已隔离，无侧边栏)
│   ├── questionnaire-config/ # 问卷配置
│   ├── checkins/           # 打卡记录
│   ├── dashboard/          # 基础看板
│   ├── login/             # 登录页
│   ├── s/                 # 短链公开路由 (无侧边栏，需 middleware 白名单)
│   ├── system-diagnostics/ # 系统诊断页
│   └── api/                # Next.js API 路由 (代理)
├── src/components/
│   ├── ui/                 # 基础 UI 组件 (shadcn/ui 风格)
│   └── layout-shell.tsx    # 布局壳组件 (隔离问卷页面)
├── src/middleware.ts       # 鉴权/路由中间件 (公开路由如 /q /s 需在白名单，否则跳 /login)
├── src/lib/                # 工具函数和 API 客户端
└── package.json
```

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| 布局和导航 | `src/app/layout.tsx` | 根布局 |
| 布局壳组件 | `src/components/layout-shell.tsx` | 隔离问卷页面 |
| API 客户端 | `src/lib/api.ts` | 统一 fetch 封装 + 类型定义 |
| 用户管理 | `src/app/users/page.tsx` | 用户列表 (支持已删除用户) |
| 用户操作 | `src/app/users/user-actions.tsx` | 删除用户按钮 |
| 服务单详情 | `src/app/cases/[id]/page.tsx` | 显示问卷/文档/打卡/统计 |
| 问卷表单 | `src/app/q/[token]/questionnaire-form.tsx` | H5 问卷表单 |
| 问卷提交代理 | `src/app/api/questionnaires/[token]/submit/route.ts` | Next.js API 代理 |

## CONVENTIONS

- 页面使用 React Server Components (默认)
- 客户端交互使用 `"use client"` 指令
- 样式使用 Tailwind CSS 原子类
- API 调用使用 `api<T>()` 泛型函数
- 表单使用 React Server Actions 或客户端 fetch
- 问卷页面 (`/q/*`) 不显示后台管理侧边栏

## API ENDPOINTS

```
# 用户管理
GET    /api/users                      # 用户列表 (?deleted=true 查看已删除)
GET    /api/users/:id                  # 用户详情
DELETE /api/users/:id                  # 软删除用户

# 服务单
GET    /api/service-cases              # 服务单列表
GET    /api/service-cases/:id          # 服务单详情
GET    /api/service-cases/:id/stats    # 服务单统计
PATCH  /api/service-cases/:id          # 更新服务单

# 文档管理
POST   /api/service-cases/:id/generate-profile    # 生成用户画像
POST   /api/service-cases/:id/generate-documents  # 生成交付文件
POST   /api/service-cases/:id/documents/send      # 发送文档
GET    /api/service-cases/:id/documents            # 列出文档

# 消息管理
GET    /api/service-cases/:id/messages             # 列出消息
POST   /api/service-cases/:id/messages/send        # 发送消息

# 问卷
GET    /api/questionnaires/:token      # 获取问卷
POST   /api/questionnaires/:token/submit # 提交问卷

# 看板
GET    /api/dashboard/summary          # 看板数据
```

## ANTI-PATTERNS

- 不要在 Server Component 中使用 useState/useEffect
- 不要硬编码 API 地址，使用环境变量
- 不要绕过 `api()` 函数直接 fetch
- 不要在问卷页面显示后台管理导航
