"""
Product Hunt API client for discovering SaaS products.

Uses the Product Hunt GraphQL API to fetch:
- Trending/popular products
- Products by topic/category
- Product details including homepage URL

Rate limit: 500 requests per 15 minutes

Note: The Product Hunt API returns tracking/redirect URLs for the `website` field.
To get actual homepage URLs, we scrape the Product Hunt launch page and extract
the real website from the page HTML.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncGenerator
from urllib.parse import urlparse

import httpx

from .rate_limiter import RateLimiter, RateLimitSource

logger = logging.getLogger(__name__)


# GraphQL queries
POSTS_QUERY = """
query GetPosts($first: Int!, $after: String, $order: PostsOrder!, $topic: String, $postedAfter: DateTime) {
    posts(first: $first, after: $after, order: $order, topic: $topic, postedAfter: $postedAfter) {
        pageInfo {
            hasNextPage
            endCursor
        }
        edges {
            node {
                id
                name
                tagline
                description
                website
                url
                votesCount
                reviewsCount
                reviewsRating
                createdAt
                featuredAt
                slug
                topics {
                    edges {
                        node {
                            name
                            slug
                        }
                    }
                }
                makers {
                    id
                    name
                    username
                    headline
                }
                thumbnail {
                    url
                }
            }
        }
    }
}
"""

TOPICS_QUERY = """
query GetTopics($first: Int!, $after: String) {
    topics(first: $first, after: $after) {
        pageInfo {
            hasNextPage
            endCursor
        }
        edges {
            node {
                id
                name
                slug
                description
                postsCount
            }
        }
    }
}
"""


@dataclass
class ProductHuntProduct:
    """A product discovered from Product Hunt."""
    id: str
    name: str
    tagline: Optional[str] = None
    description: Optional[str] = None
    homepage_url: Optional[str] = None
    producthunt_url: Optional[str] = None
    votes_count: int = 0
    reviews_count: int = 0
    reviews_rating: Optional[float] = None
    created_at: Optional[str] = None
    featured_at: Optional[str] = None
    slug: Optional[str] = None
    topics: List[str] = field(default_factory=list)
    makers: List[Dict[str, str]] = field(default_factory=list)
    thumbnail_url: Optional[str] = None
    
    @classmethod
    def from_graphql(cls, node: Dict[str, Any]) -> "ProductHuntProduct":
        """Create a ProductHuntProduct from a GraphQL response node."""
        topics = []
        if node.get("topics", {}).get("edges"):
            topics = [
                edge["node"]["name"]
                for edge in node["topics"]["edges"]
            ]
        
        makers = []
        if node.get("makers"):
            makers = [
                {
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "username": m.get("username"),
                    "headline": m.get("headline"),
                }
                for m in node["makers"]
            ]
        
        return cls(
            id=node["id"],
            name=node["name"],
            tagline=node.get("tagline"),
            description=node.get("description"),
            homepage_url=node.get("website"),
            producthunt_url=node.get("url"),
            votes_count=node.get("votesCount", 0),
            reviews_count=node.get("reviewsCount", 0),
            reviews_rating=node.get("reviewsRating"),
            created_at=node.get("createdAt"),
            featured_at=node.get("featuredAt"),
            slug=node.get("slug"),
            topics=topics,
            makers=makers,
            thumbnail_url=node.get("thumbnail", {}).get("url") if node.get("thumbnail") else None,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to a dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "tagline": self.tagline,
            "description": self.description,
            "homepage_url": self.homepage_url,
            "producthunt_url": self.producthunt_url,
            "votes_count": self.votes_count,
            "reviews_count": self.reviews_count,
            "reviews_rating": self.reviews_rating,
            "created_at": self.created_at,
            "featured_at": self.featured_at,
            "slug": self.slug,
            "topics": self.topics,
            "makers": self.makers,
            "thumbnail_url": self.thumbnail_url,
        }


@dataclass
class ProductHuntTopic:
    """A topic/category from Product Hunt."""
    id: str
    name: str
    slug: str
    description: Optional[str] = None
    posts_count: int = 0
    
    @classmethod
    def from_graphql(cls, node: Dict[str, Any]) -> "ProductHuntTopic":
        """Create a ProductHuntTopic from a GraphQL response node."""
        return cls(
            id=node["id"],
            name=node["name"],
            slug=node["slug"],
            description=node.get("description"),
            posts_count=node.get("postsCount", 0),
        )


class ProductHuntAPIError(Exception):
    """Error from the Product Hunt API."""
    pass


class ProductHuntClient:
    """
    Client for the Product Hunt GraphQL API.
    
    Usage:
        client = ProductHuntClient()
        
        # Get popular products
        async for product in client.get_popular_products(min_votes=20):
            print(product.name, product.homepage_url)
        
        # Get products by topic
        async for product in client.get_products_by_topic("saas", min_votes=20):
            print(product.name)
    """
    
    GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"
    
    def __init__(
        self,
        access_token: Optional[str] = None,
        rate_limiter: Optional[RateLimiter] = None,
        min_votes: int = 20,
    ):
        """
        Initialize the Product Hunt client.
        
        Args:
            access_token: Product Hunt API access token (or PRODUCTHUNT_ACCESS_TOKEN env var)
            rate_limiter: Optional shared rate limiter instance
            min_votes: Minimum votes required to include a product (default: 20)
        """
        self.access_token = access_token or os.getenv("PRODUCTHUNT_ACCESS_TOKEN")
        
        if not self.access_token:
            raise ValueError(
                "Product Hunt access token required. "
                "Set PRODUCTHUNT_ACCESS_TOKEN env var or pass access_token parameter. "
                "Get token at: https://www.producthunt.com/v2/oauth/applications"
            )
        
        self.rate_limiter = rate_limiter or RateLimiter()
        self.min_votes = min_votes
        self._client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self) -> "ProductHuntClient":
        """Async context manager entry."""
        self._client = httpx.AsyncClient(timeout=30.0)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def _graphql_query(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a GraphQL query with rate limiting and retry.
        
        Args:
            query: The GraphQL query string
            variables: Optional query variables
            
        Returns:
            The response data
            
        Raises:
            ProductHuntAPIError: If the API returns an error
        """
        if not self._client:
            raise RuntimeError("Client not initialized. Use async context manager.")
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        async def make_request():
            response = await self._client.post(
                self.GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers=headers,
            )
            
            if response.status_code == 429:
                # Rate limited
                retry_after = response.headers.get("retry-after")
                raise ProductHuntAPIError(
                    f"Rate limited (429). Retry after: {retry_after}s"
                )
            
            response.raise_for_status()
            result = response.json()
            
            if "errors" in result:
                error_messages = [e.get("message", str(e)) for e in result["errors"]]
                raise ProductHuntAPIError(f"GraphQL errors: {error_messages}")
            
            return result.get("data", {})
        
        # Execute with rate limiting and retry
        return await self.rate_limiter.execute_with_retry(
            RateLimitSource.PRODUCT_HUNT,
            make_request,
        )
    
    async def get_popular_products(
        self,
        limit: int = 100,
        min_votes: Optional[int] = None,
        posted_after: Optional[str] = None,
    ) -> AsyncGenerator[ProductHuntProduct, None]:
        """
        Get popular products ordered by votes.
        
        Args:
            limit: Maximum number of products to fetch
            min_votes: Minimum votes required (defaults to instance min_votes)
            posted_after: Only products posted after this ISO date
            
        Yields:
            ProductHuntProduct objects
        """
        min_votes = min_votes if min_votes is not None else self.min_votes
        cursor = None
        fetched = 0
        
        while fetched < limit:
            # Fetch a page
            page_size = min(50, limit - fetched)  # API max is 50 per page
            
            variables = {
                "first": page_size,
                "after": cursor,
                "order": "VOTES",
            }
            
            if posted_after:
                variables["postedAfter"] = posted_after
            
            data = await self._graphql_query(POSTS_QUERY, variables)
            posts = data.get("posts", {})
            edges = posts.get("edges", [])
            page_info = posts.get("pageInfo", {})
            
            if not edges:
                break
            
            for edge in edges:
                product = ProductHuntProduct.from_graphql(edge["node"])
                
                # Filter by min votes
                if product.votes_count >= min_votes:
                    yield product
                    fetched += 1
                    
                    if fetched >= limit:
                        break
            
            # Check for more pages
            if not page_info.get("hasNextPage"):
                break
            
            cursor = page_info.get("endCursor")
    
    async def get_trending_products(
        self,
        limit: int = 100,
        min_votes: Optional[int] = None,
    ) -> AsyncGenerator[ProductHuntProduct, None]:
        """
        Get trending products (recently featured, ordered by votes).
        
        This gets products featured in the last 7 days, ordered by votes.
        
        Args:
            limit: Maximum number of products to fetch
            min_votes: Minimum votes required (defaults to instance min_votes)
            
        Yields:
            ProductHuntProduct objects
        """
        min_votes = min_votes if min_votes is not None else self.min_votes
        cursor = None
        fetched = 0
        
        while fetched < limit:
            page_size = min(50, limit - fetched)
            
            variables = {
                "first": page_size,
                "after": cursor,
                "order": "RANKING",  # Trending/featured order
            }
            
            data = await self._graphql_query(POSTS_QUERY, variables)
            posts = data.get("posts", {})
            edges = posts.get("edges", [])
            page_info = posts.get("pageInfo", {})
            
            if not edges:
                break
            
            for edge in edges:
                product = ProductHuntProduct.from_graphql(edge["node"])
                
                if product.votes_count >= min_votes:
                    yield product
                    fetched += 1
                    
                    if fetched >= limit:
                        break
            
            if not page_info.get("hasNextPage"):
                break
            
            cursor = page_info.get("endCursor")
    
    async def get_products_by_topic(
        self,
        topic_slug: str,
        limit: int = 50,
        min_votes: Optional[int] = None,
    ) -> AsyncGenerator[ProductHuntProduct, None]:
        """
        Get products in a specific topic/category.
        
        Args:
            topic_slug: The topic slug (e.g., "saas", "developer-tools")
            limit: Maximum number of products to fetch
            min_votes: Minimum votes required (defaults to instance min_votes)
            
        Yields:
            ProductHuntProduct objects
        """
        min_votes = min_votes if min_votes is not None else self.min_votes
        cursor = None
        fetched = 0
        
        while fetched < limit:
            page_size = min(50, limit - fetched)
            
            variables = {
                "first": page_size,
                "after": cursor,
                "order": "VOTES",
                "topic": topic_slug,
            }
            
            data = await self._graphql_query(POSTS_QUERY, variables)
            posts = data.get("posts", {})
            edges = posts.get("edges", [])
            page_info = posts.get("pageInfo", {})
            
            if not edges:
                break
            
            for edge in edges:
                product = ProductHuntProduct.from_graphql(edge["node"])
                
                if product.votes_count >= min_votes:
                    yield product
                    fetched += 1
                    
                    if fetched >= limit:
                        break
            
            if not page_info.get("hasNextPage"):
                break
            
            cursor = page_info.get("endCursor")
    
    async def get_topics(
        self,
        limit: int = 100,
        min_posts: int = 10,
    ) -> AsyncGenerator[ProductHuntTopic, None]:
        """
        Get available topics/categories.
        
        Args:
            limit: Maximum number of topics to fetch
            min_posts: Minimum posts count required
            
        Yields:
            ProductHuntTopic objects
        """
        cursor = None
        fetched = 0
        
        while fetched < limit:
            page_size = min(50, limit - fetched)
            
            variables = {
                "first": page_size,
                "after": cursor,
            }
            
            data = await self._graphql_query(TOPICS_QUERY, variables)
            topics = data.get("topics", {})
            edges = topics.get("edges", [])
            page_info = topics.get("pageInfo", {})
            
            if not edges:
                break
            
            for edge in edges:
                topic = ProductHuntTopic.from_graphql(edge["node"])
                
                if topic.posts_count >= min_posts:
                    yield topic
                    fetched += 1
                    
                    if fetched >= limit:
                        break
            
            if not page_info.get("hasNextPage"):
                break
            
            cursor = page_info.get("endCursor")

    async def resolve_homepage_url(
        self,
        product: ProductHuntProduct,
    ) -> Optional[str]:
        """
        Resolve the actual homepage URL for a product.
        
        The Product Hunt API returns tracking/redirect URLs (e.g., /r/ABC123) 
        instead of actual website URLs. This method scrapes the Product Hunt 
        launch page to extract the real homepage URL.
        
        Args:
            product: The ProductHuntProduct to resolve
            
        Returns:
            The resolved homepage URL, or None if not found
        """
        if not product.producthunt_url:
            return None
        
        # Strip tracking params from PH URL
        ph_url = product.producthunt_url.split("?")[0]
        
        try:
            # Use crawl4ai to fetch the page (handles JS rendering)
            from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
            
            config = CrawlerRunConfig(
                wait_until="domcontentloaded",
                page_timeout=30000,
                delay_before_return_html=1.0,
            )
            
            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await crawler.arun(url=ph_url, config=config)
                
                if not result.html:
                    logger.warning(f"No HTML returned for {ph_url}")
                    return None
                
                # Extract external URLs from the HTML
                # Look for https:// URLs that are NOT producthunt.com
                external_urls = re.findall(
                    r'https?://(?!(?:www\.)?producthunt\.com)[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-z]{2,}(?:/[^\s"\'<>]*)?',
                    result.html
                )
                
                if not external_urls:
                    logger.warning(f"No external URLs found on {ph_url}")
                    return None
                
                # Filter out common non-product URLs
                skip_domains = {
                    "schema.org", "w3.org", "google.com", "googletagmanager.com",
                    "facebook.com", "twitter.com", "x.com", "linkedin.com",
                    "youtube.com", "youtu.be", "instagram.com", "github.com", "cloudflare.com",
                    "cloudflareinsights.com", "segment.com", "imgix.net", "lu.ma",
                    "fonts.googleapis.com", "fonts.gstatic.com", "segment-cdn.producthunt.com",
                    # Mobile app stores and deep links
                    "producthunt.app.link", "apps.apple.com", "play.google.com",
                    "itunes.apple.com", "appstore.com", "onelink.me", "branch.io",
                    "adjust.com", "app.link", "appsto.re",
                    # Other PH internal URLs
                    "help.producthunt.com", "ph-static.imgix.net", "ph-files.imgix.net",
                    "ph-avatars.imgix.net",
                }
                
                # Find the best candidate URL
                # Priority: exact domain match with product slug/name
                product_name_lower = product.name.lower().replace(" ", "").replace("-", "")
                slug_lower = (product.slug or "").lower().replace("-", "")
                
                # Also create variants without numbers (e.g., "cursor2.0" -> "cursor")
                name_no_numbers = ''.join(c for c in product_name_lower if not c.isdigit())
                slug_no_numbers = ''.join(c for c in slug_lower if not c.isdigit())
                
                candidates = []
                for url in external_urls:
                    try:
                        parsed = urlparse(url)
                        domain = parsed.netloc.lower()
                        
                        # Skip known non-product domains
                        if any(skip in domain for skip in skip_domains):
                            continue
                        
                        # Skip if path looks like an asset
                        path = parsed.path.lower()
                        if any(ext in path for ext in [".png", ".jpg", ".svg", ".gif", ".css", ".js"]):
                            continue
                        
                        # Normalize domain (remove www.)
                        clean_domain = domain.replace("www.", "")
                        domain_base = clean_domain.split(".")[0]
                        
                        # Score based on name match (higher = better)
                        score = 0
                        
                        # Exact match with slug or name (highest priority)
                        if domain_base == slug_lower or domain_base == product_name_lower:
                            score = 1000  # Exact match
                        elif domain_base == slug_no_numbers or domain_base == name_no_numbers:
                            score = 900  # Match without version numbers
                        # Partial match - name/slug contained in domain
                        elif len(slug_lower) > 3 and slug_lower in domain_base:
                            score = 500
                        elif len(name_no_numbers) > 3 and name_no_numbers in domain_base:
                            score = 400
                        # Domain contained in name/slug (less reliable)
                        elif len(domain_base) > 3 and domain_base in slug_lower:
                            score = 200
                        elif len(domain_base) > 3 and domain_base in name_no_numbers:
                            score = 150
                        else:
                            # No match - low priority but still a candidate
                            score = 1
                        
                        candidates.append((score, f"https://{clean_domain}"))
                    except Exception:
                        continue
                
                if not candidates:
                    return None
                
                # Return the highest-scoring unique URL
                # Only return URLs with a decent score (at least partial match)
                candidates.sort(reverse=True)
                seen = set()
                for score, url in candidates:
                    if url not in seen:
                        # Only return if we have at least a partial match (score >= 100)
                        # Otherwise, it's too risky
                        if score >= 100:
                            logger.debug(f"Resolved homepage for {product.name}: {url} (score={score})")
                            return url
                        else:
                            # Log that we're skipping a low-confidence match
                            logger.debug(f"Skipping low-confidence URL for {product.name}: {url} (score={score})")
                    seen.add(url)
                
                return None
                
        except Exception as e:
            logger.warning(f"Failed to resolve homepage for {product.name}: {e}")
            return None
    
    async def resolve_homepage_urls_batch(
        self,
        products: List[ProductHuntProduct],
        concurrency: int = 3,
    ) -> Dict[str, str]:
        """
        Resolve homepage URLs for multiple products in parallel.
        
        Args:
            products: List of ProductHuntProduct objects
            concurrency: Max concurrent resolutions (be gentle with PH servers)
            
        Returns:
            Dict mapping product ID to resolved homepage URL
        """
        semaphore = asyncio.Semaphore(concurrency)
        results = {}
        
        async def resolve_one(product: ProductHuntProduct):
            async with semaphore:
                url = await self.resolve_homepage_url(product)
                if url:
                    results[product.id] = url
                # Small delay between requests
                await asyncio.sleep(0.5)
        
        await asyncio.gather(*[resolve_one(p) for p in products])
        return results


# Priority topics for SaaS/B2B product discovery
PRIORITY_TOPICS = [
    "saas",
    "developer-tools",
    "productivity",
    "marketing",
    "analytics",
    "artificial-intelligence",
    "design-tools",
    "no-code",
    "automation",
    "api",
    "sales",
    "project-management",
    "customer-support",
    "collaboration",
    "finance",
    "email",
    "crm",
    "data-visualization",
    "security",
    "infrastructure",
]
