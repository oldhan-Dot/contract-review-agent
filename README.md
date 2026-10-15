# 合同审批审查系统

合同上传、解析、规则与模型辅助审查、法务复核、模拟审批回写及报告导出。

## 文档入口

| 文档 | 阅读目的 |
|---|---|
| [PRD](docs/PRD.md) | 唯一产品需求：范围、权限、功能、验收、阶段进度 |
| [Spec](docs/SPEC.md) | 唯一技术设计：架构、数据、接口、图流程和开发顺序 |
| [开发记录](docs/开发记录/) | 每个模块的代码解释与验证记录，供逐步学习 |
| [体验入口](docs/开发记录/22-前端整体工作台与体验入口.md) | 页面操作和本地启动方式 |
| [试验材料](docs/试验/) | 合成合同及独立模型探针，不属于另一套需求/设计 |

新旧 PRD、Spec 草稿、简版、决策记录和开发清单已经合并，旧内容可从 Git 历史找回。后续只更新上面两份规格，模块完成后继续补开发记录。

## 当前可体验内容

DOCX/文本型 PDF 上传解析、条款/字段提取、首批 7 条规则、模型调用、后台执行与快照、任务大盘、文字证据定位、法务草稿/确认、数据库内模拟回写、Markdown/PDF 报告已有实现。PDF 已保留真实页码/行坐标，原件分页绘制和扫描件 OCR 尚未接入；完整规则模板/审批接入、真实合同效果等仍待验证或补齐，详见 PRD 第 10 章。

[文本 PDF 合成任务 468](http://127.0.0.1:5173/tasks/468) 已实际完成审查。本模块的解析边界与验证见[开发记录 26](docs/开发记录/26-文本型PDF解析与流程接入.md)。

本机页面：[工作台](http://127.0.0.1:5173/)。8000 被其他项目占用时，使用 [8001 API 文档](http://127.0.0.1:8001/docs)。[合成任务 413](http://127.0.0.1:5173/tasks/413) 已实际完成模型审查、合成确认、模拟回写和报告导出；真实合同效果尚未验收。运行进程临时启用了模型和 90 秒超时，.env 未改，后续重启的配置及验证详情见[开发记录 25](docs/开发记录/25-真实模型审查与完整链路联调.md)。

合成 DOCX 真实规则/模型、法务草稿/权限/冲突/确认、模拟回写及报告接口联调通过；此前定向 4 项图执行/模拟回写测试、Ruff、Vite 构建通过；未完成浏览器视觉验收和真实合同效果验收。单个合成样本调试不代表总体审查准确率。

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

访问 [工作台](http://127.0.0.1:5173/)（[环境检查](http://127.0.0.1:5173/environment)）、[API 文档](http://127.0.0.1:8000/docs)、[健康检查](http://127.0.0.1:8000/api/health)。

页面首页现为合同大盘，环境检查位于 `/environment`。8000 已被占用时，可分别使用 `scripts/start-backend.ps1 -Port 8001` 和 `scripts/start-frontend.ps1 -BackendPort 8001` 启动。

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

直接执行 uv 时，请在同一终端先点源脚本 `. ./scripts/common.ps1`，以使用项目缓存路径。`init-db` 可重复执行，预置 `legal`、`biz`、`admin` 三个账号。本轮后台启动会额外执行 `002_workspace.sql` 新建两张快照/规则配置表，不修改旧表。首批规则与示范文本由代码提供，模拟审批单通过工作台关联创建。现有表结构变更需要另写迁移，重复初始化不会修改已有表。

停止数据库使用 `docker compose stop mysql`，保留命名卷数据。前后端在启动终端按 Ctrl+C 停止。
