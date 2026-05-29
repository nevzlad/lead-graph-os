from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseRewriter(ABC):
    @abstractmethod
    def rewrite(self, content: str, config: Dict[str, Any]) -> str:
        pass
