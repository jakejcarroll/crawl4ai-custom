"""
Market Intelligence Module for Crawl4AI

This module provides tools for collecting structured market data on SaaS products.
It includes:
- SaaSHub API client for discovering products and alternatives
- URL discovery for extracting homepage URLs from SaaSHub pages
- Pydantic schemas for LLM-based structured extraction
- State management for resumable, incremental collection runs
- Orchestrator for rate-limit-aware batch processing
"""

from .saashub import SaaSHubClient, SaaSHubAPIError
from .schemas import SaaSProductInfo
from .state import CollectionState
from .url_discovery import discover_homepage_urls
from .collect import MarketIntelCollector

__all__ = [
    "SaaSHubClient",
    "SaaSHubAPIError",
    "SaaSProductInfo",
    "CollectionState",
    "discover_homepage_urls",
    "MarketIntelCollector",
]
