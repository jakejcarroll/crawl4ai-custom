#!/usr/bin/env python3
"""
Market Intelligence Collector

Orchestrates the collection of structured market data on SaaS products:
1. Load seed queries from config
2. Query SaaSHub API for alternatives (with rate limiting)
3. Discover homepage URLs from SaaSHub pages
4. Extract structured data from homepages using LLM (GPT-4o)
5. Output JSONL dataset

Designed for resumable runs with state persistence and automatic
halt on rate limit errors.
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
from crawl4ai.types import LLMConfig

from .saashub import SaaSHubClient, SaaSHubAPIError, RateLimitError
from .schemas import (
    SaaSProductInfo,
    CollectedProduct,
    EXTRACTION_INSTRUCTION,
    get_extraction_schema,
)
from .state import CollectionState, ProductState
from .url_discovery import discover_homepage_single


# Default paths
DEFAULT_STATE_PATH = Path("data/market_intel_state.json")
DEFAULT_OUTPUT_PATH = Path("data/market_intel_products.jsonl")
DEFAULT_SEEDS_PATH = Path("configs/market_intel_seeds.yml")


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
                            cache_mode=CacheMode.ENABLED,
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


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Market Intelligence Collector - Gather structured data on SaaS products",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full run with all phases
  python -m crawl4ai.market_intel.collect
  
  # Only discover products (no homepage/extraction)
  python -m crawl4ai.market_intel.collect --skip-homepages --skip-extraction
  
  # Only extract (resume from existing state)
  python -m crawl4ai.market_intel.collect --skip-discovery --skip-homepages
  
  # Use specific seeds
  python -m crawl4ai.market_intel.collect --seeds notion slack figma
  
  # Custom batch size and output
  python -m crawl4ai.market_intel.collect --batch-size 10 --output data/products.jsonl

Environment Variables:
  SAASHUB_API_KEY   SaaSHub API key (required)
  OPENAI_API_KEY    OpenAI API key (required for extraction)
        """
    )
    
    parser.add_argument(
        "--seeds",
        nargs="+",
        help="Seed queries to use (overrides config file)"
    )
    parser.add_argument(
        "--seeds-file",
        type=Path,
        default=DEFAULT_SEEDS_PATH,
        help=f"Path to seeds YAML config (default: {DEFAULT_SEEDS_PATH})"
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"Path to state file (default: {DEFAULT_STATE_PATH})"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Path to JSONL output file (default: {DEFAULT_OUTPUT_PATH})"
    )
    parser.add_argument(
        "--skip-discovery",
        action="store_true",
        help="Skip SaaSHub API discovery phase"
    )
    parser.add_argument(
        "--skip-homepages",
        action="store_true",
        help="Skip homepage URL discovery phase"
    )
    parser.add_argument(
        "--skip-extraction",
        action="store_true",
        help="Skip LLM extraction phase"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Concurrent extractions per batch (default: 5)"
    )
    parser.add_argument(
        "--max-per-seed",
        type=int,
        default=50,
        help="Max products to fetch per seed query (default: 50)"
    )
    parser.add_argument(
        "--saashub-delay",
        type=float,
        default=12.0,
        help="Seconds between SaaSHub API requests (default: 12.0)"
    )
    parser.add_argument(
        "--llm-provider",
        default="openai/gpt-4o",
        help="LLM provider string (default: openai/gpt-4o)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset state and start fresh"
    )
    
    args = parser.parse_args()
    
    # Reset state if requested
    if args.reset_state and args.state_file.exists():
        args.state_file.unlink()
        print(f"Reset state: {args.state_file}")
    
    # Create collector
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
    
    # Run collection
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


if __name__ == "__main__":
    main()
