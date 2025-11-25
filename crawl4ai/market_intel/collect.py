#!/usr/bin/env python3
"""
Market Intelligence Collector

Two-phase collection system for SaaS product market data:

Phase 1 - Target Building (target-build):
  - Query Product Hunt API for trending/popular SaaS products
  - Filter for validated products (votes >= 20)
  - Build targets.jsonl with URLs and metadata

Phase 2 - Data Extraction (extract):
  - Read pending targets from targets.jsonl
  - Scrape product homepages for detailed features
  - Extract structured data using LLM (GPT-4o)
  - Mark targets as completed/failed

Legacy mode (default) uses SaaSHub API for backwards compatibility.

Designed for resumable runs with rate limit auto-recovery.
"""

import os
import sys
import json
import asyncio
import argparse
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

import yaml

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CrawlerRunConfig,
    CacheMode,
    LLMExtractionStrategy,
)
from crawl4ai.async_configs import LLMConfig

from .saashub import SaaSHubClient, SaaSHubAPIError, RateLimitError
from .schemas import (
    SaaSProductInfo,
    CollectedProduct,
    EXTRACTION_INSTRUCTION,
    get_extraction_schema,
)
from .state import CollectionState, ProductState
from .url_discovery import discover_homepage_single
from .producthunt import ProductHuntClient, ProductHuntProduct, PRIORITY_TOPICS
from .targets import TargetManager, Target, TargetStatus
from .rate_limiter import RateLimiter, RateLimitSource


# Default paths
DEFAULT_STATE_PATH = Path("data/market_intel_state.json")
DEFAULT_OUTPUT_PATH = Path("data/market_intel_products.jsonl")
DEFAULT_SEEDS_PATH = Path("configs/market_intel_seeds.yml")
DEFAULT_TARGETS_PATH = Path("targets/targets.jsonl")


