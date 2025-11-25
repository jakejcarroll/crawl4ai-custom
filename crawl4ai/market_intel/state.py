"""
State management for resumable market intelligence collection runs.

Tracks:
- Which seed queries have been processed
- Which products have been discovered
- Which products have been extracted
- Errors and failures for retry logic
- URL-based deduplication across sources
- Phase-level checkpointing for resume
"""

import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime
from urllib.parse import urlparse


# Current state version for migration
STATE_VERSION = 2


@dataclass
class ProductState:
    """State of a single product in the collection pipeline."""
    name: str
    homepage_url: Optional[str] = None
    saashub_url: Optional[str] = None
    saashub_id: Optional[str] = None
    seed_query: str = ""
    
    # Multi-source support
    source: str = "saashub"  # "saashub", "producthunt", or "merged"
    source_url: Optional[str] = None  # URL where we found this product
    producthunt_id: Optional[str] = None
    producthunt_url: Optional[str] = None
    votes_count: Optional[int] = None  # From Product Hunt
    
    # Extra metadata from sources
    tagline: Optional[str] = None
    topics: List[str] = field(default_factory=list)
    
    # Discovery state
    discovered_at: Optional[str] = None
    homepage_discovered: bool = False
    
    # Extraction state
    extracted: bool = False
    extracted_at: Optional[str] = None
    extraction_error: Optional[str] = None
    
    # Rate limit tracking
    extraction_attempts: int = 0
    last_attempt_at: Optional[str] = None


