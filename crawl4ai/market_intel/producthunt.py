"""
Product Hunt API client for discovering SaaS products.

Uses the Product Hunt GraphQL API to fetch:
- Trending/popular products
- Products by topic/category
- Product details including homepage URL

Rate limit: 500 requests per 15 minutes
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, AsyncGenerator

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
