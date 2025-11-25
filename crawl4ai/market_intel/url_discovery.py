"""
URL Discovery module for extracting homepage URLs from SaaSHub pages.

SaaSHub API only returns minimal data (name, tagline, saashubUrl).
To get actual homepage URLs, we need to crawl the SaaSHub product pages
and extract the homepage link.
"""

import asyncio
import re
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode


# CSS selector for the homepage link on SaaSHub product pages
# The homepage link is typically in the product header/info section
HOMEPAGE_SELECTORS = [
    'a[href*="?ref=saashub"][rel="nofollow"]',  # Referral links to homepage
    'a.product-website-link',
    'a[data-action="visit-website"]',
    '.product-links a[rel="nofollow"]',
]


async def extract_homepage_from_saashub(
    saashub_url: str,
    crawler: Optional[AsyncWebCrawler] = None,
) -> Optional[str]:
    """
    Extract the product homepage URL from a SaaSHub product page.
    
    Args:
        saashub_url: SaaSHub product page URL (e.g., "https://www.saashub.com/notion")
        crawler: Optional existing crawler instance to reuse
        
    Returns:
        Homepage URL if found, None otherwise
    """
    should_close = crawler is None
    
    if crawler is None:
        crawler = AsyncWebCrawler(config=BrowserConfig(headless=True))
        await crawler.__aenter__()
    
    try:
        result = await crawler.arun(
            url=saashub_url,
            config=CrawlerRunConfig(
                cache_mode=CacheMode.ENABLED,
                wait_until="domcontentloaded",
            )
        )
        
        if not result.success:
            return None
        
        # Parse the HTML to find homepage link
        # SaaSHub typically has the homepage as an external link with ref parameter
        html = result.html or ""
        
        # Look for patterns like href="https://example.com?ref=saashub"
        # or the "Visit Website" button
        patterns = [
            r'href="(https?://[^"]+)\?ref=saashub"',
            r'href="(https?://[^"]+)"[^>]*>Visit Website',
            r'href="(https?://[^"]+)"[^>]*>Official Website',
            r'class="website-link"[^>]*href="(https?://[^"]+)"',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                url = match.group(1)
                # Clean up the URL (remove tracking params)
                url = url.split("?ref=")[0].split("?utm_")[0]
                return url
        
        # Fallback: look in extracted links
        if result.links and "external" in result.links:
            for link in result.links["external"]:
                href = link.get("href", "")
                # Skip SaaSHub internal links
                if "saashub.com" in href:
                    continue
                # Skip common non-homepage links
                if any(x in href for x in ["twitter.com", "linkedin.com", "facebook.com", "github.com"]):
                    continue
                # This is likely the homepage
                return href
        
        return None
        
    finally:
        if should_close:
            await crawler.__aexit__(None, None, None)


async def discover_homepage_urls(
    products: List[Dict[str, Any]],
    batch_size: int = 5,
    delay_between_batches: float = 2.0,
    on_progress: Optional[callable] = None,
) -> Dict[str, Optional[str]]:
    """
    Discover homepage URLs for multiple products from their SaaSHub pages.
    
    Args:
        products: List of product dicts with 'saashub_url' and 'name' keys
        batch_size: Number of concurrent requests per batch
        delay_between_batches: Seconds to wait between batches
        on_progress: Optional callback(name, index, total, homepage_url) for progress
        
    Returns:
        Dict mapping product name -> homepage URL (or None if not found)
    """
    results = {}
    total = len(products)
    
    async with AsyncWebCrawler(config=BrowserConfig(headless=True)) as crawler:
        for i in range(0, total, batch_size):
            batch = products[i:i + batch_size]
            
            # Process batch concurrently
            tasks = []
            for prod in batch:
                saashub_url = prod.get("saashub_url") or prod.get("attributes", {}).get("saashubUrl")
                if saashub_url:
                    if not saashub_url.startswith("http"):
                        saashub_url = f"https://www.saashub.com{saashub_url}"
                    tasks.append(extract_homepage_from_saashub(saashub_url, crawler))
                else:
                    tasks.append(asyncio.coroutine(lambda: None)())
            
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for j, (prod, homepage) in enumerate(zip(batch, batch_results)):
                name = prod.get("name") or prod.get("attributes", {}).get("name", "Unknown")
                
                if isinstance(homepage, Exception):
                    homepage = None
                
                results[name] = homepage
                
                if on_progress:
                    on_progress(name, i + j, total, homepage)
            
            # Delay between batches to be respectful
            if i + batch_size < total:
                await asyncio.sleep(delay_between_batches)
    
    return results


async def discover_homepage_single(
    name: str,
    saashub_url: str,
    crawler: Optional[AsyncWebCrawler] = None,
) -> Optional[str]:
    """
    Convenience function to discover homepage for a single product.
    
    Args:
        name: Product name (for logging)
        saashub_url: SaaSHub product page URL
        crawler: Optional existing crawler instance
        
    Returns:
        Homepage URL if found, None otherwise
    """
    if not saashub_url:
        return None
    
    if not saashub_url.startswith("http"):
        saashub_url = f"https://www.saashub.com{saashub_url}"
    
    return await extract_homepage_from_saashub(saashub_url, crawler)
