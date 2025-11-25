"""
Market Intelligence Module for Crawl4AI

This module provides tools for collecting structured market data on SaaS products.
It includes:
- Multi-source discovery (SaaSHub API, Product Hunt API)
- Intelligent rate limit switching between sources
- URL-based deduplication across sources
- URL discovery for extracting homepage URLs from source pages
- Pydantic schemas for LLM-based structured extraction
- State management for resumable, incremental collection runs
- Topic mapping between data sources
- Orchestrator for multi-source, rate-limit-aware batch processing
"""

from .saashub import SaaSHubClient, SaaSHubAPIError, RateLimitError as SaaSHubRateLimitError
from .producthunt import (
    ProductHuntClient, 
    ProductHuntAPIError, 
    ProductHuntRateLimitError,
    PHProduct,
)
from .discovery import MultiSourceDiscovery, DiscoveryResult
from .rate_limiter import MultiSourceRateLimiter, DataSource, RateLimitConfig
from .topic_mapper import TopicMapper, CategoryMapping, SourceQuery
from .schemas import SaaSProductInfo, CollectedProduct
from .state import CollectionState, ProductState
from .url_discovery import discover_homepage_urls
from .collect import MarketIntelCollector

__all__ = [
    # SaaSHub
    "SaaSHubClient",
    "SaaSHubAPIError",
    "SaaSHubRateLimitError",
    # Product Hunt
    "ProductHuntClient",
    "ProductHuntAPIError",
    "ProductHuntRateLimitError",
    "PHProduct",
    # Multi-source discovery
    "MultiSourceDiscovery",
    "DiscoveryResult",
    # Rate limiting
    "MultiSourceRateLimiter",
    "DataSource",
    "RateLimitConfig",
    # Topic mapping
    "TopicMapper",
    "CategoryMapping",
    "SourceQuery",
    # Schemas
    "SaaSProductInfo",
    "CollectedProduct",
    # State
    "CollectionState",
    "ProductState",
    # URL discovery
    "discover_homepage_urls",
    # Collector
    "MarketIntelCollector",
]
