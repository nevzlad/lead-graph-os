import logging
from typing import Any, Dict, Optional

from .base import BaseRewriter
from .presets import NICHE_PRESETS

logger = logging.getLogger(__name__)

class NicheRewriter(BaseRewriter):
    """
    Rewrites content based on niche-specific presets.
    Adapts tone, style, keywords, and length according to target niche.
    """
    
    def __init__(self):
        self.presets = NICHE_PRESETS
    
    def rewrite(self, content: str, config: Dict[str, Any]) -> str:
        """
        Rewrite content with niche-specific adaptations.
        
        Args:
            content: Original content to rewrite
            config: Config dict with 'niche' and optional 'style_adjustments'
        
        Returns:
            Rewritten content adapted to niche
        """
        niche = config.get("niche", "tech").lower()
        
        if niche not in self.presets:
            logger.warning(f"Unknown niche: {niche}. Using 'tech' as default.")
            niche = "tech"
        
        preset = self.presets[niche]
        
        # Trim content to max_length
        max_length = preset["max_length"]
        if len(content) > max_length:
            content = content[:max_length].rsplit(' ', 1)[0] + " ..."
        
        # Enhance with niche-specific keywords (simple implementation)
        enhanced = self._enhance_with_keywords(content, preset["keywords"])
        
        # Apply tone and style markers (for downstream processing)
        result = f"[TONE:{preset['tone']}][STYLE:{preset['style']}]\n{enhanced}"
        
        return result
    
    def _enhance_with_keywords(self, content: str, keywords: list) -> str:
        """
        Optionally enhance content by highlighting or injecting niche keywords.
        """
        # Simple keyword injection: add a keyword summary at the beginning
        keyword_str = ", ".join(keywords[:3])
        return f"[Key topics: {keyword_str}]\n{content}"
    
    def get_preset(self, niche: str) -> Optional[Dict[str, Any]]:
        """
        Get preset configuration for a specific niche.
        """
        return self.presets.get(niche.lower())
    
    def list_niches(self) -> list:
        """
        List all available niches.
        """
        return list(self.presets.keys())
