# 合同审批审查系统

正在依据 [重整 PRD](docs/需求重整/01-产品需求文档-重整草稿.md) 与 [重整 spec](docs/需求重整/02-技术设计规格-重整草稿.md) 开发，旧文档保留作历史参考。当前已有可运行的 DOCX 工作台：上传、后台解析和结构化、7 条初筛规则、模型适配器、结果保存、任务大盘、原文证据定位、法务草稿与确认、数据库内模拟审批回写，以及 Markdown/PDF 报告。真实模型效果尚未验证；PDF/OCR 输入、原始分页预览、外部审批接入等仍待完成。

本次体验页面：[合同工作台](http://127.0.0.1:5173/)，后端因 8000 端口被占用使用 [8001 API 文档](http://127.0.0.1:8001/docs)。业务身份点击“体验合成样例”，切换法务可编辑草稿和导出报告。当前模型未启用，任务会明确显示 AI_NOT_CONFIGURED，保留真实规则结果，不能确认完整审查。

本轮模块说明：[规则与后台执行](docs/开发记录/20-规则模型审查与后台执行.md)、[复核回写与报告](docs/开发记录/21-人工复核模拟回写与报告.md)、[前端体验入口](docs/开发记录/22-前端整体工作台与体验入口.md)。合成样例真实 MySQL 接口联调通过；定向 4 项测试及前端构建通过，尚未完成浏览器视觉验收。

模块说明：[用户和任务模型](docs/开发记录/01-用户和合同任务模型.md)、[用户身份与权限](docs/开发记录/02-用户身份与权限.md)、[合同上传与任务创建](docs/开发记录/03-合同上传与任务创建.md)。`GET /api/users/me` 和 `POST /api/tasks/upload` 通过 `X-User-Id`、`X-User-Role` 两个演示身份头访问，具体请求示例及错误码见模块记录。

[新版上传调整](docs/开发记录/18-送审信息与上传权限调整.md)：上传仅限业务身份，必填文件、合同类型与我方角色，名称可空；去重区分送审上下文。本轮合成 DOCX 已通过真实上传接口保存并后台处理；未重跑上传模块全量异常用例。

[送审上下文传递](docs/开发记录/19-审查输入的送审上下文.md)：解析节点从任务记录载入类型/我方角色，经 State 传到规则输入；不默认未知值。历史单元检查 477 passed，本轮后台真实处理已验证上下文传递。

[LangGraph 流程框架](docs/开发记录/04-LangGraph流程框架.md) 已接入解析、结构化、规则、AI 和结果保存五个节点。上传提交 pending 后，后台执行器领取并执行图，失败时保存部分结果及受阻原因。

[解析结果数据结构](docs/开发记录/05-解析结果数据结构.md) 已定义统一文档、段落、样式和定位信息，并校验正文与字符位置的一致性。[DOCX 正文与表格读取](docs/开发记录/06-DOCX正文与表格读取.md) 保留原文顺序和样式；[解析节点](docs/开发记录/07-文档解析节点接入.md) 已接入后台执行。

[条款数据结构](docs/开发记录/08-条款数据结构与原文校验.md) 已定义条款、分类代码及原文一致性校验；[条款切分器](docs/开发记录/09-条款切分器.md) 已实现编号、标题和长条款切分；[条款归类](docs/开发记录/10-条款归类.md) 已实现标题、关键词和父类型判定，真实模型兜底尚未接入。

[字段提取结果结构](docs/开发记录/11-字段提取结果结构.md) 已定义字段目录、标准值、原文、缺失状态和证据位置校验；[主体信息提取](docs/开发记录/12-主体名称与信用代码提取.md) 已支持明确甲乙方标签下的名称、信用代码和证据位置；[金额与币种提取](docs/开发记录/13-金额与币种提取.md) 已支持数字/中文金额、四种币种和大小写冲突提示；[日期与期限提取](docs/开发记录/14-日期与期限提取.md) 已支持履行起止日期、期限天数、生效条件及签署日期；[合同名称与编号提取](docs/开发记录/15-合同名称与编号提取.md) 已补齐剩余两个字段及可选任务名兜底。[结构化节点](docs/开发记录/16-结构化节点接入.md) 已按切分、归类、字段提取顺序接入图，输出条款与字段结果。

[风险结果结构与审查输入](docs/开发记录/17-风险结果结构与审查输入.md) 定义规则输入、三类风险、等级与来源，以及原文证据和执行版本校验。本轮规则与模型适配器复用该结构，结果按任务/审查版本保存快照。

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
