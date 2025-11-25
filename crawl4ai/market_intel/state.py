"""
State management for resumable market intelligence collection runs.

Tracks:
- Which seed queries have been processed
- Which products have been discovered
- Which products have been extracted
- Errors and failures for retry logic
"""

import os
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class ProductState:
    """State of a single product in the collection pipeline."""
    name: str
    homepage_url: Optional[str] = None
    saashub_url: Optional[str] = None
    saashub_id: Optional[str] = None
    seed_query: str = ""
    
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
    """
    
    # Run metadata
    run_id: str = ""
    started_at: str = ""
    updated_at: str = ""
    
    # Progress tracking
    processed_seeds: List[str] = field(default_factory=list)
    products: Dict[str, ProductState] = field(default_factory=dict)
    
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
        """Load state from a JSON file."""
        if not path.exists():
            return cls.new()
        
        with open(path, "r") as f:
            data = json.load(f)
        
        # Reconstruct ProductState objects
        products = {}
        for key, prod_data in data.get("products", {}).items():
            products[key] = ProductState(**prod_data)
        
        state = cls(
            run_id=data.get("run_id", ""),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
            processed_seeds=data.get("processed_seeds", []),
            products=products,
            consecutive_llm_failures=data.get("consecutive_llm_failures", 0),
            total_llm_failures=data.get("total_llm_failures", 0),
            last_llm_error=data.get("last_llm_error"),
            halted=data.get("halted", False),
            halt_reason=data.get("halt_reason"),
            total_discovered=data.get("total_discovered", 0),
            total_extracted=data.get("total_extracted", 0),
            total_failed=data.get("total_failed", 0),
        )
        return state
    
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
            "run_id": self.run_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "processed_seeds": self.processed_seeds,
            "products": {k: asdict(v) for k, v in self.products.items()},
            "consecutive_llm_failures": self.consecutive_llm_failures,
            "total_llm_failures": self.total_llm_failures,
            "last_llm_error": self.last_llm_error,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "total_discovered": self.total_discovered,
            "total_extracted": self.total_extracted,
            "total_failed": self.total_failed,
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
    ) -> ProductState:
        """Add a newly discovered product."""
        # Use saashub_id or name as key for deduplication
        key = saashub_id or name.lower().replace(" ", "-")
        
        if key in self.products:
            # Update existing product with new info
            prod = self.products[key]
            if homepage_url and not prod.homepage_url:
                prod.homepage_url = homepage_url
                prod.homepage_discovered = True
            return prod
        
        now = datetime.utcnow().isoformat() + "Z"
        prod = ProductState(
            name=name,
            homepage_url=homepage_url,
            saashub_url=saashub_url,
            saashub_id=saashub_id,
            seed_query=seed_query,
            discovered_at=now,
            homepage_discovered=homepage_url is not None,
        )
        self.products[key] = prod
        self.total_discovered += 1
        return prod
    
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
            "seeds_processed": len(self.processed_seeds),
            "total_discovered": self.total_discovered,
            "total_extracted": self.total_extracted,
            "total_failed": self.total_failed,
            "pending_homepage": len(self.get_products_needing_homepage()),
            "pending_extraction": len(self.get_products_needing_extraction()),
            "consecutive_llm_failures": self.consecutive_llm_failures,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }
