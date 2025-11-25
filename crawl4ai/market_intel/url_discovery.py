"""
URL Discovery module for extracting homepage URLs from SaaSHub pages.

SaaSHub API only returns minimal data (name, tagline, saashubUrl).
To get actual homepage URLs, we need to crawl the SaaSHub product pages
and extract the homepage link.
"""

import asyncio
import re
from typing import Optional, List, Dict, Any
from urllib.parse import urljoin, urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode


# Domains to skip when looking for homepage URLs (not product websites)
SKIP_DOMAINS = [
    # Social media
    "twitter.com", "x.com", "linkedin.com", "facebook.com", 
    "github.com", "youtube.com", "instagram.com", "tiktok.com",
    # App stores and extensions
    "chrome.google.com",  # Chrome Web Store
    "addons.mozilla.org",  # Firefox Add-ons
    "microsoftedge.microsoft.com",  # Edge Add-ons
    "apps.apple.com",  # Apple App Store
    "play.google.com",  # Google Play Store
    "itunes.apple.com",
    # Directories and tools
    "producthunt.com",
    "crunchbase.com",
    "alternativeto.net",
    "g2.com",
    "capterra.com",
    "trustpilot.com",
    "similarweb.com",
    "ahrefs.com",
    "moz.com",
    "reddit.com",
    "bunny.net",
    "spinthewheelofnames.com",
    # Generic/utility
    "javascript:",
    "mailto:",
]


def _extract_product_slug(saashub_url: str) -> str:
    """Extract the product slug from a SaaSHub URL."""
    # https://www.saashub.com/skype -> skype
    path = urlparse(saashub_url).path
    return path.strip("/").split("/")[0] if path else ""


def _is_likely_homepage(href: str, product_slug: str) -> bool:
    """
    Check if a URL is likely the product's homepage.
    
    Prioritizes URLs that contain the product name/slug in the domain.
    """
    if not href or not href.startswith("http"):
        return False
    
    # Skip known non-homepage domains
    if any(domain in href.lower() for domain in SKIP_DOMAINS):
        return False
    
    # Check if it looks like a homepage (not a deep link)
    parsed = urlparse(href)
    path = parsed.path.rstrip("/")
    
    # Prefer root pages or simple paths
    # e.g., https://example.com/ or https://example.com/product
    path_depth = len([p for p in path.split("/") if p])
    
    # If the product slug appears in the domain, it's very likely the homepage
    domain = parsed.netloc.lower()
    slug_lower = product_slug.lower().replace("-", "").replace("_", "")
    domain_clean = domain.replace("-", "").replace("_", "").replace(".", "")
    
    if slug_lower and slug_lower in domain_clean:
        return True
    
    # Otherwise, prefer root-level pages
    return path_depth <= 1


def _clean_url(url: str) -> str:
    """Clean up URL by removing tracking parameters."""
    if not url:
        return url
    # Remove common tracking params
    for param in ["?ref=", "?utm_", "&ref=", "&utm_"]:
        if param in url:
            url = url.split(param)[0]
    return url.rstrip("/")


async def extract_homepage_from_saashub(
    saashub_url: str,
    crawler: Optional[AsyncWebCrawler] = None,
    product_name: Optional[str] = None,
) -> Optional[str]:
    """
    Extract the product homepage URL from a SaaSHub product page.
    
    Args:
        saashub_url: SaaSHub product page URL (e.g., "https://www.saashub.com/notion")
        crawler: Optional existing crawler instance to reuse
        product_name: Optional product name to help identify the correct homepage
        
    Returns:
        Homepage URL if found, None otherwise
    """
    should_close = crawler is None
    product_slug = _extract_product_slug(saashub_url)
    
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
        
        html = result.html or ""
        
        # Strategy 1: Look for the Website button onclick handler
        # SaaSHub uses: onclick="window.open('https://.../')"
        onclick_pattern = r'onclick="window\.open\(\'(https?://[^\']+)\'\);"[^>]*>(?:Website|Official\s+Site)'
        onclick_match = re.search(onclick_pattern, html, re.IGNORECASE)
        if onclick_match:
            url = _clean_url(onclick_match.group(1))
            if url and not any(d in url.lower() for d in SKIP_DOMAINS):
                return url
        
        # Strategy 2: Look for the product's main website URL in the HTML
        # SaaSHub pages often have the URL embedded in JavaScript or data attributes
        # Pattern: Look for URLs containing the product slug in the domain
        if product_slug:
            # Look for clean URLs with the product domain (e.g., https://www.skype.com/)
            # Also match variations like "productapp.com" or "productio.com"
            product_domain_pattern = rf'https?://(?:www\.)?{re.escape(product_slug)}(?:app|io|hq)?\.(?:com|io|app|co|org|net)(?:/[a-z]{{2}})?/?'
            domain_matches = re.findall(product_domain_pattern, html, re.IGNORECASE)
            if domain_matches:
                # Return the shortest/cleanest match (likely the homepage)
                cleaned = [_clean_url(m.rstrip("');\"")) for m in domain_matches]
                cleaned = [c for c in cleaned if c and not any(d in c for d in SKIP_DOMAINS)]
                if cleaned:
                    # Prefer root URLs
                    cleaned.sort(key=lambda u: len(urlparse(u).path))
                    return cleaned[0]
        
        # Strategy 3: Look for explicit homepage patterns in HTML
        # SaaSHub sometimes has the homepage as a referral link
        patterns = [
            r'href="(https?://[^"]+)\?ref=saashub"',
            r'href="(https?://[^"]+)"[^>]*>\s*Visit\s+Website',
            r'href="(https?://[^"]+)"[^>]*>\s*Official\s+Website',
            r'href="(https?://[^"]+)"[^>]*>\s*Go\s+to\s+Website',
            r'class="website-link"[^>]*href="(https?://[^"]+)"',
            r'data-website="(https?://[^"]+)"',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                url = _clean_url(match.group(1))
                if _is_likely_homepage(url, product_slug):
                    return url
        
        # Strategy 4: Look in external links, prioritizing those matching the product name
        if result.links and "external" in result.links:
            candidate_links = []
            
            for link in result.links["external"]:
                href = link.get("href", "")
                if not href or not href.startswith("http"):
                    continue
                
                # Skip known non-homepage domains
                if any(domain in href.lower() for domain in SKIP_DOMAINS):
                    continue
                    
                # Skip SaaSHub internal links
                if "saashub.com" in href.lower():
                    continue
                
                href = _clean_url(href)
                candidate_links.append(href)
            
            # Sort candidates: prioritize those containing the product slug in domain
            def score_link(href: str) -> int:
                parsed = urlparse(href)
                domain = parsed.netloc.lower()
                path = parsed.path.rstrip("/")
                path_depth = len([p for p in path.split("/") if p])
                
                score = 0
                slug_lower = product_slug.lower().replace("-", "").replace("_", "")
                domain_clean = domain.replace("-", "").replace("_", "").replace(".", "")
                
                # Big bonus if product slug is in domain
                if slug_lower and slug_lower in domain_clean:
                    score += 100
                
                # Prefer root pages
                score -= path_depth * 10
                
                # Prefer shorter domains (less likely to be subdomains)
                score -= len(domain.split(".")) * 5
                
                return score
            
            candidate_links.sort(key=score_link, reverse=True)
            
            if candidate_links:
                return candidate_links[0]
        
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
