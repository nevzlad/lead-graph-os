from abc import ABC, abstractmethod
from typing import List, Dict, Any

class BaseCollector(ABC):
    @abstractmethod
    def fetch(self, url: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        pass
