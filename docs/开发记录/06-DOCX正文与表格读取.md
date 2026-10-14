# 模块 06：DOCX 正文与表格读取

新增 `app/parsing/docx_channel.py`，入口为 `parse_docx(path, file_id=...)`，返回上一模块定义的 `ParsedDocument`。本模块直接读取原件，不修改数据库，也不执行其他审查节点。

## 读取顺序

1. 检测文件能否打开、是否加密，随后交给 `python-docx`。
2. 按 Word 正文 XML 的物理顺序遍历段落和表格，保持表格前后文字的相对位置。
3. 表格按行和列读取单元格段落；合并单元格只读取一次，嵌套表格保持在原位置。
4. 过滤空白段落，保留非空段落中的原始空格、换行、文字样式、标题级别和表格标记。
5. 按段落拼接正文，生成连续编号和字符区间，由 IR 模型验证一致性。

仅遍历正文，不读取页眉页脚。DOCX 不生成页码或像素坐标。`Heading 1`～`Heading 6` 和「标题 1」～「标题 6」样式映射为标题级别，其他自定义样式保留名称，但本模块不推测其标题级别。

## 失败处理

`app/parsing/exceptions.py` 定义 `ParseBlocked`，包含 `reason` 和可展示的 `detail`：

| 原因 | 触发情况 |
|---|---|
| `ATTACHMENT_MISSING` | 原件路径不存在 |
| `ATTACHMENT_CORRUPT` | 文件无法读取、不是合法 DOCX 或包结构损坏 |
| `ENCRYPTED_FILE` | Word 文件有密码加密 |
| `EMPTY_BODY` | 没有非空段落，或有效正文少于技术方案规定的 50 字符 |
| `PARSE_ERROR` | 未预期的解析异常 |

加密识别使用技术方案提到的 `msoffcrypto-tool`，已加入 `pyproject.toml` 和 `uv.lock`。解析器抛出这些异常后，未来的流程执行器负责写入任务的 `blocked` 状态；当前还没有接入执行器。

当前上传接口会先进行 DOCX ZIP 结构校验，因此加密容器可能在上传时被拒绝；本次加密检测验证的是直接调用解析器时的行为，不代表已完成加密文件上传后的阻塞状态流转。

技术方案要求保留空标题，但 IR 要求非空段落和非零长度字符区间。这里跳过空标题并在质量报告记录警告，避免生成无文字、不可定位的条款。超长单条条款的送检截断由后续切分/审查模块处理，本模块不截断全文。

## 手工调用

把自己的测试合同放到 `data/samples/` 后，可以在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -c "from app.parsing.docx_channel import parse_docx; result = parse_docx('data/samples/采购合同.docx', file_id=1); print(result.quality.model_dump()); print(len(result.paragraphs))"
```

直接调用中的 `file_id=1` 仅作为结果标识，不会查询数据库，也不会创建任务。使用真实业务文件时，下一步节点会从数据库记录取得正确的文件 ID 和路径。

## 验证和下一步

新增 12 个测试通过，使用临时生成的实际 DOCX 文件验证正文表格顺序、样式、合并与嵌套表格、页眉页脚排除、标题层级、原文区间和错误原因。加密测试实际生成带密码的 Word 文件。连同 IR 校验共 29 passed，代码和格式检查通过。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_docx_channel.py tests/unit/test_parsing_ir.py -q
```

下一步把 `parse_docx` 接入 LangGraph 的 `parse_document` 节点，查询 `ContractFile` 获取原件路径，并将解析结果写入共享状态。后台执行、数据库任务状态流转和其他节点仍待对应模块实现。
