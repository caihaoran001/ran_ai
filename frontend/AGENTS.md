# 前端开发说明

这里只记录前端独有的定位和架构边界。协作、授权、提交、WIP、测试命令及部署要求以仓库根目录的 `AGENTS.md` 为准。页面和接口会持续变化，不在此维护完整清单。

## 代码地图

| 任务 | 主要入口 |
| --- | --- |
| 布局与导航隔离 | `src/app/layout.tsx`、`src/components/layout-shell.tsx` |
| 登录、会话与公开路由 | `src/middleware.ts`、`src/lib/auth.ts` |
| 通用请求与共享类型 | `src/lib/api.ts` |
| 浏览器到后端的同源代理 | `src/app/api/backend/[...path]/route.ts` |
| 公开问卷 | `src/app/q/[token]/`、`src/app/api/questionnaires/[token]/submit/route.ts` |
| 公开短链 | `src/app/s/[code]/route.ts` |
| 运营页面 | `src/app/users/`、`cases/`、`documents/`、`dashboard/` |
| AI 运营与评测 | `src/app/agent-operations/`、`rag-evaluations/` |
| 通用组件 | `src/components/` |

## 架构边界

### 渲染与组件

- 页面默认使用 React Server Components；只有需要浏览器状态、事件、Effect 或浏览器 API 的最小交互区域才添加 `"use client"`。Server Component 中不得使用客户端 Hook。
- 沿用现有 Tailwind CSS 和组件体系，优先复用 `src/components/`，避免为单页重复建设基础组件。

### 请求与密钥

- 普通 JSON 请求优先复用 `src/lib/api.ts` 的 `api<T>()`。上传、流式响应、登录、公开提交或需保留响应头时可以直接使用 `fetch`，但必须复用既有同源路由或集中定义的地址。
- 浏览器访问受保护的后端能力必须走 `/api/backend/*`，不得直连内部 FastAPI 地址。
- `BACKEND_ADMIN_API_KEY` 只允许 Next.js 服务端读取和注入；禁止进入 `NEXT_PUBLIC_*`、客户端属性、浏览器响应或日志。

### 公开页面与业务状态

- `/q/*` 问卷和 `/s/*` 短链是公开入口，不显示后台导航；其他运营页面默认需要登录。
- 新增或改变公开入口时，同时检查 `src/middleware.ts` 的白名单和 `layout-shell.tsx` 的布局隔离。问卷公开提交只放行既定请求方式，运营编辑和重新生成仍需登录。
- 生命周期、风险、发送资格和文档审核以服务端状态为准，不在前端复制业务判定。状态变更操作必须处理失败并防止重复提交。

## 验证

- 按根目录测试规范选择与改动相称的验证；涉及公开路由、登录、代理或布局隔离时，同时验证公开用户和已登录运营人员两条路径。
