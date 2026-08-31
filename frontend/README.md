# FinScope 前端

Next.js App Router 桌面端网页工作台，连接已存在的 FastAPI 财报分析服务。设计以 1280px+ 电脑浏览器为主，窄窗口仅提供基础折叠兜底。

## 本地运行

```bash
cp .env.example .env.local
npm install
npm run dev
```

默认访问 `http://localhost:3000`。后端地址由 `NEXT_PUBLIC_API_BASE_URL` 控制。

默认开启本地演示数据（`NEXT_PUBLIC_DEMO_MODE=true`），打开页面即可查看仪表盘、图表、风险洞察和示例报告；接入 FastAPI 后可设置为 `false`，上传真实 PDF。

## API 约定

- `POST /tasks`：使用 `multipart/form-data` 字段 `file` 上传 PDF，返回 `{ task_id, status }`。
- `GET /stream/{task_id}`：SSE 事件 `status`、`agent`、`warning`。
- `GET /tasks/{task_id}`：返回 `status`、`report_markdown`、`report_json`、`error`。
- `GET /tasks?limit=50`：返回历史任务数组。

## 生产构建

```bash
npm run typecheck
npm run build
npm run start
```

项目使用 Next standalone 输出，可直接使用 `Dockerfile` 部署到 Render 或其他容器平台。
