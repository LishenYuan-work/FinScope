# AI 财报多 Agent 投研分析平台

这是一个只处理“用户上传 PDF 年报”的投研分析工作台。四个 Agent 通过 LangGraph 依次完成文档解析、财务分析、风险研判和报告汇总；原文按页分块后写入 Supabase pgvector，模型通过检索工具获取证据，并在报告中保留页码与 chunk 来源。

## 目前状态

- 前端仪表盘、拖拽上传、历史任务、SSE 输出、指标卡片、Recharts 图表和 Markdown 打印导出已完成。
- FastAPI 接口、LangGraph 状态流转、PDF 分页解析、pgvector 检索和异常状态落库已完成。
- 没有配置密钥时仍可运行前端演示；这不代表真实 LLM/Supabase 链路已经联调。

## 真实联调前的人工步骤

1. 在 Supabase SQL Editor 中执行根目录的 `schema.sql`，启用 `vector` 扩展并创建表和检索函数。
2. 创建 Supabase 项目，复制 Project URL 和后端专用 secret/service_role key（新版 `sb_secret_...` 可直接使用）。
3. 在 DeepSeek 或阿里云百炼（DashScope）控制台创建 API key。密钥只写入本地 `.env`，不要提交 GitHub。
4. 复制环境模板并填写真实值：

```powershell
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env.local
```

根目录 `.env` 至少填写模型 key 和 Supabase 配置：默认 `deepseek` 对话需要 `DEEPSEEK_API_KEY`，`qwen` 向量化需要 `DASHSCOPE_API_KEY`，以及 `SUPABASE_URL`、`SUPABASE_SERVICE_KEY`。前端 `.env.local` 设置 `NEXT_PUBLIC_DEMO_MODE=false`。

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

打开 [http://localhost:3000](http://localhost:3000)。后端健康检查为 [http://localhost:8000/healthz](http://localhost:8000/healthz)。健康检查会明确显示 `configuration: ready` 或缺失的变量名，但不会输出密钥。

## 接口

`POST /tasks` 上传字段 `file`；`GET /stream/{task_id}` 订阅 SSE；`GET /tasks/{task_id}` 获取 Markdown 与 JSON 报告；`GET /tasks?limit=50` 获取历史任务。

## 首页官方年报示例

首页的“2025 年报示例”卡片仅提供官方来源链接，不会在后台自动下载或抓取文件。请在官方页面打开并手动下载后，再将 PDF 拖入上传区：

- [阿里巴巴集团 FY2025 Annual Report（PDF）](https://data.alibabagroup.com/ecms-files/1514443390/dbeaef58-eac8-4c00-9fdf-e361a5841078/Alibaba%20Group%20Holding%20Limited%20Fiscal%20Year%202025%20Annual%20Report.pdf) · [投资者关系来源页](https://www.alibabagroup.com/en-US/ir-financial-reports-financial-results)
- [腾讯控股 2025 Annual Report（PDF）](https://static.www.tencent.com/uploads/2025/08/26/d1d2fb988f5e910e254b3abb41dadb0f.pdf) · [投资者关系来源页](https://www.tencent.com/investors/financial-reports/)
- [百度集团 FY2025 20-F（SEC HTML）](https://www.sec.gov/Archives/edgar/data/1329099/000119312526109289/d38065d20f.htm) · [百度证券备案页](https://ir.baidu.com/financial-information/sec-filings)

百度的 2025 财年报告目前以 SEC 20-F 年度备案形式提供，因此卡片标注为 HTML，而不是 PDF。示例卡片不会伪造指标或分析结果；真实图表和报告只在上传后端处理完成时生成。

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