@dataclass 
class CollectionState:
    """
    Persistent state for a market intelligence collection run.
    
    Enables resumable runs by tracking:
    - Processed seed queries
    - Discovered products
    - Extraction status
    - Error history
    - URL-based deduplication
    - Phase checkpoints
    """
    
    # State version for migration
    version: int = STATE_VERSION
    
    # Run metadata
    run_id: str = ""
    started_at: str = ""
    updated_at: str = ""
    
    # Progress tracking
    processed_seeds: List[str] = field(default_factory=list)
    products: Dict[str, ProductState] = field(default_factory=dict)
    
    # URL-based deduplication index
    # Maps normalized URL -> product key for fast lookup
    homepage_url_index: Dict[str, str] = field(default_factory=dict)
    
    # Phase checkpointing
    current_phase: str = "discovery"  # discovery, homepage, extraction
    phase_progress: Dict[str, Any] = field(default_factory=dict)
    
    # Error tracking for halt logic
    consecutive_llm_failures: int = 0
    total_llm_failures: int = 0
    last_llm_error: Optional[str] = None
    halted: bool = False
    halt_reason: Optional[str] = None
    
    # Statistics
    total_discovered: int = 0
    total_extracted: int = 0
    total_failed: int = 0
    total_merged: int = 0  # Products found in multiple sources
    
    # Per-source statistics
    source_stats: Dict[str, Dict[str, int]] = field(default_factory=dict)
    
    @staticmethod
    def normalize_url(url: str) -> str:
        """
        Normalize a URL for deduplication.
        
        Removes www., trailing slashes, and common tracking params.
        """
        if not url:
            return ""
        
        url = url.lower().strip()
        
        # Parse URL
        parsed = urlparse(url)
        
        # Normalize domain
        domain = parsed.netloc.replace("www.", "")
        
        # Normalize path
        path = parsed.path.rstrip("/")
        
        # Remove common tracking/locale suffixes
        for suffix in ["/en", "/en-us", "/en-gb", "/home", "/index"]:
            if path.endswith(suffix):
                path = path[:-len(suffix)]
        
        return f"{domain}{path}"
    
    def is_url_known(self, homepage_url: str) -> bool:
        """Check if a homepage URL has already been discovered."""
        if not homepage_url:
            return False
        normalized = self.normalize_url(homepage_url)
        return normalized in self.homepage_url_index
    
    def get_product_by_url(self, homepage_url: str) -> Optional[ProductState]:
        """Get existing product by homepage URL."""
        if not homepage_url:
            return None
        normalized = self.normalize_url(homepage_url)
        product_key = self.homepage_url_index.get(normalized)
        if product_key:
            return self.products.get(product_key)
        return None
    
    def _rebuild_url_index(self) -> None:
        """Rebuild the URL index from products dict."""
        self.homepage_url_index = {}
        for key, prod in self.products.items():
            if prod.homepage_url:
                normalized = self.normalize_url(prod.homepage_url)
                if normalized:
                    self.homepage_url_index[normalized] = key
    
    # Error tracking for halt logic
    consecutive_llm_failures: int = 0
    total_llm_failures: int = 0
    last_llm_error: Optional[str] = None
    halted: bool = False
    halt_reason: Optional[str] = None
    
    # Statistics
    total_discovered: int = 0
    total_extracted: int = 0
    total_failed: int = 0
    
    @classmethod
    def load(cls, path: Path) -> "CollectionState":
        """Load state from a JSON file, with automatic migration."""
        if not path.exists():
            return cls.new()
        
        with open(path, "r") as f:
            data = json.load(f)
        
        # Check version and migrate if needed
        version = data.get("version", 1)
        if version < STATE_VERSION:
            data = cls._migrate(data, version)
        
        # Reconstruct ProductState objects
        products = {}
        for key, prod_data in data.get("products", {}).items():
            # Handle missing fields from old versions
            prod_data.setdefault("source", "saashub")
            prod_data.setdefault("source_url", prod_data.get("saashub_url"))
            prod_data.setdefault("producthunt_id", None)
            prod_data.setdefault("producthunt_url", None)
            prod_data.setdefault("votes_count", None)
            prod_data.setdefault("tagline", None)
            prod_data.setdefault("topics", [])
            products[key] = ProductState(**prod_data)
        
        state = cls(
            version=STATE_VERSION,
            run_id=data.get("run_id", ""),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
            processed_seeds=data.get("processed_seeds", []),
            products=products,
            homepage_url_index=data.get("homepage_url_index", {}),
            current_phase=data.get("current_phase", "discovery"),
            phase_progress=data.get("phase_progress", {}),
            consecutive_llm_failures=data.get("consecutive_llm_failures", 0),
            total_llm_failures=data.get("total_llm_failures", 0),
            last_llm_error=data.get("last_llm_error"),
            halted=data.get("halted", False),
            halt_reason=data.get("halt_reason"),
            total_discovered=data.get("total_discovered", 0),
            total_extracted=data.get("total_extracted", 0),
            total_failed=data.get("total_failed", 0),
            total_merged=data.get("total_merged", 0),
            source_stats=data.get("source_stats", {}),
        )
        
        # Rebuild URL index if missing (from v1 migration)
        if not state.homepage_url_index:
            state._rebuild_url_index()
        
        return state
    
    @classmethod
    def _migrate(cls, data: Dict[str, Any], from_version: int) -> Dict[str, Any]:
        """Migrate state data from older versions."""
        if from_version < 2:
            # v1 -> v2: Add multi-source fields
            data["version"] = 2
            data["homepage_url_index"] = {}
            data["current_phase"] = "discovery"
            data["phase_progress"] = {}
            data["total_merged"] = 0
            data["source_stats"] = {}
            
            # Migrate products
            for key, prod in data.get("products", {}).items():
                prod["source"] = "saashub"
                prod["source_url"] = prod.get("saashub_url")
                prod["producthunt_id"] = None
                prod["producthunt_url"] = None
                prod["votes_count"] = None
                prod["tagline"] = None
                prod["topics"] = []
        
        return data
    
    @classmethod
    def new(cls, run_id: Optional[str] = None) -> "CollectionState":
        """Create a new state instance."""
        now = datetime.utcnow().isoformat() + "Z"
        return cls(
            run_id=run_id or datetime.utcnow().strftime("%Y%m%d_%H%M%S"),
            started_at=now,
            updated_at=now,
        )
    
    def save(self, path: Path) -> None:
        """Save state to a JSON file."""
        self.updated_at = datetime.utcnow().isoformat() + "Z"
        
        # Convert to dict, handling ProductState objects
        data = {
            "version": self.version,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "processed_seeds": self.processed_seeds,
            "products": {k: asdict(v) for k, v in self.products.items()},
            "homepage_url_index": self.homepage_url_index,
            "current_phase": self.current_phase,
            "phase_progress": self.phase_progress,
            "consecutive_llm_failures": self.consecutive_llm_failures,
            "total_llm_failures": self.total_llm_failures,
            "last_llm_error": self.last_llm_error,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "total_discovered": self.total_discovered,
            "total_extracted": self.total_extracted,
            "total_failed": self.total_failed,
            "total_merged": self.total_merged,
            "source_stats": self.source_stats,
        }
        
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    
    def is_seed_processed(self, seed: str) -> bool:
        """Check if a seed query has been processed."""
        return seed in self.processed_seeds
    
    def mark_seed_processed(self, seed: str) -> None:
        """Mark a seed query as processed."""
        if seed not in self.processed_seeds:
            self.processed_seeds.append(seed)
    
    def add_product(
        self,
        name: str,
        seed_query: str,
        saashub_url: Optional[str] = None,
        saashub_id: Optional[str] = None,
        homepage_url: Optional[str] = None,
        source: str = "saashub",
        source_url: Optional[str] = None,
        producthunt_id: Optional[str] = None,
        producthunt_url: Optional[str] = None,
        votes_count: Optional[int] = None,
        tagline: Optional[str] = None,
        topics: Optional[List[str]] = None,
    ) -> ProductState:
        """
        Add a newly discovered product, with deduplication by URL.
        
        If a product with the same homepage URL exists, merges the data.
        """
        # Generate key based on source
        if source == "producthunt" and producthunt_id:
            key = f"ph_{producthunt_id}"
        elif saashub_id:
            key = saashub_id
        else:
            key = name.lower().replace(" ", "-")
        
        # Check for URL-based duplicate
        if homepage_url:
            existing = self.get_product_by_url(homepage_url)
            if existing:
                # Merge data from new source
                return self.merge_product(existing, ProductState(
                    name=name,
                    homepage_url=homepage_url,
                    saashub_url=saashub_url,
                    saashub_id=saashub_id,
                    seed_query=seed_query,
                    source=source,
                    source_url=source_url or saashub_url or producthunt_url,
                    producthunt_id=producthunt_id,
                    producthunt_url=producthunt_url,
                    votes_count=votes_count,
                    tagline=tagline,
                    topics=topics or [],
                ))
        
        # Check for key-based duplicate
        if key in self.products:
            prod = self.products[key]
            # Update existing product with new info
            if homepage_url and not prod.homepage_url:
                prod.homepage_url = homepage_url
                prod.homepage_discovered = True
                # Add to URL index
                normalized = self.normalize_url(homepage_url)
                if normalized:
                    self.homepage_url_index[normalized] = key
            if tagline and not prod.tagline:
                prod.tagline = tagline
            if votes_count and not prod.votes_count:
                prod.votes_count = votes_count
            if topics:
                existing_topics = set(prod.topics)
                prod.topics = list(existing_topics | set(topics))
            return prod
        
        # Create new product
        now = datetime.utcnow().isoformat() + "Z"
        prod = ProductState(
            name=name,
            homepage_url=homepage_url,
            saashub_url=saashub_url,
            saashub_id=saashub_id,
            seed_query=seed_query,
            source=source,
            source_url=source_url or saashub_url or producthunt_url,
            producthunt_id=producthunt_id,
            producthunt_url=producthunt_url,
            votes_count=votes_count,
            tagline=tagline,
            topics=topics or [],
            discovered_at=now,
            homepage_discovered=homepage_url is not None,
        )
        self.products[key] = prod
        self.total_discovered += 1
        
        # Update URL index
        if homepage_url:
            normalized = self.normalize_url(homepage_url)
            if normalized:
                self.homepage_url_index[normalized] = key
        
        # Update source stats
        if source not in self.source_stats:
            self.source_stats[source] = {"discovered": 0, "extracted": 0}
        self.source_stats[source]["discovered"] += 1
        
        return prod
    
    def merge_product(
        self, 
        existing: ProductState, 
        new_data: ProductState
    ) -> ProductState:
        """
        Merge data from a new source into an existing product.
        
        Strategy:
        - Keep homepage URL from Product Hunt (direct from API) if available
        - Combine taglines (prefer PH if both exist)
        - Keep votes from PH
        - Merge topics
        - Mark as "merged" source
        """
        # Get the key for existing product
        existing_key = None
        for key, prod in self.products.items():
            if prod is existing:
                existing_key = key
                break
        
        # Merge fields
        if new_data.homepage_url and not existing.homepage_url:
            existing.homepage_url = new_data.homepage_url
            existing.homepage_discovered = True
        
        # Prefer PH homepage URL (direct from API)
        if new_data.source == "producthunt" and new_data.homepage_url:
            old_normalized = self.normalize_url(existing.homepage_url or "")
            new_normalized = self.normalize_url(new_data.homepage_url)
            if old_normalized != new_normalized:
                # Update URL index
                if old_normalized and old_normalized in self.homepage_url_index:
                    del self.homepage_url_index[old_normalized]
                existing.homepage_url = new_data.homepage_url
                if new_normalized and existing_key:
                    self.homepage_url_index[new_normalized] = existing_key
        
        # Keep Product Hunt specific data
        if new_data.producthunt_id:
            existing.producthunt_id = new_data.producthunt_id
        if new_data.producthunt_url:
            existing.producthunt_url = new_data.producthunt_url
        if new_data.votes_count:
            existing.votes_count = new_data.votes_count
        
        # Keep SaaSHub specific data
        if new_data.saashub_id and not existing.saashub_id:
            existing.saashub_id = new_data.saashub_id
        if new_data.saashub_url and not existing.saashub_url:
            existing.saashub_url = new_data.saashub_url
        
        # Merge tagline (prefer PH)
        if new_data.tagline:
            if new_data.source == "producthunt" or not existing.tagline:
                existing.tagline = new_data.tagline
        
        # Merge topics
        if new_data.topics:
            existing_topics = set(existing.topics)
            existing.topics = list(existing_topics | set(new_data.topics))
        
        # Mark as merged
        if existing.source != new_data.source:
            existing.source = "merged"
            self.total_merged += 1
        
        return existing
    
    def checkpoint(self, phase: str, progress: Any) -> None:
        """Save a checkpoint for the current phase."""
        self.current_phase = phase
        self.phase_progress[phase] = progress
    
    def get_products_needing_homepage(self) -> List[ProductState]:
        """Get products that need homepage URL discovery."""
        return [p for p in self.products.values() if not p.homepage_discovered]
    
    def get_products_needing_extraction(self) -> List[ProductState]:
        """Get products that have homepage URLs but haven't been extracted."""
        return [
            p for p in self.products.values()
            if p.homepage_discovered and p.homepage_url and not p.extracted
        ]
    
    def mark_extraction_success(self, product_key: str) -> None:
        """Mark a product as successfully extracted."""
        if product_key in self.products:
            prod = self.products[product_key]
            prod.extracted = True
            prod.extracted_at = datetime.utcnow().isoformat() + "Z"
            prod.extraction_attempts += 1
            self.total_extracted += 1
            # Reset consecutive failures on success
            self.consecutive_llm_failures = 0
    
    def mark_extraction_failure(
        self,
        product_key: str,
        error: str,
        is_rate_limit: bool = False,
    ) -> bool:
        """
        Mark an extraction failure.
        
        Args:
            product_key: Key of the product that failed
            error: Error message
            is_rate_limit: Whether this was a rate limit error
            
        Returns:
            True if collection should halt, False otherwise
        """
        now = datetime.utcnow().isoformat() + "Z"
        
        if product_key in self.products:
            prod = self.products[product_key]
            prod.extraction_error = error
            prod.extraction_attempts += 1
            prod.last_attempt_at = now
        
        self.total_failed += 1
        self.total_llm_failures += 1
        self.last_llm_error = error
        
        if is_rate_limit:
            self.consecutive_llm_failures += 1
            # Halt after 3 consecutive rate limit failures
            if self.consecutive_llm_failures >= 3:
                self.halted = True
                self.halt_reason = f"Rate limit: {self.consecutive_llm_failures} consecutive failures. Last error: {error}"
                return True
        
        return False
    
    def should_halt(self) -> bool:
        """Check if collection should halt."""
        return self.halted
    
    def get_stats(self) -> Dict[str, Any]:
        """Get collection statistics."""
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "current_phase": self.current_phase,
            "seeds_processed": len(self.processed_seeds),
            "total_discovered": self.total_discovered,
            "total_extracted": self.total_extracted,
            "total_failed": self.total_failed,
            "total_merged": self.total_merged,
            "pending_homepage": len(self.get_products_needing_homepage()),
            "pending_extraction": len(self.get_products_needing_extraction()),
            "consecutive_llm_failures": self.consecutive_llm_failures,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "source_stats": self.source_stats,
        }
