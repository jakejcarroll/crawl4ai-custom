"""
Market Intelligence Module for Crawl4AI

This module provides tools for collecting structured market data on SaaS products.
It includes:
- Product Hunt API client for discovering trending/popular products
- SaaSHub API client for alternative product discovery  
- Intelligent rate limit handling with auto-recovery
- URL-based deduplication across sources
- URL discovery for extracting homepage URLs from source pages
- Pydantic schemas for LLM-based structured extraction
- Target list management with completion tracking
- Two-phase collection: target building → data extraction
"""

from .saashub import SaaSHubClient, SaaSHubAPIError, RateLimitError as SaaSHubRateLimitError
from .producthunt import (
    ProductHuntClient, 
    ProductHuntAPIError,
    ProductHuntProduct,
    ProductHuntTopic,
    PRIORITY_TOPICS,
)
from .rate_limiter import RateLimiter, RateLimitSource, RateLimitConfig, RATE_LIMIT_CONFIGS
from .targets import TargetManager, Target, TargetStatus
from .schemas import SaaSProductInfo, CollectedProduct
from .state import CollectionState, ProductState
from .url_discovery import discover_homepage_urls
from .collect import MarketIntelCollector, ProductHuntCollector

__all__ = [
    # SaaSHub (legacy)
    "SaaSHubClient",
    "SaaSHubAPIError",
    "SaaSHubRateLimitError",
    # Product Hunt
    "ProductHuntClient",
    "ProductHuntAPIError",
    "ProductHuntProduct",
    "ProductHuntTopic",
    "PRIORITY_TOPICS",
    # Rate limiting
    "RateLimiter",
    "RateLimitSource",
    "RateLimitConfig",
    "RATE_LIMIT_CONFIGS",
    # Target management
    "TargetManager",
    "Target",
    "TargetStatus",
    # Schemas
    "SaaSProductInfo",
    "CollectedProduct",
    # State (legacy)
    "CollectionState",
    "ProductState",
    # URL discovery
    "discover_homepage_urls",
    # Collectors
    "MarketIntelCollector",
    "ProductHuntCollector",
]
