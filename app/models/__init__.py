"""集中导入模型，使 SQLAlchemy 能解析表之间的关联。"""

from app.models.base import Base
from app.models.task import ContractTask
from app.models.user import AppUser

__all__ = ["AppUser", "Base", "ContractTask"]
