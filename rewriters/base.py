from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseRewriter(ABC):
    @abstractmethod
    def rewrite(self, content: str, config: Dict[str, Any]) -> str:
        pass
