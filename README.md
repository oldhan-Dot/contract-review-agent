# 合同审批审查系统

依据 [PRD](docs/合同审批审查系统-PRD.md) 与 [技术方案](docs/合同审批审查系统-技术方案设计.md) 逐模块实现。当前包含开发环境、用户和任务 ORM 模型、身份与权限依赖、当前用户接口、DOCX 上传与任务创建、LangGraph 流程框架、健康检查和 Vue 环境检查页；合同解析、规则审查、流程业务节点和审批 Mock 业务接口尚未实现。

模块说明：[用户和任务模型](docs/开发记录/01-用户和合同任务模型.md)、[用户身份与权限](docs/开发记录/02-用户身份与权限.md)、[合同上传与任务创建](docs/开发记录/03-合同上传与任务创建.md)。`GET /api/users/me` 和 `POST /api/tasks/upload` 通过 `X-User-Id`、`X-User-Role` 两个演示身份头访问，具体请求示例及错误码见模块记录。

[LangGraph 流程框架](docs/开发记录/04-LangGraph流程框架.md) 已定义解析到结果保存的五个节点；默认节点尚未实现，执行会明确中止，上传接口尚未触发该图。

[解析结果数据结构](docs/开发记录/05-解析结果数据结构.md) 已定义统一文档、段落、样式和定位信息，并校验正文与字符位置的一致性；实际 DOCX 读取待下一模块实现。

## 环境

- Python 3.11，uv 管理依赖，`uv.lock` 固定实际版本。
- MySQL 8.0.36，Docker Compose 部署，端口 `127.0.0.1:3307`。
- Vue 3.4、Vite 5、Vue Router 4、Pinia 2、Element Plus 2。
- Node.js 20+；本机优先复用已安装的 Node 24，实际兼容性以构建验证为准。

PowerShell 脚本将 uv 缓存和 Python 放在本项目 `.cache/`、`.runtime/` 内。MySQL 使用 Docker 命名卷保存数据，避免 Windows 宿主目录权限问题。技术文档中的 DaoCloud 镜像源本次返回 502，已改用官方 `mysql:8.0.36`；可通过 `.env` 的 `MYSQL_IMAGE` 切换镜像源。

## 部署与启动（PowerShell 7）

先启动 Docker Desktop 并切换到 Linux containers。在项目根目录执行：

```powershell
pwsh -File scripts/setup.ps1
```

脚本仅在 `.env` 不存在时创建配置并随机生成数据库密码，不覆盖已有配置。`uv.lock`、`frontend/package-lock.json` 已提供时使用 `uv sync --locked` 和 `npm ci`。仅安装语言环境可加 `-SkipDatabase`。

分别在两个终端启动：

```powershell
pwsh -File scripts/start-backend.ps1
pwsh -File scripts/start-frontend.ps1
```

访问 [环境检查页](http://127.0.0.1:5173)、[API 文档](http://127.0.0.1:8000/docs)、[健康检查](http://127.0.0.1:8000/api/health)。

```powershell
pwsh -File scripts/check-env.ps1
```

健康检查的顶层 `status=ok` 仅表示基础数据库环境可用；模型、MinerU、审批能力必须分别查看对应字段。`disabled`、`pending`、`unverified` 和 `reachable` 不代表已完成业务验收。

## 模型与 MinerU 配置

在根目录 `.env` 填写真实服务信息：

```dotenv
LLM_ENABLED=1
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL_NAME=your-model-name
LLM_API_KEY=
MINERU_ENABLED=1
MINERU_API_TOKEN=
MINERU_BASE_URL=https://mineru.net/api/v4
MINERU_HEALTH_PATH=
```

API Key 从你实际使用的模型服务商控制台获取并直接写入 `.env`。文档中的 `deepseek-flash` 是待验证配置值，需要替换为服务商实际提供的模型 ID。

`MINERU_BASE_URL` 是 MinerU 云 API 的版本根地址，`MINERU_API_TOKEN` 用于 Bearer 认证，具体任务路径由后续 MinerU 客户端拼接。配置变量统一使用 `MINERU_` 前缀，表示文档解析服务；Milvus 是向量数据库。

云 API 的 `MINERU_HEALTH_PATH` 默认留空，当前健康检查显示「地址和 Token 已配置，解析接口待接入验证」，不调用假定的 `/health`，也不创建解析任务。只有实际服务明确提供健康端点时才填写该路径。环境骨架不包含本地 MinerU 服务或模型权重。

本阶段新增显式 `LLM_ENABLED=0` 开关，允许在等待服务信息时启动环境检查；启用模型后，缺少密钥将拒绝启动，遵循技术方案 KEY-01。启用 MinerU 后也必须配置 Token。修改 `.env` 后重启后端。

`.env`、日志、缓存及运行数据已加入 `.gitignore`，不要提交真实密钥。前端代理地址可写入 `frontend/.env` 的 `API_PROXY_TARGET`；代理默认访问 `http://127.0.0.1:8000`。

## 数据库初始化

`sql/ddl/001_schema.sql` 来自技术方案第 9.2 节，仅将建表改为 `CREATE TABLE IF NOT EXISTS` 以支持重复初始化。文档实际列出 **21 张表**（包含两张 LangGraph 表），与实施计划中「20 张表」的文字计数存在差异，以 DDL 为准。

```powershell
docker compose up -d --wait mysql
. ./scripts/common.ps1
uv run --locked python -m app.cli init-db
uv run --locked python -m app.cli seed
```

直接执行 uv 时，请在同一终端先点源脚本 `. ./scripts/common.ps1`，以使用项目缓存路径。`init-db` 可重复执行，预置 `legal`、`biz`、`admin` 三个账号；规则、模板和审批单种子属于后续业务开发。现有表结构变更需要另写迁移，重复初始化不会修改已有表。

停止数据库使用 `docker compose stop mysql`，保留命名卷数据。前后端在启动终端按 Ctrl+C 停止。
