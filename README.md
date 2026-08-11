# CodeTrace

代码变更追溯图 —— 不只是展示 `git log`，而是给你的仓库做体检：告诉你项目健不健康、哪里在恶化、变更背后的原因是什么。

## 核心定位

> **结构化计算 + LLM 翻译。** 技术壁垒来自 git 数据层面的结构化计算（co-change、时序趋势、耦合分析），LLM 只负责把数据翻译成可读的洞察，不依赖 LLM 的"智能"作为核心能力。

## 功能特性

- **变更追溯图**：输入 GitHub URL + 文件路径，自动构建从 commit 到 PR 到讨论的完整变更时间线，支持函数 / 类级别的演化追溯（含重命名跟随，对齐 `git log --follow`）。
- **Git Graph 仪表盘**：分支拓扑 + 可展开提交时间线，应用内直接查看 PR 详情、问 Agent、跳转 GitHub。
- **仓库体检**：文件健康度（变更频率、churn、时效性）、耦合风险（三层分布）、模块侵蚀分析。
- **对话式 Agent**：文件树 / 时间线 / 详情页随处"问 Agent"，SSE 流式输出，支持并行工具调用（查看 diff、读取文件、追踪函数、查询 PR）。
- **持续追踪**：PR 合入后自动生成增量洞察报告（结构化快照 + LLM 翻译，失败自动回退结构化摘要）。
- **持久化预索引**：SQLite 事实表，clone/pull 后后台自动建索引 + SSE 进度；热路径查询索引优先、异常静默回退。
- **私有仓库支持**：`GITHUB_TOKEN` 经 URL 作用域认证头注入，匿名优先回退，防止 token 泄露 / 跨主机。
- **代理兼容**：git/API 失败自动直连重试，支持显式代理 `CODETRACE_GIT_PROXY`。
- **安全收口**：本地默认绑定 127.0.0.1 + CORS 来源限制 + 可选 API Key 统一鉴权。

## 架构

```text
浏览器 (React SPA)
   │  /api（开发: Vite 代理 / 生产: nginx 同源代理）
   ▼
FastAPI 后端
   ├─ routers/   trace · repo · agent（按域拆分）
   ├─ services/  git_runner · git_stats · git_cache · index_service
   │             metrics · tracking · github_service · llm_service
   │             ast_service · coupling_runner
   ├─ services/agent/  planner · tools · graph（Agent 分析层）
   └─ models/    Pydantic schemas
        │
        ├─ SQLite 预索引（commits / files / file_commits / symbols / pr_cache）
        └─ git 仓库缓存（按主机 + owner 隔离）
```

## 目录结构

```text
backend/           FastAPI 后端（Python 3.12，uv 管理依赖）
  routers/         API 路由（trace / repo / agent）
  services/        领域服务（git、索引、指标、追踪、LLM、AST、耦合）
  models/          Pydantic 模型
  tests/           pytest 测试
frontend/          React 19 + Vite + Tailwind 前端
  src/components/  界面组件（文件树、时间线、Git Graph、Agent 面板等）
Dockerfile.backend / Dockerfile.frontend / docker-compose.yml / nginx.conf
```

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.12 · FastAPI · uvicorn · uv |
| 前端 | React 19 · Vite · Tailwind CSS 4 · vis-network |
| 数据 | SQLite（预索引）· git |
| 分析 | tree-sitter（AST 符号提取）· 结构化指标计算 |
| 部署 | Docker · docker compose · nginx |

## 快速开始（本地开发）

### 1. 后端

```bash
cd backend
uv sync
cp .env.example .env   # 按需填入 GITHUB_TOKEN / LLM_API_KEY
uv run python main.py  # 默认 http://127.0.0.1:8000
```

