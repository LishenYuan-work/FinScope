# AI 财报多 Agent 投研分析平台
这是一个只处理“用户上传 PDF 年报”的投研分析工作台。四个 Agent 通过 LangGraph 依次完成文档解析、财务分析、风险研判和报告汇总；原文按页分块后写入 Supabase pgvector，模型通过检索工具获取证据，并在报告中保留页码与 chunk 来源。解析后的完整正文不会复制进后续 LangGraph 状态，避免长文档重复占用实例内存。
## 目前状态
- 前端仪表盘、拖拽上传、历史任务、SSE 输出、指标卡片、Recharts 图表和 Markdown 打印导出已完成。
- FastAPI 接口、LangGraph 状态流转、PDF 分页解析、pgvector 检索和异常状态落库已完成。
- 没有配置密钥时只能查看空白工作台；示例 PDF 也必须通过真实 FastAPI、Supabase 和模型链路分析，不会伪造结果。
## 真实联调前的人工步骤
1. 在 Supabase SQL Editor 中执行根目录的 `schema.sql`，启用 `vector` 扩展并创建任务、报告、事件、文档分块表和检索函数。事件表用于 SSE 断线补发。
2. 创建 Supabase 项目，复制 Project URL 和后端专用 secret/service_role key（新版 `sb_secret_...` 可直接使用）。
3. 在 DeepSeek 或阿里云百炼（DashScope）控制台创建 API key。密钥只写入本地 `.env`，不要提交 GitHub。
4. 复制环境模板并填写真实值：
```powershell
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env.local
```
根目录 `.env` 至少填写模型 key 和 Supabase 配置：默认 `deepseek` 对话需要 `DEEPSEEK_API_KEY`，`qwen` 向量化需要 `DASHSCOPE_API_KEY`，以及 `SUPABASE_URL`、`SUPABASE_SERVICE_KEY`。后端会将 PDF 写入私有 Supabase Storage bucket（默认 `annual-reports`），并在启动时尝试自动创建；如果项目策略禁止自动创建，请在 Storage 页面手动创建同名私有 bucket，再保持 `SUPABASE_STORAGE_AUTO_CREATE=false`。前端 `.env.local` 只需设置 `NEXT_PUBLIC_API_BASE_URL`。`VECTOR_DB_BATCH_SIZE` 控制向量写入批次，默认值 `8` 适合 Supabase 免费实例；批次超时会自动拆分重试。`MODEL_REQUEST_TIMEOUT_SECONDS` 与 `EMBEDDING_REQUEST_TIMEOUT_SECONDS` 分别限制模型和向量接口单次请求时长，超时会进入既有重试/降级流程。`MAX_CONCURRENT_ANALYSES` 控制单实例并行任务数，Render 免费实例建议保持默认 `1`。
### 使用 DeepSeek
项目支持 DeepSeek 和通义千问的 OpenAI 兼容接口。`LLM_PROVIDER=deepseek` 时把 DeepSeek key 填到 `DEEPSEEK_API_KEY`，并把 `EMBEDDING_PROVIDER=qwen`、千问 key 填到 `DASHSCOPE_API_KEY`。DeepSeek目前没有公开embedding接口，因此“DeepSeek对话 + 千问向量化”需要两把 key。
如果希望只使用一把 key，可设置 `LLM_PROVIDER=qwen`、`EMBEDDING_PROVIDER=qwen`，同一个 `DASHSCOPE_API_KEY` 同时用于 qwen-plus 对话和 text-embedding-v2 向量化，不需要 OpenAI key。Supabase表中的 `vector(1536)` 与 text-embedding-v2 输出维度匹配。
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
打开 [http://localhost:3000](http://localhost:3000)。后端诊断检查为 [http://localhost:8000/healthz](http://localhost:8000/healthz)，会明确显示 `configuration: ready`、`event_replay` 和 `storage` 状态或缺失的变量名，但不会输出密钥；执行 `schema.sql` 并确保 `annual-reports` bucket 存在后，相关状态会在下一次检查（最多约 30 秒）自动变为 `available`，无需重启服务。部署编排使用严格的 [http://localhost:8000/readyz](http://localhost:8000/readyz)：只有 Supabase、事件回放表、Storage bucket 和模型变量全部就绪才返回 HTTP 200，Render Blueprint 已将健康检查切换到该地址。
如果 `/healthz` 显示 `event_replay: unavailable_run_schema_sql`，说明当前 Supabase 项目还没有完整执行数据库脚本。登录对应项目的 Supabase Dashboard，进入 **SQL Editor**，粘贴并运行仓库根目录 `schema.sql` 的全部内容；然后确认 Storage 中存在名为 `annual-reports` 的私有 bucket。重新访问 `/healthz`，看到 `event_replay: available`、`storage: available` 后，再访问 `/readyz`，应返回 HTTP 200。脚本使用 `if not exists` 和 `create or replace`，重复执行是安全的。
## 认证与接口
首页支持未登录只读预览；上传年报、运行示例分析、查看历史任务和订阅 SSE 等功能要求邮箱+密码登录。认证由 Supabase Auth 托管。注册后如果 Supabase 开启了邮箱确认，需要先点击确认邮件，再返回登录页。

`POST /auth/signup` 注册；`POST /auth/login` 登录并返回 access token；`GET /auth/me` 校验当前登录用户。除 `/healthz`、`/readyz` 和认证接口外，任务接口都要求 `Authorization: Bearer <access_token>`。`POST /tasks` 上传字段 `file`；`GET /stream/{task_id}` 订阅 SSE（原生 EventSource 使用短期 `access_token` 查询参数，支持 `Last-Event-ID` 断线补发）；`GET /tasks/{task_id}` 获取当前用户的 Markdown 与 JSON 报告；`GET /tasks?limit=50` 只返回当前用户的历史任务。

任务表通过 `user_id` 关联 Supabase Auth 用户，用户之间不会互相看到任务、报告或事件。执行 schema 迁移后，旧的未归属任务不会展示给新登录用户。
## 首页官方年报示例
仓库已内置三份官方来源年报到 `frontend/public/examples`。首页的“2025 年报示例”卡片会读取本地 PDF，调用 `POST /tasks` 创建真实任务，再通过 SSE 展示 Agent 流水线、输出流、RAG 检索、核心指标、图表、风险、行业对比和中文 Markdown 报告。示例不会使用前端预设结果，也不会绕过 FastAPI 或写入虚假的任务记录。卡片同时保留官方原件和来源页链接，便于核验：
- [阿里巴巴集团 FY2025 Annual Report（PDF）](https://data.alibabagroup.com/ecms-files/1514443390/dbeaef58-eac8-4c00-9fdf-e361a5841078/Alibaba%20Group%20Holding%20Limited%20Fiscal%20Year%202025%20Annual%20Report.pdf) · [投资者关系来源页](https://www.alibabagroup.com/en-US/ir-financial-reports-financial-results)
- [腾讯控股 FY2025 Annual Report（PDF）](https://static.www.tencent.com/uploads/2026/04/09/5c38a9ec3c5695da42c1534d63e4be0b.pdf) · [投资者关系来源页](https://www.tencent.com/investors/financial-reports/)
- [百度集团 FY2025 年报（香港交易所 PDF）](https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0317/2026031701611_c.pdf) · [百度证券备案页](https://ir.baidu.com/financial-information/sec-filings)

百度的 2025 财年报告以香港交易所官方披露 PDF 提供，文件名为 `baidu-2025-annual-report.pdf`。示例入口不会请求外部下载；它只读取仓库内置 PDF，然后与用户上传文件使用完全相同的后端分析流程。

## Docker / Render

安装 Docker Desktop 后，在根目录执行：

```powershell
docker compose up --build
```

前端访问 `http://localhost:3000`，后端访问 `http://localhost:8000`。仓库根目录的 `render.yaml` 已提供后端与前端两个 Render Web Service 的 Blueprint：后端使用根目录 `Dockerfile` 和严格的 `/readyz` 就绪检查，前端使用 `frontend/Dockerfile`。创建 Blueprint 后，在 Render 控制台填写标记为 `sync: false` 的密钥变量，并确认 `CORS_ALLOW_ORIGINS` 与实际前端域名一致。Docker 构建依赖网络和 Docker Desktop；本地未安装 Docker 时只能验证 Python/Next 构建，无法验证镜像构建。

Compose 与 Render 都使用 `/readyz` 作为严格就绪检查；只有 Supabase schema、Storage 和模型配置全部可用时，前端容器才会启动。

## 验证命令

```powershell
python -m py_compile app/main.py
python -m unittest discover -s tests -v
Set-Location frontend
npm run typecheck
npm run build
```

本地联调记录：Python 单元测试与 Next.js 类型检查、生产构建均已通过。若上传接口返回
`503`，先访问 `/healthz`；当 `event_replay` 显示 `unavailable_run_schema_sql` 或
`storage` 不可用时，需在当前 Supabase 项目执行根目录 `schema.sql` 并确认
`annual-reports` 私有 bucket 已创建。未完成这一步时，系统会保留错误状态，不会伪造报告结果。
