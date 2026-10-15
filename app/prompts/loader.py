"""加载项目内的提示词文件，不依赖启动命令所在目录。"""

from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
PROMPT_FILES = {
    "contract_review": "contract_review.txt",
    "model_probe": "model_probe.txt",
}


def load_prompt(name: str) -> str:
    """按已登记名称读取 UTF-8 文本；每次读取，修改后下一次调用即可生效。"""
    # 只接受登记名称，调用方不能传入任意文件路径。
    if name not in PROMPT_FILES:
        raise ValueError(f"未登记的提示词：{name}")
    prompt = (PROMPT_DIR / PROMPT_FILES[name]).read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"提示词内容为空：{name}")
    return prompt