class MarketIntelCollector:
    """
    Orchestrator for market intelligence collection.
    
    Handles:
    - Rate-limited SaaSHub API queries
    - Homepage URL discovery
    - LLM-based structured extraction
    - Resumable state management
    - Automatic halt on persistent rate limit errors
    """
    
    def __init__(
        self,
        state_path: Path = DEFAULT_STATE_PATH,
        output_path: Path = DEFAULT_OUTPUT_PATH,
        seeds_path: Path = DEFAULT_SEEDS_PATH,
        saashub_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        saashub_delay: float = 12.0,  # ~5 req/min
        llm_provider: str = "openai/gpt-4o",
        batch_size: int = 5,
        max_products_per_seed: int = 50,
        verbose: bool = False,
    ):
        """
        Initialize the collector.
        
        Args:
            state_path: Path to state file for resumable runs
            output_path: Path to JSONL output file
            seeds_path: Path to seed queries YAML config
            saashub_api_key: SaaSHub API key (or from env SAASHUB_API_KEY)
            openai_api_key: OpenAI API key (or from env OPENAI_API_KEY)
            saashub_delay: Seconds between SaaSHub API requests
            llm_provider: LLM provider string (e.g., "openai/gpt-4o")
            batch_size: Number of concurrent extractions
            max_products_per_seed: Max alternatives to fetch per seed query
            verbose: Enable verbose output
        """
        self.state_path = Path(state_path)
        self.output_path = Path(output_path)
        self.seeds_path = Path(seeds_path)
        self.saashub_delay = saashub_delay
        self.llm_provider = llm_provider
        self.batch_size = batch_size
        self.max_products_per_seed = max_products_per_seed
        self.verbose = verbose
        
        # API keys
        self.saashub_api_key = saashub_api_key or os.getenv("SAASHUB_API_KEY")
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        
        # State
        self.state: Optional[CollectionState] = None
        self._crawler: Optional[AsyncWebCrawler] = None
    
    def _log(self, msg: str):
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    
    def load_seeds(self) -> List[str]:
        """Load seed queries from config file."""
        if not self.seeds_path.exists():
            raise FileNotFoundError(f"Seeds config not found: {self.seeds_path}")
        
        with open(self.seeds_path) as f:
            config = yaml.safe_load(f)
        
        # Support flat list or categorized structure
        if isinstance(config, list):
            return config
        elif isinstance(config, dict):
            seeds = []
            for category, items in config.items():
                if isinstance(items, list):
                    seeds.extend(items)
            return seeds
        
        return []
    
    def load_state(self) -> CollectionState:
        """Load or create state for resumable runs."""
        if self.state_path.exists():
            self.state = CollectionState.load(self.state_path)
            self._log(f"Loaded state: {self.state.get_stats()}")
        else:
            self.state = CollectionState.new()
            self._log("Created new collection state")
        return self.state
    
    def save_state(self):
        """Save current state."""
        if self.state:
            self.state.save(self.state_path)
    
    def write_product(self, product: CollectedProduct):
        """Append a product to the JSONL output file."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "a") as f:
            f.write(product.model_dump_json() + "\n")
    
    async def discover_products(self, seeds: Optional[List[str]] = None) -> int:
        """
        Phase 1: Discover products from SaaSHub API.
        
        Args:
            seeds: Optional list of seed queries (defaults to loading from config)
            
        Returns:
            Number of new products discovered
        """
        if seeds is None:
            seeds = self.load_seeds()
        
        if not self.saashub_api_key:
            raise ValueError("SaaSHub API key required. Set SAASHUB_API_KEY env var.")
        
        discovered = 0
        
        with SaaSHubClient(
            api_key=self.saashub_api_key,
            request_delay=self.saashub_delay,
        ) as client:
            for seed in seeds:
                if self.state.is_seed_processed(seed):
                    self._log(f"Skipping already processed seed: {seed}")
                    continue
                
                self._log(f"Querying SaaSHub for alternatives to: {seed}")
                
                try:
                    alternatives = client.get_alternatives(seed, limit=self.max_products_per_seed)
                    
                    for alt in alternatives:
                        attrs = alt.get("attributes", {})
                        name = attrs.get("name", "Unknown")
                        saashub_url = attrs.get("saashubUrl", "")
                        
                        if saashub_url and not saashub_url.startswith("http"):
                            saashub_url = f"https://www.saashub.com{saashub_url}"
                        
                        self.state.add_product(
                            name=name,
                            seed_query=seed,
                            saashub_url=saashub_url,
                            saashub_id=alt.get("id"),
                        )
                        discovered += 1
                    
                    self.state.mark_seed_processed(seed)
                    self.save_state()
                    
                    self._log(f"Discovered {len(alternatives)} products from '{seed}'")
                    
                except RateLimitError as e:
                    self._log(f"SaaSHub rate limit hit: {e}")
                    self.state.halted = True
                    self.state.halt_reason = f"SaaSHub rate limit: {e}"
                    self.save_state()
                    break
                    
                except SaaSHubAPIError as e:
                    self._log(f"SaaSHub API error for '{seed}': {e}")
                    continue
        
        return discovered
    
    async def discover_homepages(self) -> int:
        """
        Phase 2: Discover homepage URLs from SaaSHub pages.
        
        Returns:
            Number of homepages discovered
        """
        products = self.state.get_products_needing_homepage()
        
        if not products:
            self._log("No products need homepage discovery")
            return 0
        
        self._log(f"Discovering homepages for {len(products)} products")
        discovered = 0
        
        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            for i, prod in enumerate(products):
                if self.state.should_halt():
                    self._log("Halting due to previous errors")
                    break
                
                self._log(f"[{i+1}/{len(products)}] Discovering homepage for: {prod.name}")
                
                try:
                    homepage = await discover_homepage_single(
                        name=prod.name,
                        saashub_url=prod.saashub_url,
                        crawler=crawler,
                    )
                    
                    if homepage:
                        prod.homepage_url = homepage
                        prod.homepage_discovered = True
                        discovered += 1
                        self._log(f"  Found: {homepage}")
                    else:
                        self._log(f"  Not found")
                    
                    # Save state periodically
                    if (i + 1) % 10 == 0:
                        self.save_state()
                        
                except Exception as e:
                    self._log(f"  Error: {e}")
        
        self.save_state()
        return discovered
    
    async def extract_product_info(self) -> int:
        """
        Phase 3: Extract structured data from product homepages using LLM.
        
        Returns:
            Number of products successfully extracted
        """
        if not self.openai_api_key:
            raise ValueError("OpenAI API key required. Set OPENAI_API_KEY env var.")
        
        products = self.state.get_products_needing_extraction()
        
        if not products:
            self._log("No products need extraction")
            return 0
        
        self._log(f"Extracting info from {len(products)} product homepages")
        extracted = 0
        
        # Configure LLM extraction strategy
        llm_config = LLMConfig(
            provider=self.llm_provider,
            api_token=self.openai_api_key,
        )
        
        extraction_strategy = LLMExtractionStrategy(
            llm_config=llm_config,
            schema=get_extraction_schema(),
            instruction=EXTRACTION_INSTRUCTION,
            verbose=self.verbose,
        )
        
        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            for i, prod in enumerate(products):
                if self.state.should_halt():
                    self._log(f"Halting: {self.state.halt_reason}")
                    break
                
                product_key = prod.saashub_id or prod.name.lower().replace(" ", "-")
                
                self._log(f"[{i+1}/{len(products)}] Extracting: {prod.name} ({prod.homepage_url})")
                
                try:
                    result = await crawler.arun(
                        url=prod.homepage_url,
                        config=CrawlerRunConfig(
                            extraction_strategy=extraction_strategy,
                            cache_mode=CacheMode.BYPASS,
                            wait_until="domcontentloaded",
                        )
                    )
                    
                    if not result.success:
                        error_msg = result.error_message or "Crawl failed"
                        self._log(f"  Crawl failed: {error_msg}")
                        self.state.mark_extraction_failure(product_key, error_msg)
                        continue
                    
                    # Check for extraction errors
                    if result.extracted_content:
                        try:
                            content = json.loads(result.extracted_content)
                            
                            # Check if it's an error response
                            if isinstance(content, list) and content:
                                first = content[0]
                                if isinstance(first, dict) and first.get("error"):
                                    error_msg = first.get("content", "Unknown error")
                                    is_rate_limit = "rate limit" in str(error_msg).lower()
                                    
                                    self._log(f"  Extraction error: {error_msg}")
                                    
                                    should_halt = self.state.mark_extraction_failure(
                                        product_key, error_msg, is_rate_limit=is_rate_limit
                                    )
                                    
                                    if should_halt:
                                        self._log(f"  HALTING: {self.state.halt_reason}")
                                        self.save_state()
                                        break
                                    continue
                            
                            # Parse as SaaSProductInfo
                            if isinstance(content, list) and content:
                                product_info = SaaSProductInfo.model_validate(content[0])
                            elif isinstance(content, dict):
                                product_info = SaaSProductInfo.model_validate(content)
                            else:
                                raise ValueError(f"Unexpected content format: {type(content)}")
                            
                            # Create collected product
                            collected = CollectedProduct(
                                source="saashub",
                                seed_query=prod.seed_query,
                                discovered_at=prod.discovered_at,
                                homepage_url=prod.homepage_url,
                                saashub_url=prod.saashub_url,
                                product_info=product_info,
                                extraction_success=True,
                                extracted_at=datetime.utcnow().isoformat() + "Z",
                            )
                            
                            # Write to output
                            self.write_product(collected)
                            self.state.mark_extraction_success(product_key)
                            extracted += 1
                            
                            self._log(f"  Success: {product_info.name}")
                            
                        except json.JSONDecodeError as e:
                            self._log(f"  JSON parse error: {e}")
                            self.state.mark_extraction_failure(product_key, f"JSON parse error: {e}")
                            
                        except Exception as e:
                            error_str = str(e)
                            is_rate_limit = "rate limit" in error_str.lower() or "429" in error_str
                            
                            self._log(f"  Parse error: {e}")
                            
                            should_halt = self.state.mark_extraction_failure(
                                product_key, error_str, is_rate_limit=is_rate_limit
                            )
                            
                            if should_halt:
                                self.save_state()
                                break
                    else:
                        self._log(f"  No content extracted")
                        self.state.mark_extraction_failure(product_key, "No content extracted")
                    
                    # Save state periodically
                    if (i + 1) % 5 == 0:
                        self.save_state()
                        
                except Exception as e:
                    error_str = str(e)
                    is_rate_limit = "rate limit" in error_str.lower() or "429" in error_str
                    
                    self._log(f"  Exception: {e}")
                    
                    should_halt = self.state.mark_extraction_failure(
                        product_key, error_str, is_rate_limit=is_rate_limit
                    )
                    
                    if should_halt:
                        self.save_state()
                        break
        
        self.save_state()
        return extracted
    
    async def run(
        self,
        seeds: Optional[List[str]] = None,
        skip_discovery: bool = False,
        skip_homepages: bool = False,
        skip_extraction: bool = False,
    ) -> Dict[str, Any]:
        """
        Run the full collection pipeline.
        
        Args:
            seeds: Optional list of seed queries
            skip_discovery: Skip SaaSHub API discovery phase
            skip_homepages: Skip homepage URL discovery phase
            skip_extraction: Skip LLM extraction phase
            
        Returns:
            Collection statistics
        """
        self.load_state()
        
        if self.state.should_halt():
            self._log(f"Previous run halted: {self.state.halt_reason}")
            self._log("Reset state or fix issue before continuing")
            return self.state.get_stats()
        
        stats = {
            "discovered": 0,
            "homepages": 0,
            "extracted": 0,
        }
        
        # Phase 1: Discover products from SaaSHub
        if not skip_discovery:
            self._log("=== Phase 1: Product Discovery ===")
            stats["discovered"] = await self.discover_products(seeds)
            
            if self.state.should_halt():
                return {**self.state.get_stats(), **stats}
        
        # Phase 2: Discover homepage URLs
        if not skip_homepages:
            self._log("=== Phase 2: Homepage Discovery ===")
            stats["homepages"] = await self.discover_homepages()
            
            if self.state.should_halt():
                return {**self.state.get_stats(), **stats}
        
        # Phase 3: Extract product info
        if not skip_extraction:
            self._log("=== Phase 3: LLM Extraction ===")
            stats["extracted"] = await self.extract_product_info()
        
        final_stats = {**self.state.get_stats(), **stats}
        self._log(f"=== Complete ===")
        self._log(f"Stats: {json.dumps(final_stats, indent=2)}")
        
        return final_stats


class ProductHuntCollector:
    """
    Two-phase collector using Product Hunt as the discovery source.
    
    Phase 1 (target-build): Build target list from Product Hunt API
    Phase 2 (extract): Scrape and extract data from target homepages
    
    Features:
    - Resilient rate limiting with auto-recovery
    - Persistent target list with completion tracking
    - Resumable runs with state preservation
    """
    
    def __init__(
        self,
        targets_path: Path = DEFAULT_TARGETS_PATH,
        output_path: Path = DEFAULT_OUTPUT_PATH,
        producthunt_token: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        llm_provider: str = "openai/gpt-4o",
        min_votes: int = 20,
        verbose: bool = False,
    ):
        """
        Initialize the Product Hunt collector.
        
        Args:
            targets_path: Path to targets.jsonl file
            output_path: Path to JSONL output file
            producthunt_token: Product Hunt API token (or from PRODUCTHUNT_ACCESS_TOKEN)
            openai_api_key: OpenAI API key (or from OPENAI_API_KEY)
            llm_provider: LLM provider string (e.g., "openai/gpt-4o")
            min_votes: Minimum vote threshold for products (default: 20)
            verbose: Enable verbose output
        """
        self.targets_path = Path(targets_path)
        self.output_path = Path(output_path)
        self.llm_provider = llm_provider
        self.min_votes = min_votes
        self.verbose = verbose
        
        # API keys
        self.producthunt_token = producthunt_token or os.getenv("PRODUCTHUNT_ACCESS_TOKEN")
        self.openai_api_key = openai_api_key or os.getenv("OPENAI_API_KEY")
        
        # Rate limiter (shared for both sources)
        self.rate_limiter = RateLimiter()
        
        # Target list manager
        self.targets = TargetManager(self.targets_path)
    
    def _log(self, msg: str):
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    
    async def build_targets(
        self,
        topics: Optional[List[str]] = None,
        max_per_source: int = 100,
        include_trending: bool = True,
        include_popular: bool = True,
        resolve_urls: bool = True,
    ) -> Dict[str, Any]:
        """
        Phase 1: Build target list from Product Hunt.
        
        The Product Hunt API returns tracking/redirect URLs instead of actual
        website URLs. We resolve these by scraping the Product Hunt launch page
        for each product to extract the real homepage URL.
        
        Args:
            topics: Optional list of topics to discover from
            max_per_source: Max products to fetch per source
            include_trending: Include trending products
            include_popular: Include popular products
            resolve_urls: Whether to resolve redirect URLs to actual homepages
            
        Returns:
            Discovery statistics
        """
        if not self.producthunt_token:
            raise ValueError("Product Hunt token required. Set PRODUCTHUNT_ACCESS_TOKEN env var.")
        
        stats = {
            "discovered": 0,
            "resolved": 0,
            "added": 0,
            "duplicates": 0,
            "unresolved": 0,
        }
        
        self._log("=== Building Target List from Product Hunt ===")
        
        # Use topics from priority list if not specified
        topic_list = topics if topics else PRIORITY_TOPICS[:5]  # Top 5 by default
        
        # Collect all products first
        all_products: List[ProductHuntProduct] = []
        
        async with ProductHuntClient(
            access_token=self.producthunt_token,
            rate_limiter=self.rate_limiter,
            min_votes=self.min_votes,
        ) as client:
            # Collect popular products
            if include_popular:
                self._log("Fetching popular products...")
                async for product in client.get_popular_products(limit=max_per_source):
                    all_products.append(product)
                    stats["discovered"] += 1
            
            # Collect trending products
            if include_trending:
                self._log("Fetching trending products...")
                async for product in client.get_trending_products(limit=max_per_source):
                    all_products.append(product)
                    stats["discovered"] += 1
            
            # Collect by topics
            for topic in topic_list:
                self._log(f"Fetching products for topic: {topic}")
                async for product in client.get_products_by_topic(topic, limit=max_per_source):
                    all_products.append(product)
                    stats["discovered"] += 1
            
            # Deduplicate by ID
            seen_ids = set()
            unique_products = []
            for p in all_products:
                if p.id not in seen_ids:
                    seen_ids.add(p.id)
                    unique_products.append(p)
            
            self._log(f"Discovered {len(unique_products)} unique products")
            
            # Resolve actual homepage URLs
            if resolve_urls:
                self._log("Resolving actual homepage URLs (this may take a while)...")
                resolved_urls = await client.resolve_homepage_urls_batch(
                    unique_products,
                    concurrency=3,  # Be gentle with PH servers
                )
                stats["resolved"] = len(resolved_urls)
                self._log(f"Resolved {len(resolved_urls)} homepage URLs")
                
                # Update products with resolved URLs
                for product in unique_products:
                    if product.id in resolved_urls:
                        product.homepage_url = resolved_urls[product.id]
            
            # Add products as targets
            for product in unique_products:
                if self._add_product_as_target(product):
                    stats["added"] += 1
                else:
                    stats["duplicates"] += 1
                
                # Track unresolved
                if not product.homepage_url or "producthunt.com/r/" in (product.homepage_url or ""):
                    stats["unresolved"] += 1
        
        self._log(f"Added {stats['added']} new targets, {stats['duplicates']} duplicates skipped")
        if stats["unresolved"] > 0:
            self._log(f"Warning: {stats['unresolved']} products could not be resolved to actual URLs")
        
        # Get current stats
        list_stats = self.targets.get_stats()
        stats.update({
            "total_targets": list_stats["total"],
            "pending": list_stats["pending"],
            "completed": list_stats["completed"],
        })
        
        return stats
    
    def _add_product_as_target(self, product: ProductHuntProduct) -> bool:
        """Add a ProductHuntProduct as a target. Returns True if added, False if duplicate."""
        # Skip if no homepage URL or if it's still a redirect URL
        if not product.homepage_url:
            return False
        
        # Skip if it's still a Product Hunt redirect URL (not resolved)
        if "producthunt.com/r/" in product.homepage_url:
            return False
        
        target = Target.from_producthunt(product)
        return self.targets.add_target(target)
    
    async def extract_targets(
        self,
        max_targets: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Phase 2: Extract structured data from pending targets.
        
        Args:
            max_targets: Maximum number of targets to process (None for all)
            
        Returns:
            Extraction statistics
        """
        if not self.openai_api_key:
            raise ValueError("OpenAI API key required. Set OPENAI_API_KEY env var.")
        
        pending_targets = list(self.targets.get_pending())
        if max_targets:
            pending_targets = pending_targets[:max_targets]
        
        if not pending_targets:
            self._log("No pending targets to extract")
            return {"extracted": 0, "failed": 0, "pending": 0}
        
        stats = {
            "extracted": 0,
            "failed": 0,
            "total": len(pending_targets),
        }
        
        self._log(f"=== Extracting Data from {len(pending_targets)} Targets ===")
        
        # Configure LLM extraction strategy
        llm_config = LLMConfig(
            provider=self.llm_provider,
            api_token=self.openai_api_key,
        )
        
        extraction_strategy = LLMExtractionStrategy(
            llm_config=llm_config,
            schema=get_extraction_schema(),
            instruction=EXTRACTION_INSTRUCTION,
            verbose=self.verbose,
        )
        
        async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
            for i, target in enumerate(pending_targets):
                self._log(f"[{i+1}/{len(pending_targets)}] Extracting: {target.name} ({target.homepage_url})")
                
                # Wait for rate limiter
                await self.rate_limiter.wait(RateLimitSource.OPENAI)
                
                try:
                    result = await crawler.arun(
                        url=target.homepage_url,
                        config=CrawlerRunConfig(
                            extraction_strategy=extraction_strategy,
                            cache_mode=CacheMode.BYPASS,
                            wait_until="domcontentloaded",
                        )
                    )
                    
                    if not result.success:
                        error_msg = result.error_message or "Crawl failed"
                        self._log(f"  Crawl failed: {error_msg}")
                        self.targets.mark_failed(target.id, error_msg)
                        stats["failed"] += 1
                        continue
                    
                    if result.extracted_content:
                        try:
                            content = json.loads(result.extracted_content)
                            
                            # Check for error response
                            if isinstance(content, list) and content:
                                first = content[0]
                                if isinstance(first, dict) and first.get("error"):
                                    error_msg = first.get("content", "Unknown error")
                                    is_rate_limit = "rate limit" in str(error_msg).lower()
                                    
                                    self._log(f"  Extraction error: {error_msg}")
                                    
                                    if is_rate_limit:
                                        await self.rate_limiter.handle_rate_limit(RateLimitSource.OPENAI)
                                    
                                    self.targets.mark_failed(target.id, error_msg)
                                    stats["failed"] += 1
                                    continue
                            
                            # Parse as SaaSProductInfo
                            if isinstance(content, list) and content:
                                product_info = SaaSProductInfo.model_validate(content[0])
                            elif isinstance(content, dict):
                                product_info = SaaSProductInfo.model_validate(content)
                            else:
                                raise ValueError(f"Unexpected content format: {type(content)}")
                            
                            # Create collected product (include PH metadata)
                            collected = CollectedProduct(
                                source="producthunt",
                                seed_query=target.topics[0] if target.topics else "discovery",
                                discovered_at=target.discovered_at,
                                homepage_url=target.homepage_url,
                                saashub_url=target.producthunt_url,  # Using this field for PH URL
                                product_info=product_info,
                                extraction_success=True,
                                extracted_at=datetime.utcnow().isoformat() + "Z",
                            )
                            
                            # Write to output
                            self._write_product(collected)
                            self.targets.mark_completed(target.id)
                            stats["extracted"] += 1
                            
                            self._log(f"  Success: {product_info.name}")
                            
                        except json.JSONDecodeError as e:
                            self._log(f"  JSON parse error: {e}")
                            self.targets.mark_failed(target.id, f"JSON parse error: {e}")
                            stats["failed"] += 1
                            
                        except Exception as e:
                            error_str = str(e)
                            is_rate_limit = "rate limit" in error_str.lower() or "429" in error_str
                            
                            self._log(f"  Parse error: {e}")
                            
                            if is_rate_limit:
                                await self.rate_limiter.handle_rate_limit(RateLimitSource.OPENAI)
                            
                            self.targets.mark_failed(target.id, error_str)
                            stats["failed"] += 1
                    else:
                        self._log(f"  No content extracted")
                        self.targets.mark_failed(target.id, "No content extracted")
                        stats["failed"] += 1
                        
                except Exception as e:
                    error_str = str(e)
                    is_rate_limit = "rate limit" in error_str.lower() or "429" in error_str
                    
                    self._log(f"  Exception: {e}")
                    
                    if is_rate_limit:
                        await self.rate_limiter.handle_rate_limit(RateLimitSource.OPENAI)
                    
                    self.targets.mark_failed(target.id, error_str)
                    stats["failed"] += 1
        
        # Final stats
        list_stats = self.targets.get_stats()
        stats["pending"] = list_stats["pending"]
        
        self._log(f"=== Extraction Complete ===")
        self._log(f"Extracted: {stats['extracted']}, Failed: {stats['failed']}, Remaining: {stats['pending']}")
        
        return stats
    
    def _write_product(self, product: CollectedProduct):
        """Append a product to the JSONL output file."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.output_path, "a") as f:
            f.write(product.model_dump_json() + "\n")


def main_legacy():
    """Legacy CLI entry point (SaaSHub-based)."""
    parser = argparse.ArgumentParser(
        description="Market Intelligence Collector (Legacy - SaaSHub)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument("--seeds", nargs="+", help="Seed queries")
    parser.add_argument("--seeds-file", type=Path, default=DEFAULT_SEEDS_PATH)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--skip-homepages", action="store_true")
    parser.add_argument("--skip-extraction", action="store_true")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--max-per-seed", type=int, default=50)
    parser.add_argument("--saashub-delay", type=float, default=12.0)
    parser.add_argument("--llm-provider", default="openai/gpt-4o")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--reset-state", action="store_true")
    
    args = parser.parse_args()
    
    if args.reset_state and args.state_file.exists():
        args.state_file.unlink()
        print(f"Reset state: {args.state_file}")
    
    collector = MarketIntelCollector(
        state_path=args.state_file,
        output_path=args.output,
        seeds_path=args.seeds_file,
        saashub_delay=args.saashub_delay,
        llm_provider=args.llm_provider,
        batch_size=args.batch_size,
        max_products_per_seed=args.max_per_seed,
        verbose=args.verbose,
    )
    
    try:
        stats = asyncio.run(collector.run(
            seeds=args.seeds,
            skip_discovery=args.skip_discovery,
            skip_homepages=args.skip_homepages,
            skip_extraction=args.skip_extraction,
        ))
        
        print("\n=== Collection Complete ===")
        print(json.dumps(stats, indent=2))
        
        if stats.get("halted"):
            print(f"\n⚠️  Collection halted: {stats.get('halt_reason')}")
            sys.exit(1)
        
        sys.exit(0)
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        collector.save_state()
        sys.exit(130)
        
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_target_build(args):
    """Handle target-build subcommand."""
    collector = ProductHuntCollector(
        targets_path=args.targets_file,
        output_path=args.output,
        min_votes=args.min_votes,
        verbose=args.verbose,
    )
    
    topics = args.topics if args.topics else None
    
    try:
        stats = asyncio.run(collector.build_targets(
            topics=topics,
            max_per_source=args.max_per_source,
            include_trending=not args.no_trending,
            include_popular=not args.no_popular,
            resolve_urls=not args.no_resolve,
        ))
        
        print("\n=== Target Building Complete ===")
        print(json.dumps(stats, indent=2))
        sys.exit(0)
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
        
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_extract(args):
    """Handle extract subcommand."""
    collector = ProductHuntCollector(
        targets_path=args.targets_file,
        output_path=args.output,
        llm_provider=args.llm_provider,
        verbose=args.verbose,
    )
    
    max_targets = args.max_targets if args.max_targets > 0 else None
    
    try:
        stats = asyncio.run(collector.extract_targets(
            max_targets=max_targets,
        ))
        
        print("\n=== Extraction Complete ===")
        print(json.dumps(stats, indent=2))
        sys.exit(0)
        
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
        
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def cmd_status(args):
    """Handle status subcommand."""
    targets = TargetManager(args.targets_file)
    stats = targets.get_stats()
    
    total = stats['total']
    completed = stats['completed']
    progress = (completed / total * 100) if total > 0 else 0.0
    
    print("\n=== Target List Status ===")
    print(f"Total targets:  {total}")
    print(f"Pending:        {stats['pending']}")
    print(f"Completed:      {completed}")
    print(f"Failed:         {stats['failed']}")
    print(f"No homepage:    {stats['no_homepage']}")
    print(f"Progress:       {progress:.1f}%")
    
    if args.show_pending:
        pending = list(targets.get_pending())[:args.limit]
        if pending:
            print(f"\n--- Pending Targets (showing {len(pending)}) ---")
            for t in pending:
                print(f"  • {t.name}: {t.homepage_url}")
    
    if args.show_failed:
        failed = list(targets.get_failed())[:args.limit]
        if failed:
            print(f"\n--- Failed Targets (showing {len(failed)}) ---")
            for t in failed:
                reason = t.failure_reason or "Unknown"
                error = reason[:50] + "..." if len(reason) > 50 else reason
                print(f"  ✗ {t.name}: {error}")


def cmd_reset(args):
    """Handle reset subcommand."""
    targets_file = Path(args.targets_file)
    
    if args.failed_only:
        targets = TargetManager(targets_file)
        count = targets.reset_all_failed()
        print(f"Reset {count} failed targets to pending status")
    else:
        if targets_file.exists():
            targets_file.unlink()
            print(f"Removed: {targets_file}")
        else:
            print(f"No targets file found: {targets_file}")


def main():
    """Main CLI entry point with subcommands."""
    parser = argparse.ArgumentParser(
        description="Market Intelligence Collector - Gather structured data on SaaS products",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  target-build   Build target list from Product Hunt API
  extract        Extract structured data from pending targets
  status         Show target list status
  reset          Reset targets (all or failed only)
  legacy         Run legacy SaaSHub-based collection

Examples:
  # Build target list from Product Hunt
  python -m crawl4ai.market_intel.collect target-build
  
  # Build with specific topics
  python -m crawl4ai.market_intel.collect target-build --topics "Developer Tools" "Productivity"
  
  # Extract data from pending targets
  python -m crawl4ai.market_intel.collect extract
  
  # Extract limited batch
  python -m crawl4ai.market_intel.collect extract --max-targets 10
  
  # Check status
  python -m crawl4ai.market_intel.collect status --show-pending
  
  # Reset failed targets
  python -m crawl4ai.market_intel.collect reset --failed-only

Environment Variables:
  PRODUCTHUNT_ACCESS_TOKEN   Product Hunt API token (required for target-build)
  OPENAI_API_KEY             OpenAI API key (required for extract)
        """
    )
    
    # Global arguments
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # target-build command
    build_parser = subparsers.add_parser(
        "target-build",
        help="Build target list from Product Hunt",
        description="Query Product Hunt API for trending/popular SaaS products and add to targets list."
    )
    build_parser.add_argument("--topics", nargs="+", help="Topics to discover from")
    build_parser.add_argument("--max-per-source", type=int, default=100, help="Max products per source")
    build_parser.add_argument("--min-votes", type=int, default=20, help="Minimum votes threshold")
    build_parser.add_argument("--no-trending", action="store_true", help="Skip trending products")
    build_parser.add_argument("--no-popular", action="store_true", help="Skip popular products")
    build_parser.add_argument("--no-resolve", action="store_true", help="Skip URL resolution (use raw API URLs)")
    build_parser.add_argument("--targets-file", type=Path, default=DEFAULT_TARGETS_PATH)
    build_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    build_parser.set_defaults(func=cmd_target_build)
    
    # extract command
    extract_parser = subparsers.add_parser(
        "extract",
        help="Extract structured data from pending targets",
        description="Scrape product homepages and extract structured data using LLM."
    )
    extract_parser.add_argument("--max-targets", type=int, default=0, help="Max targets to process (0 for all)")
    extract_parser.add_argument("--llm-provider", default="openai/gpt-4o", help="LLM provider string")
    extract_parser.add_argument("--targets-file", type=Path, default=DEFAULT_TARGETS_PATH)
    extract_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    extract_parser.set_defaults(func=cmd_extract)
    
    # status command
    status_parser = subparsers.add_parser(
        "status",
        help="Show target list status",
        description="Display statistics about the target list."
    )
    status_parser.add_argument("--show-pending", action="store_true", help="List pending targets")
    status_parser.add_argument("--show-failed", action="store_true", help="List failed targets")
    status_parser.add_argument("--limit", type=int, default=20, help="Max items to show per list")
    status_parser.add_argument("--targets-file", type=Path, default=DEFAULT_TARGETS_PATH)
    status_parser.set_defaults(func=cmd_status)
    
    # reset command
    reset_parser = subparsers.add_parser(
        "reset",
        help="Reset targets",
        description="Reset target list (all or failed only)."
    )
    reset_parser.add_argument("--failed-only", action="store_true", help="Only reset failed targets")
    reset_parser.add_argument("--targets-file", type=Path, default=DEFAULT_TARGETS_PATH)
    reset_parser.set_defaults(func=cmd_reset)
    
    # legacy command
    legacy_parser = subparsers.add_parser(
        "legacy",
        help="Run legacy SaaSHub-based collection",
        description="Original SaaSHub-based collection pipeline (deprecated)."
    )
    legacy_parser.add_argument("--seeds", nargs="+", help="Seed queries")
    legacy_parser.add_argument("--seeds-file", type=Path, default=DEFAULT_SEEDS_PATH)
    legacy_parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH)
    legacy_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    legacy_parser.add_argument("--skip-discovery", action="store_true")
    legacy_parser.add_argument("--skip-homepages", action="store_true")
    legacy_parser.add_argument("--skip-extraction", action="store_true")
    legacy_parser.add_argument("--batch-size", type=int, default=5)
    legacy_parser.add_argument("--max-per-seed", type=int, default=50)
    legacy_parser.add_argument("--saashub-delay", type=float, default=12.0)
    legacy_parser.add_argument("--llm-provider", default="openai/gpt-4o")
    legacy_parser.add_argument("--reset-state", action="store_true")
    
    args = parser.parse_args()
    
    # Handle no command
    if not args.command:
        parser.print_help()
        sys.exit(0)
    
    # Handle legacy command separately (different arg structure)
    if args.command == "legacy":
        if args.reset_state and args.state_file.exists():
            args.state_file.unlink()
            print(f"Reset state: {args.state_file}")
        
        collector = MarketIntelCollector(
            state_path=args.state_file,
            output_path=args.output,
            seeds_path=args.seeds_file,
            saashub_delay=args.saashub_delay,
            llm_provider=args.llm_provider,
            batch_size=args.batch_size,
            max_products_per_seed=args.max_per_seed,
            verbose=args.verbose,
        )
        
        try:
            stats = asyncio.run(collector.run(
                seeds=args.seeds,
                skip_discovery=args.skip_discovery,
                skip_homepages=args.skip_homepages,
                skip_extraction=args.skip_extraction,
            ))
            
            print("\n=== Collection Complete ===")
            print(json.dumps(stats, indent=2))
            
            if stats.get("halted"):
                print(f"\n⚠️  Collection halted: {stats.get('halt_reason')}")
                sys.exit(1)
            
            sys.exit(0)
            
        except KeyboardInterrupt:
            print("\n\nInterrupted by user")
            collector.save_state()
            sys.exit(130)
            
        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    # Run the appropriate command handler
    args.func(args)


if __name__ == "__main__":
    main()
