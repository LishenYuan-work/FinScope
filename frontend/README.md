# FinScope 前端

Next.js App Router 桌面端网页工作台，连接已存在的 FastAPI 财报分析服务。设计以 1280px+ 电脑浏览器为主，窄窗口仅提供基础折叠兜底。

## 本地运行

```bash
cp .env.example .env.local
npm install
npm run dev
```

默认访问 `http://localhost:3000`。后端地址由 `NEXT_PUBLIC_API_BASE_URL` 控制；standalone 容器启动时会写入 `public/runtime-config.js`，因此 Render 可以在运行时切换后端域名而不依赖重新构建前端代码。

打开首页会先进入邮箱+密码登录页；登录成功后才会加载当前账号的历史任务。点击“2025 年报示例”中的阿里、腾讯或百度后，前端会读取仓库内置 PDF，调用 FastAPI 创建真实任务，并通过 SSE 实时展示解析、RAG、Agent 输出和最终报告；不会使用预设结果。

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

`npm run start` 会自动同步 standalone 所需的静态资源后启动；Docker 镜像使用同一产物中的 `server.js`。

项目使用 Next standalone 输出，可直接使用 `Dockerfile` 部署到 Render 或其他容器平台；容器入口会在启动时生成运行时 API 配置，再启动 standalone server。
