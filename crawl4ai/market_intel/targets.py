"""
Target list manager for market intelligence collection.

Manages a single JSONL file (targets/targets.jsonl) that tracks:
- Products discovered from Product Hunt
- Status of each target (pending, completed, failed)
- URL-based deduplication

The target list enables:
1. Decoupled discovery and extraction phases
2. Resumable collection (skip already-completed targets)
3. Progress tracking
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator
from urllib.parse import urlparse
import fcntl
import tempfile
import shutil

logger = logging.getLogger(__name__)


class TargetStatus(str, Enum):
    """Status of a target in the collection pipeline."""
    PENDING = "pending"      # Discovered, not yet extracted
    COMPLETED = "completed"  # Successfully extracted
    FAILED = "failed"        # Extraction failed


@dataclass
class Target:
    """
    A target product for market intelligence extraction.
    
    Contains Product Hunt metadata plus status tracking.
    """
    # Identity
    id: str  # Product Hunt ID (e.g., "ph_12345")
    name: str
    
    # URLs
    homepage_url: Optional[str] = None
    producthunt_url: Optional[str] = None
    
    # Status tracking
    status: TargetStatus = TargetStatus.PENDING
    discovered_at: Optional[str] = None
    completed_at: Optional[str] = None
    failed_at: Optional[str] = None
    failure_reason: Optional[str] = None
    extraction_attempts: int = 0
    
    # Product Hunt metadata
    source: str = "producthunt"
    tagline: Optional[str] = None
    description: Optional[str] = None
    votes_count: int = 0
    reviews_count: int = 0
    reviews_rating: Optional[float] = None
    topics: List[str] = field(default_factory=list)
    makers: List[Dict[str, str]] = field(default_factory=list)
    slug: Optional[str] = None
    created_at: Optional[str] = None  # Product Hunt created at
    featured_at: Optional[str] = None
    thumbnail_url: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        data["status"] = self.status.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Target":
        """Create a Target from a dictionary."""
        data = data.copy()
        if "status" in data:
            data["status"] = TargetStatus(data["status"])
        return cls(**data)
    
    @classmethod
    def from_producthunt(cls, product: Any) -> "Target":
        """Create a Target from a ProductHuntProduct."""
        now = datetime.utcnow().isoformat() + "Z"
        
        return cls(
            id=f"ph_{product.id}",
            name=product.name,
            homepage_url=product.homepage_url,
            producthunt_url=product.producthunt_url,
            status=TargetStatus.PENDING,
            discovered_at=now,
            source="producthunt",
            tagline=product.tagline,
            description=product.description,
            votes_count=product.votes_count,
            reviews_count=product.reviews_count,
            reviews_rating=product.reviews_rating,
            topics=product.topics,
            makers=product.makers,
            slug=product.slug,
            created_at=product.created_at,
            featured_at=product.featured_at,
            thumbnail_url=product.thumbnail_url,
        )


class TargetManager:
    """
    Manages the target list for market intelligence collection.
    
    Features:
    - Single JSONL file storage
    - URL-based deduplication
    - Atomic status updates
    - Thread-safe file operations
    
    Usage:
        manager = TargetManager("targets/targets.jsonl")
        
        # Add new targets
        for product in products:
            manager.add_target(Target.from_producthunt(product))
        
        # Iterate pending targets
        for target in manager.get_pending():
            # Process target
            manager.mark_completed(target.id)
    """
    
    def __init__(self, path: Path | str = "targets/targets.jsonl"):
        """
        Initialize the target manager.
        
        Args:
            path: Path to the JSONL file
        """
        self.path = Path(path)
        self._url_index: Dict[str, str] = {}  # normalized_url -> target_id
        self._targets: Dict[str, Target] = {}  # target_id -> Target
        self._load()
    
    @staticmethod
    def normalize_url(url: str) -> str:
        """
        Normalize a URL for deduplication.
        
        Removes www., trailing slashes, and common variations.
        """
        if not url:
            return ""
        
        url = url.lower().strip()
        parsed = urlparse(url)
        
        # Normalize domain
        domain = parsed.netloc.replace("www.", "")
        
        # Normalize path
        path = parsed.path.rstrip("/")
        
        # Remove common suffixes
        for suffix in ["/en", "/en-us", "/en-gb", "/home", "/index"]:
            if path.endswith(suffix):
                path = path[:-len(suffix)]
        
        return f"{domain}{path}"
    
    def _load(self) -> None:
        """Load targets from the JSONL file."""
        self._targets = {}
        self._url_index = {}
        
        if not self.path.exists():
            return
        
        with open(self.path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                try:
                    data = json.loads(line)
                    target = Target.from_dict(data)
                    self._targets[target.id] = target
                    
                    if target.homepage_url:
                        normalized = self.normalize_url(target.homepage_url)
                        if normalized:
                            self._url_index[normalized] = target.id
                            
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"Failed to parse target line: {e}")
    
    def _save(self) -> None:
        """Save all targets to the JSONL file atomically."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write to temp file first
        temp_path = self.path.with_suffix(".jsonl.tmp")
        
        with open(temp_path, "w") as f:
            for target in self._targets.values():
                f.write(json.dumps(target.to_dict()) + "\n")
        
        # Atomic rename
        shutil.move(str(temp_path), str(self.path))
    
    def _save_single(self, target: Target) -> None:
        """Append a single target to the file (for new targets only)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(self.path, "a") as f:
            f.write(json.dumps(target.to_dict()) + "\n")
    
    def is_url_known(self, url: str) -> bool:
        """Check if a homepage URL has already been added."""
        if not url:
            return False
        normalized = self.normalize_url(url)
        return normalized in self._url_index
    
    def is_id_known(self, target_id: str) -> bool:
        """Check if a target ID has already been added."""
        return target_id in self._targets
    
    def add_target(self, target: Target) -> bool:
        """
        Add a new target to the list.
        
        Returns:
            True if added, False if duplicate (by ID or URL)
        """
        # Check for ID duplicate
        if target.id in self._targets:
            logger.debug(f"Target already exists by ID: {target.id}")
            return False
        
        # Check for URL duplicate
        if target.homepage_url:
            normalized = self.normalize_url(target.homepage_url)
            if normalized and normalized in self._url_index:
                existing_id = self._url_index[normalized]
                logger.debug(
                    f"Target already exists by URL: {target.homepage_url} "
                    f"(existing: {existing_id})"
                )
                return False
        
        # Add to indexes
        self._targets[target.id] = target
        
        if target.homepage_url:
            normalized = self.normalize_url(target.homepage_url)
            if normalized:
                self._url_index[normalized] = target.id
        
        # Append to file
        self._save_single(target)
        
        return True
    
    def get_target(self, target_id: str) -> Optional[Target]:
        """Get a target by ID."""
        return self._targets.get(target_id)
    
    def get_pending(self) -> Iterator[Target]:
        """Iterate over pending targets."""
        for target in self._targets.values():
            if target.status == TargetStatus.PENDING:
                yield target
    
    def get_completed(self) -> Iterator[Target]:
        """Iterate over completed targets."""
        for target in self._targets.values():
            if target.status == TargetStatus.COMPLETED:
                yield target
    
    def get_failed(self) -> Iterator[Target]:
        """Iterate over failed targets."""
        for target in self._targets.values():
            if target.status == TargetStatus.FAILED:
                yield target
    
    def mark_completed(self, target_id: str) -> bool:
        """
        Mark a target as completed.
        
        Returns:
            True if updated, False if target not found
        """
        target = self._targets.get(target_id)
        if not target:
            return False
        
        target.status = TargetStatus.COMPLETED
        target.completed_at = datetime.utcnow().isoformat() + "Z"
        target.extraction_attempts += 1
        
        # Full save (rewrite file)
        self._save()
        
        return True
    
    def mark_failed(
        self,
        target_id: str,
        reason: Optional[str] = None,
    ) -> bool:
        """
        Mark a target as failed.
        
        Returns:
            True if updated, False if target not found
        """
        target = self._targets.get(target_id)
        if not target:
            return False
        
        target.status = TargetStatus.FAILED
        target.failed_at = datetime.utcnow().isoformat() + "Z"
        target.failure_reason = reason
        target.extraction_attempts += 1
        
        # Full save
        self._save()
        
        return True
    
    def reset_failed(self, target_id: str) -> bool:
        """
        Reset a failed target to pending for retry.
        
        Returns:
            True if updated, False if target not found
        """
        target = self._targets.get(target_id)
        if not target:
            return False
        
        target.status = TargetStatus.PENDING
        target.failure_reason = None
        
        # Full save
        self._save()
        
        return True
    
    def reset_all_failed(self) -> int:
        """
        Reset all failed targets to pending.
        
        Returns:
            Number of targets reset
        """
        count = 0
        for target in self._targets.values():
            if target.status == TargetStatus.FAILED:
                target.status = TargetStatus.PENDING
                target.failure_reason = None
                count += 1
        
        if count > 0:
            self._save()
        
        return count
    
    def get_stats(self) -> Dict[str, int]:
        """Get statistics about the target list."""
        pending = 0
        completed = 0
        failed = 0
        no_homepage = 0
        
        for target in self._targets.values():
            if target.status == TargetStatus.PENDING:
                pending += 1
            elif target.status == TargetStatus.COMPLETED:
                completed += 1
            elif target.status == TargetStatus.FAILED:
                failed += 1
            
            if not target.homepage_url:
                no_homepage += 1
        
        return {
            "total": len(self._targets),
            "pending": pending,
            "completed": completed,
            "failed": failed,
            "no_homepage": no_homepage,
        }
    
    def __len__(self) -> int:
        """Return the total number of targets."""
        return len(self._targets)
    
    def __iter__(self) -> Iterator[Target]:
        """Iterate over all targets."""
        return iter(self._targets.values())
