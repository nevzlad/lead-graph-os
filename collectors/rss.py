import logging
from typing import Any, Dict, List

import feedparser

from .base import BaseCollector

logger = logging.getLogger(__name__)

class RSSCollector(BaseCollector):
    def fetch(self, url: str, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                raise ValueError(f"Invalid RSS feed or network error: {feed.bozo_exception}")
                
            items = []
            max_items = int(config.get("max_items", 10))
            
            for entry in feed.entries[:max_items]:
                items.append({
                    "title": entry.get("title", "Без заголовка").strip(),
                    "content": entry.get("summary", entry.get("description", "")).strip(),
                    "link": entry.get("link", ""),
                    "published_raw": entry.get("published_parsed", None)
                })
            return items
        except Exception as e:
            logger.error(f"RSS fetch failed for {url}: {e}")
            raise
