# 28 — MinerU 扫描 PDF 解析与证据接入

日期：2026-10-15。需求见 [PRD](../PRD.md)，技术实现见 [Spec](../SPEC.md)。

## 本模块完成

扫描 PDF 上传后，文本通道发现无可靠文本层，自动进入 MinerU 云端 OCR；识别结果转换为现有 IR，再进入字段/条款、规则和模型审查。复用原 PDF 分页、风险框和字段定位。

[合成扫描任务 470](http://127.0.0.1:5173/tasks/470) 当前版本 2，PDF_OCR、2 页、4 个条款，已 completed。模型复验后有主体信息缺失及验收/付款条件风险；金额数字被 OCR 拆分时未写入错误标准金额。

扫描样例由程序把合成文本 PDF 渲染成图片，再生成没有文本层的两页 PDF。真实调用 MinerU 和模型，仅发送合成内容，没有发送真实业务合同。本模块没有进行 Git 提交/推送。

## 真实协议与来源

已读取并对照 [MinerU 官方 API 文档](https://mineru.net/apiManage/docs) 和 [输出格式说明](https://opendatalab.github.io/MinerU/reference/output_files/)，并以本次真实 ZIP 返回验证格式。

| 步骤 | 云 API / 返回 |
|---|---|
| 申请上传地址 | POST /api/v4/file-urls/batch，单文件、is_ocr=true、model_version=vlm |
| 上传 | 对返回的签名地址 PUT 原件字节，不携带 API Token |
| 轮询 | GET /api/v4/extract-results/batch/{batch_id}，等待 done/failed |
| 下载 | 读取 full_zip_url，不携带 API Token，不自动跟随重定向 |
| 转换 | ZIP 中 *_content_list.json 的文字、page_idx、0～1000 bbox；layout.json 提供页序与页面尺寸 |

单文件接口不能直接上传本地文件，因此采用批量上传接口提交一个文件。未使用未经验证的 /ocr 或 /health 假定路径。

## 文件职责与节点流程

| 文件 | 职责 |
|---|---|
| app/parsing/mineru_client.py | 配置校验、上传/轮询/下载、任务号与结果缓存、有限等待及错误脱敏 |
| app/parsing/ocr_channel.py | 已验证内容列表到 PDF_OCR IR，保留文字、页码和内容框 |
| app/graph/nodes/parse_document.py | PDF 先读文本；仅 OCR_REQUIRED 分流云端，不将加密/损坏自动上传 |
| app/structuring 与结构化节点 | 支持 PDF_OCR；Anchor 保留扫描 PDF 页号与内容块位置 |
| app/structuring/money_metadata.py | 排除 OCR 拆分数字候选，提示人工核对，防止把 100000 识别成 1 |
| app/review/engine.py、提示词 | 向模型传来源格式/解析警告，不据疑似 OCR 错误推断原合同问题 |
| 工作台和页图接口 | 支持 PDF_OCR 预览，明确显示识别警告与内容块精度 |

只有 PDF 通道明确返回 OCR_REQUIRED 才调用 MinerU。尚未支持 PNG/JPG 等图片直接上传；文本 PDF 中未触发扫描检测的复杂图文混排也不能保证全部识别。

## 位置、文字与置信度

正文逐字保留 MinerU 识别结果，不静默补字、删除数字空格或改变金额。IR 字符区间相对于“识别文本”，并不证明原件字形完全一致。

内容框由 0～1000 归一化坐标按原页宽高转换，核对返回页数/尺寸，异常页号或位置拒绝继续。当前用 LineBox 字段存整块范围，精度是内容块（PARAGRAPH），不是字符级/真实文本行框。OCR 表格从 table_body 提取纯文字，不执行 HTML；复杂表格结构仍待完善。

实际 vlm 返回未提供可靠置信度，ocr_avg_confidence 保持空，不编造 0.99，也不把 MinerU 阈值配置当作已经执行的质量评分。页面及报告解析数据保留人工核对提示；未来有可信分数后再实施阈值判断。

真实样例中的金额被识别成“1 0 0 0 0 0 . 0 0”。相关数字候选不参与标准金额提取，字段显示未识别并附警告，不将某一数字写入任务金额。模型首轮曾把这一识别现象当作原合同金额书写风险，传入来源/质量警告并调整提示词后，复验未再报该项。

## 缓存、超时与安全

- 已上传的 batch_id 按原件 SHA-256 存入 data/renders/mineru，超时后重试可继续查询，避免重复上传；完成的原始结构结果按哈希缓存，重审时重新生成当前 file_id 的 IR。
- Token 仅用于配置的 API 域名请求；不发送到签名上传/下载地址。持久任务记录不保存签名 URL，日志/用户错误不回显服务正文、认证头或原件内容。
- HTTP 超时沿用 MINERU_TIMEOUT_SECONDS；新增 MINERU_TASK_TIMEOUT_SECONDS 默认 180 秒，轮询间隔默认 2 秒。等待有界，超时显示 OCR_TIMEOUT，不无限阻塞后台。
- 下载上限 30MB，ZIP 声明展开总量 100MB、成员数 2000、单 JSON 20MB；仅读取已知成员，不解压 ZIP 路径到文件系统。
- 缺配置 OCR_NOT_CONFIGURED；服务/认证/连接失败 OCR_FAILED；未知 ZIP/字段/页码/坐标 OCR_RESULT_INVALID；正文不足仍 EMPTY_BODY。失败进入 blocked，不假装完成。

云请求暂未实现取消接口；本地等待超时不表示云任务被取消。缓存格式为当前 v1，后续协议变化需更新适配/缓存版本；管理员重试不会自动修复不支持的新协议。

## 必要验证

- 真实云端分配、上传、排队、完成和 ZIP 下载成功；生产客户端在任务 470 中再次实际走通云协议。
- 实际扫描任务完成 OCR → 结构化 → 规则/模型；第二次重审复用已识别结果缓存，没有重新提交 OCR 文件。
- 5 项 OCR 定向用例：协议/凭据隔离/缓存、坐标及金额保护、未配置/坏结果、HTTP 错误、超时；另结合 PDF/结构化定向检查共 29 项通过。没有重跑全量测试。
- 真实浏览器检查扫描页图、翻第二页、卡片回到对应页、证据框反向选卡片、甲方字段定位和缩放，运行时异常 0；查看截图确认蓝框与扫描图中文字对应。
- Ruff、前端构建通过。截图和云端试验材料仅存本地忽略目录，密钥未写入文档或 Git。

仍未完成真实扫描合同、严重模糊、复杂表格/旋转混排的效果验收，单个合成样本不代表 OCR 准确率达标。下一步可补图片直接上传，或继续 Word 转换映射；原任务书最终范围不因本次仅做扫描 PDF 而删减。
