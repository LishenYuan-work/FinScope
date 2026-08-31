# AI 财报多 Agent 投研分析平台

这是一个只处理“用户上传 PDF 年报”的投研分析工作台。四个 Agent 通过 LangGraph 依次完成文档解析、财务分析、风险研判和报告汇总；原文按页分块后写入 Supabase pgvector，模型通过检索工具获取证据，并在报告中保留页码与 chunk 来源。

## 目前状态

- 前端仪表盘、拖拽上传、历史任务、SSE 输出、指标卡片、Recharts 图表和 Markdown 打印导出已完成。
- FastAPI 接口、LangGraph 状态流转、PDF 分页解析、pgvector 检索和异常状态落库已完成。
- 没有配置密钥时仍可运行前端演示；这不代表真实 LLM/Supabase 链路已经联调。

## 真实联调前的人工步骤

1. 在 Supabase SQL Editor 中执行根目录的 `schema.sql`，启用 `vector` 扩展并创建表和检索函数。
2. 创建 Supabase 项目，复制 Project URL 和 service role key。
3. 在 OpenAI 控制台创建 API key。密钥只写入本地 `.env`，不要提交 GitHub。
4. 复制环境模板并填写真实值：

```powershell
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env.local
```

根目录 `.env` 至少填写：`OPENAI_API_KEY`、`SUPABASE_URL`、`SUPABASE_SERVICE_KEY`。前端 `.env.local` 设置 `NEXT_PUBLIC_DEMO_MODE=false`。

## 本地运行

后端（PowerShell）：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

前端（另开一个终端）：

```powershell
Set-Location frontend
npm install
npm run dev
```

打开 [http://localhost:3000](http://localhost:3000)。后端健康检查为 [http://localhost:8000/healthz](http://localhost:8000/healthz)。健康检查会明确显示 `configuration: ready` 或缺失的变量名，但不会输出密钥。

## 接口

`POST /tasks` 上传字段 `file`；`GET /stream/{task_id}` 订阅 SSE；`GET /tasks/{task_id}` 获取 Markdown 与 JSON 报告；`GET /tasks?limit=50` 获取历史任务。

## Docker / Render

安装 Docker Desktop 后，在根目录执行：

```powershell
docker compose up --build
```

前端访问 `http://localhost:3000`，后端访问 `http://localhost:8000`。Render 部署时分别使用根目录 `Dockerfile` 和 `frontend/Dockerfile`，并在服务环境变量中填写同名配置。Docker 构建依赖网络和 Docker Desktop，本地未安装 Docker 时无法验证镜像构建。

## 验证命令

```powershell
python -m py_compile app/main.py
Set-Location frontend
npm run typecheck
npm run build
```
