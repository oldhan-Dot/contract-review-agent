"""流程节点共用的异常定义。"""


class NodeNotImplementedError(NotImplementedError):
    """占位节点不能返回假结果；运行到此处时明确中止。"""
