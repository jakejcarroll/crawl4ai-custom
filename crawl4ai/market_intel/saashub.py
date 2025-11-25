#!/usr/bin/env python3
"""
SaaSHub API Client

A minimal, well-documented client for the SaaSHub public API.
Provides functions to query product alternatives and product details.

API Documentation: https://www.saashub.com/site/api
"""

import os
import time
import json
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

import httpx
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# API Configuration
BASE_URL = "https://www.saashub.com/api/"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds


class SaaSHubAPIError(Exception):
    """Base exception for SaaSHub API errors."""
    pass


class RateLimitError(SaaSHubAPIError):
    """Raised when rate limit is exceeded and all retries are exhausted."""
    
    def __init__(self, message: str, retry_after: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after


class SaaSHubClient:
    """
    Client for interacting with the SaaSHub public API.
    
    The SaaSHub API follows the jsonapi.org specification and provides
    two main endpoints:
    - /api/alternatives/{query} - Get alternatives for a product
    - /api/product/{query} - Get product details
    
    Attributes:
        api_key (str): SaaSHub API key from environment variable SAASHUB_API_KEY
        base_url (str): Base URL for the API
        timeout (float): Request timeout in seconds
        request_delay (float): Delay between requests to respect rate limits
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        request_delay: float = 12.0,  # ~5 requests per minute by default
    ):
        """
        Initialize the SaaSHub API client.
        
        Args:
            api_key: SaaSHub API key. If None, reads from SAASHUB_API_KEY env var.
            base_url: Base URL for the API (default: https://www.saashub.com/api/)
            timeout: Request timeout in seconds (default: 30.0)
            request_delay: Minimum delay between requests in seconds (default: 12.0)
            
        Raises:
            ValueError: If no API key is provided or found in environment
        """
        self.api_key = api_key or os.getenv("SAASHUB_API_KEY")
        if not self.api_key:
            raise ValueError(
                "SaaSHub API key is required. Set SAASHUB_API_KEY environment variable "
                "or pass api_key parameter. Get your key at: https://www.saashub.com/profile/api_key"
            )
        
        self.base_url = base_url
        self.timeout = timeout
        self.request_delay = request_delay
        self._client = httpx.Client(timeout=timeout)
        self._last_request_time: Optional[float] = None
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - cleanup resources."""
        self.close()
    
    def close(self):
        """Close the HTTP client and cleanup resources."""
        self._client.close()
    
    def _wait_for_rate_limit(self):
        """Wait if necessary to respect rate limits."""
        if self._last_request_time is not None:
            elapsed = time.time() - self._last_request_time
            if elapsed < self.request_delay:
                time.sleep(self.request_delay - elapsed)
    
    def _make_request(
        self,
        endpoint: str,
        params: Optional[dict] = None
    ) -> dict:
        """
        Make a request to the SaaSHub API with retry logic.
        
        Args:
            endpoint: API endpoint path (e.g., "alternatives/notion")
            params: Additional query parameters (api_key is added automatically)
            
        Returns:
            Parsed JSON response as dict
            
        Raises:
            SaaSHubAPIError: If the request fails after retries
            RateLimitError: If rate limit is exceeded after all retries
        """
        url = urljoin(self.base_url, endpoint)
        
        # Add API key to query parameters
        request_params = params or {}
        request_params["api_key"] = self.api_key
        
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                # Respect rate limits
                self._wait_for_rate_limit()
                
                response = self._client.get(url, params=request_params)
                self._last_request_time = time.time()
                
                # Handle rate limiting (429) with exponential backoff
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", RETRY_DELAY * (2 ** attempt)))
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(retry_after)
                        continue
                    raise RateLimitError(
                        f"Rate limit exceeded after {MAX_RETRIES} attempts.",
                        retry_after=retry_after
                    )
                
                # Raise for other HTTP errors
                response.raise_for_status()
                
                # Parse and return JSON
                return response.json()
                
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code in (400, 401, 403, 404):
                    # Don't retry client errors
                    raise SaaSHubAPIError(
                        f"API request failed: {e.response.status_code} - {e.response.text}"
                    ) from e
                
                # Retry on server errors (5xx)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
                    continue
                    
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
                    continue
        
        # All retries exhausted
        raise SaaSHubAPIError(f"Request failed after {MAX_RETRIES} attempts: {last_error}") from last_error
    
    def get_alternatives(
        self,
        query: str,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get alternatives for a given product.
        
        Calls the /api/alternatives/{query} endpoint to retrieve a list of
        alternative products for the specified query.
        
        Args:
            query: Product name or identifier (e.g., "notion", "basecamp")
            limit: Maximum number of alternatives to return (None = all)
            
        Returns:
            List of product dictionaries. Each product has:
            - id: Product identifier
            - type: Resource type (typically "product")
            - attributes: Product details (name, saashubUrl, tagline, etc.)
            
        Raises:
            SaaSHubAPIError: If the API request fails
            
        Example:
            >>> client = SaaSHubClient()
            >>> alternatives = client.get_alternatives("notion", limit=5)
            >>> for alt in alternatives:
            ...     print(alt["attributes"]["name"])
        """
        endpoint = f"alternatives/{query}"
        
        try:
            response = self._make_request(endpoint)
            
            # Extract alternatives from JSON API response
            data = response.get("data", {})
            alternatives = data.get("alternatives", [])
            
            # Apply limit if specified
            if limit is not None and limit > 0:
                alternatives = alternatives[:limit]
            
            return alternatives
            
        except KeyError as e:
            raise SaaSHubAPIError(f"Unexpected API response structure: missing {e}") from e
    
    def get_product(self, query: str) -> Optional[Dict[str, Any]]:
        """
        Get product details for a given query.
        
        Calls the /api/product/{query} endpoint to retrieve details about
        a specific product.
        
        Args:
            query: Product name or identifier (e.g., "notion", "basecamp")
            
        Returns:
            Product dictionary with:
            - id: Product identifier
            - type: Resource type (typically "product")
            - attributes: Product details (name, saashubUrl, tagline, etc.)
            
            Returns None if the product is not found.
            
        Raises:
            SaaSHubAPIError: If the API request fails (except 404)
            
        Example:
            >>> client = SaaSHubClient()
            >>> product = client.get_product("notion")
            >>> if product:
            ...     print(product["attributes"]["name"])
            ...     print(product["attributes"]["tagline"])
        """
        endpoint = f"product/{query}"
        
        try:
            response = self._make_request(endpoint)
            
            # Extract product from JSON API response
            data = response.get("data", {})
            product = data.get("product")
            
            return product
            
        except SaaSHubAPIError as e:
            # Return None for 404 (not found)
            if "404" in str(e):
                return None
            raise
        except KeyError as e:
            raise SaaSHubAPIError(f"Unexpected API response structure: missing {e}") from e
    
    def get_alternatives_batch(
        self,
        queries: List[str],
        limit_per_query: Optional[int] = None,
        on_progress: Optional[callable] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get alternatives for multiple products with rate limiting.
        
        Args:
            queries: List of product names to query
            limit_per_query: Maximum alternatives per query
            on_progress: Optional callback(query, index, total) for progress updates
            
        Returns:
            Dict mapping query -> list of alternatives
        """
        results = {}
        total = len(queries)
        
        for idx, query in enumerate(queries):
            if on_progress:
                on_progress(query, idx, total)
            
            try:
                results[query] = self.get_alternatives(query, limit=limit_per_query)
            except SaaSHubAPIError as e:
                # Store error but continue with other queries
                results[query] = []
                print(f"Warning: Failed to get alternatives for '{query}': {e}")
        
        return results


# CLI Entry Point
def main():
    """
    Command-line interface for testing the SaaSHub API client.
    
    Usage:
        python -m crawl4ai.market_intel.saashub --alternatives <query> [--limit <n>]
        python -m crawl4ai.market_intel.saashub --product <query>
    
    Examples:
        python -m crawl4ai.market_intel.saashub --alternatives notion --limit 5
        python -m crawl4ai.market_intel.saashub --product basecamp
    """
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(
        description="SaaSHub API Client - Query product alternatives and details",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --alternatives notion --limit 5
  %(prog)s --product basecamp
  
Environment:
  SAASHUB_API_KEY    Your SaaSHub API key (required)
                     Get it at: https://www.saashub.com/profile/api_key
        """
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--alternatives",
        metavar="QUERY",
        help="Get alternatives for a product (e.g., 'notion')"
    )
    group.add_argument(
        "--product",
        metavar="QUERY",
        help="Get product details (e.g., 'basecamp')"
    )
    
    parser.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Limit number of alternatives returned (only with --alternatives)"
    )
    
    parser.add_argument(
        "--api-key",
        help="SaaSHub API key (overrides SAASHUB_API_KEY env var)"
    )
    
    args = parser.parse_args()
    
    try:
        # Initialize client
        with SaaSHubClient(api_key=args.api_key) as client:
            
            if args.alternatives:
                # Get alternatives
                print(f"Fetching alternatives for '{args.alternatives}'...")
                alternatives = client.get_alternatives(args.alternatives, limit=args.limit)
                
                if not alternatives:
                    print(f"No alternatives found for '{args.alternatives}'")
                    return 0
                
                print(f"\nFound {len(alternatives)} alternative(s):\n")
                print(json.dumps(alternatives, indent=2, ensure_ascii=False))
                
            elif args.product:
                # Get product details
                print(f"Fetching product details for '{args.product}'...")
                product = client.get_product(args.product)
                
                if not product:
                    print(f"Product '{args.product}' not found")
                    return 1
                
                print(f"\nProduct details:\n")
                print(json.dumps(product, indent=2, ensure_ascii=False))
        
        return 0
        
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        print("\nSet your API key:", file=sys.stderr)
        print("  export SAASHUB_API_KEY='your_key_here'", file=sys.stderr)
        print("  or use --api-key option", file=sys.stderr)
        return 1
        
    except SaaSHubAPIError as e:
        print(f"API Error: {e}", file=sys.stderr)
        return 1
        
    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        return 130
        
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