### 2. 前端

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173（/api 已代理到后端）
```

## 配置

后端配置见 `backend/.env.example`，部署配置见根目录 `deploy.env.example`。

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `GITHUB_TOKEN` | GitHub API / 私有仓库访问令牌 | 空 |
| `LLM_API_KEY` | LLM 服务密钥（OpenAI 兼容接口） | 空 |
| `LLM_BASE_URL` | LLM 服务地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | LLM 模型名 | `deepseek-v4-pro` |
| `CODETRACE_CACHE` | git 缓存目录 | `/tmp/codetrace` |
| `CODETRACE_GIT_PROXY` | 显式 git/API 代理 | 空 |
| `CODETRACE_CLONE_THRESHOLD_KB` | 全量 clone 体积阈值 | 1 GB |
| `CODETRACE_HOST` | 后端监听地址 | `127.0.0.1`（容器内须为 `0.0.0.0`） |
| `CODETRACE_CORS_ORIGINS` | 允许的前端来源（逗号分隔） | 本地 5173 |
| `CODETRACE_API_KEY` | 统一 API Key（设置后 /api 需 `X-API-Key` 请求头） | 空（免鉴权） |
| `VITE_CODETRACE_API_KEY` | 前端构建注入的 API Key（与后端一致） | 空 |
| `VITE_CODETRACE_API_BASE` | 前端 API 基址（支持跨域完整地址） | `/api` |
| `PORT` | 部署对外端口 | `8000` |

> 注意：`VITE_CODETRACE_API_KEY` 会打包进前端产物、可被访问者读取，仅作轻量访问控制；真正的安全边界应在上层网络完成（防火墙 / VPN / 鉴权网关）。

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查（免鉴权，供容器编排探测） |
| POST | `/api/trace` | 文件变更追溯 |
| POST | `/api/trace/function` | 函数演化追溯 |
| POST | `/api/trace/class` | 类演化追溯 |
| GET | `/api/repo/files` | 仓库文件树 |
| GET | `/api/repo/symbols` | 符号列表（函数 / 类） |
| GET | `/api/repo/file-risks` | 文件风险 |
| GET | `/api/repo/dashboard` | 仓库体检仪表盘 |
| GET | `/api/repo/git-graph` | Git Graph（分支拓扑 + 提交 DAG） |
| GET | `/api/repo/pr-info` | PR 信息 |
| GET | `/api/repo/index-status` | 索引进度（SSE） |
| GET | `/api/repo/tracking` | 持续追踪数据 |
| POST | `/api/agent/analyze` | 全量 Agent 分析（SSE 流式） |
| POST | `/api/graph/analyze` | Graph 分析 |
| POST | `/api/agent/ask` | "问 Agent"轻量入口（SSE 流式） |

启用 API Key 后，除 `/healthz` 外所有接口需携带 `X-API-Key` 请求头。

## 测试与质量

```bash
# 后端：lint + 安全相关测试
cd backend && uv run ruff check . && uv run pytest tests/test_security.py -q

# 后端：CI 离线测试子集（不依赖网络 / LLM）
uv run pytest tests/test_ast_service.py tests/test_schemas.py tests/test_security.py \
  tests/test_tools_thresholds.py tests/test_git_graph.py tests/test_index_service.py \
  tests/test_routers.py tests/test_tracking.py -q

# 前端：lint + 构建
cd frontend && npm run lint && npm run build
```

CI（GitHub Actions）自动执行：后端 ruff + 离线测试子集、前端 oxlint + 构建、Docker 镜像构建验证；每个 PR 还会触发 AI Code Review。

## Docker 部署

### 1. 准备环境变量

```bash
cp deploy.env.example .env
```

按需填写 `GITHUB_TOKEN`、`LLM_API_KEY` 等；如需启用 API Key 鉴权，同时设置 `CODETRACE_API_KEY` 与 `VITE_CODETRACE_API_KEY`（两者保持一致）。

### 2. 启动

```bash
docker compose up -d --build
```

访问 `http://<服务器地址>:8000`。nginx 同源代理 `/api` 到后端（后端不对外暴露端口），SSE 流式响应已配置直通，后端容器内置健康检查（`/healthz`）。

### 3. 升级

```bash
git pull && docker compose up -d --build
```

## 开发约定

- **分支流程**：个人开发统一在 `develop` 分支进行，PR 提往 `main`；合并采用 squash，合并后删除远程 `develop` 并从 `main` 重建。
- **重构缓冲**：连续 3 个 feature commit 后插入 1 个重构 / 清理 commit，技术债随迭代同步偿还，不等到阻塞再大改。
- **AI Code Review 处理准则**：review 是参考、不是命令。看错的（证据不成立的）回帖附证据不改；真实问题才改，MVP 阶段也要把地基搭稳，不以 "by design" 当偷懒借口。

## 项目状态

- 阶段 A · 本地稳定与安全基线 ✅
- 阶段 B · 架构收敛 ✅
- 阶段 C · 上线准备（API Key 鉴权 ✅ / Docker 部署就绪 / GitHub App 待定）
