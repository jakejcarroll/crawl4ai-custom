# Crawl4AI Architecture & Usage Guide

> **Purpose**: This document explains how Crawl4AI works from a user's perspective and provides practical guidance for common use cases, especially for AI/RAG pipelines.

## Table of Contents
1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
3. [Primary Entry Points](#primary-entry-points)
4. [Common Use Cases](#common-use-cases)
5. [Configuration Patterns](#configuration-patterns)
6. [Output Formats](#output-formats)

---

## Overview

**Crawl4AI** is an open-source web crawler designed specifically to produce LLM-ready output. It transforms web content into clean, structured formats (primarily Markdown and JSON) optimized for AI/RAG applications.

**Key Philosophy**:
- **Async-first**: Built on `asyncio` and Playwright for high-performance concurrent crawling
- **Configuration-driven**: Separate browser setup (`BrowserConfig`) from crawl behavior (`CrawlerRunConfig`)
- **LLM-optimized**: Multiple output formats including "fit markdown" (noise-reduced) and structured extraction
- **Flexible**: Supports single URLs, multi-URL batches, deep crawling with domain/depth limits

---

## Core Concepts

### 1. **Two-Level Configuration**

Crawl4AI uses a clean separation between browser setup and crawl execution:

```python
# Browser-level settings (persistent across crawls)
BrowserConfig(
    headless=True,
    browser_type="chromium",  # or "firefox", "webkit", "undetected"
    verbose=True,
    proxy_config={...},
    user_agent_mode="random"
)

# Crawl-level settings (per-request)
CrawlerRunConfig(
    cache_mode=CacheMode.ENABLED,
    word_count_threshold=200,
    css_selector=".main-content",
    extraction_strategy=...,
    deep_crawl_strategy=...
)
```

### 2. **The Crawler Lifecycle**

You can use the crawler in two ways:

**Context Manager (Recommended for simple cases)**:
```python
async with AsyncWebCrawler(config=browser_config) as crawler:
    result = await crawler.arun(url="https://example.com", config=crawler_config)
```

**Explicit Lifecycle (Recommended for long-running apps)**:
```python
crawler = AsyncWebCrawler(config=browser_config)
await crawler.start()

# Use crawler multiple times
result1 = await crawler.arun(url="https://example.com", config=config1)
result2 = await crawler.arun(url="https://another.com", config=config2)

await crawler.close()
```

### 3. **Cache Modes**

Control caching behavior with `CacheMode`:
- `CacheMode.ENABLED`: Read from cache if available, write new results
- `CacheMode.BYPASS`: Always fetch fresh, don't write to cache
- `CacheMode.READ_ONLY`: Only read from cache, never fetch
- `CacheMode.WRITE_ONLY`: Always fetch fresh, always write to cache

### 4. **Result Structure**

Every crawl returns a `CrawlResult` object:

```python
result = await crawler.arun(url="...")

# Core content
result.html                    # Raw HTML
result.cleaned_html            # Processed HTML (after scraping strategy)
result.fit_html                # Preprocessed HTML for schema extraction
result.markdown                # MarkdownGenerationResult object (acts like string)
result.markdown.raw_markdown   # Full markdown
result.markdown.fit_markdown   # Noise-reduced markdown (AI-optimized)
result.markdown.markdown_with_citations  # With numbered citations
result.markdown.references_markdown      # Reference list

# Structured data
result.extracted_content       # JSON string from extraction strategy
result.links                   # {"internal": [...], "external": [...]}
result.media                   # {"images": [...], "videos": [...], "audios": [...]}
result.tables                  # List of extracted tables
result.metadata                # Page metadata (title, description, etc.)

# Additional data
result.screenshot              # Base64-encoded screenshot (if requested)
result.pdf                     # PDF bytes (if requested)
result.success                 # Boolean
result.error_message           # Error details if failed
result.status_code             # HTTP status
result.response_headers        # HTTP headers
```

---

## Primary Entry Points

### 1. **AsyncWebCrawler** (Main Class)

The primary interface for all crawling operations.

**Key Methods**:
- `arun(url, config)` - Crawl a single URL
- `arun_many(urls, config, dispatcher)` - Crawl multiple URLs concurrently
- `aseed_urls(domain, config)` - Discover URLs from sitemaps/Common Crawl
- `aprocess_html(url, html, config)` - Process already-fetched HTML

### 2. **Command-Line Interface**

The `crwl` command provides quick access:

```bash
# Basic crawl with markdown output
crwl https://www.example.com -o markdown

# Deep crawl with BFS strategy, max 10 pages
crwl https://docs.example.com --deep-crawl bfs --max-pages 10

# Use LLM extraction with a question
crwl https://www.example.com/products -q "Extract all product prices"

# Save to file
crwl https://example.com -o json --output-file results.json
```

### 3. **Docker/REST API**

For production deployments:

```python
import requests

# Submit crawl job
response = requests.post(
    "http://localhost:11235/crawl",
    json={"urls": ["https://example.com"], "priority": 10}
)

# Check results
task_id = response.json()["task_id"]
result = requests.get(f"http://localhost:11235/task/{task_id}")
```

Or use the Python client:

```python
from crawl4ai import Crawl4aiDockerClient

client = Crawl4aiDockerClient(base_url="http://localhost:11235")
results = await client.crawl(urls=["https://example.com"])
```

---

## Common Use Cases

### Use Case 1: Single URL for AI/RAG Pipeline

**Goal**: Crawl one URL and get clean, AI-ready markdown.

```python
import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai import DefaultMarkdownGenerator, PruningContentFilter

async def crawl_for_rag():
    browser_config = BrowserConfig(headless=True)
    
    # Use PruningContentFilter to remove noise
    crawler_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(
                threshold=0.48,
                threshold_type="fixed",
                min_word_threshold=0
            )
        )
    )
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url="https://www.nbcnews.com/business",
            config=crawler_config
        )
        
        if result.success:
            # Use fit_markdown for AI - it's noise-reduced
            ai_ready_content = result.markdown.fit_markdown
            print(f"Content length: {len(ai_ready_content)} chars")
            return ai_ready_content
        else:
            print(f"Error: {result.error_message}")
            return None

asyncio.run(crawl_for_rag())
```

**Key Points**:
- Use `fit_markdown` for AI/RAG - it's optimized and noise-reduced
- `PruningContentFilter` removes boilerplate (nav, footer, ads)
- Set `cache_mode=CacheMode.BYPASS` for fresh content

---

### Use Case 2: Multiple URLs with Concurrent Crawling

**Goal**: Crawl multiple URLs efficiently and process results as they complete.

```python
import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode

async def crawl_multiple_urls():
    urls = [
        "https://example.com/page1",
        "https://example.com/page2",
        "https://example.com/page3",
    ]
    
    crawler_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        word_count_threshold=100
    )
    
    async with AsyncWebCrawler() as crawler:
        # Batch mode: get all results at once
        results = await crawler.arun_many(urls=urls, config=crawler_config)
        
        for result in results:
            if result.success:
                print(f"✓ {result.url}: {len(result.markdown)} chars")
            else:
                print(f"✗ {result.url}: {result.error_message}")

asyncio.run(crawl_multiple_urls())
```

**Streaming Mode** (process results as they arrive):

```python
async def crawl_multiple_urls_streaming():
    urls = ["https://example.com/page1", "https://example.com/page2"]
    
    crawler_config = CrawlerRunConfig(
        cache_mode=CacheMode.ENABLED,
        stream=True  # Enable streaming
    )
    
    async with AsyncWebCrawler() as crawler:
        async for result in await crawler.arun_many(urls=urls, config=crawler_config):
            # Process each result immediately as it completes
            if result.success:
                print(f"Received: {result.url}")
                # Send to your RAG pipeline immediately
                await process_for_rag(result.markdown.fit_markdown)

asyncio.run(crawl_multiple_urls_streaming())
```

---

### Use Case 3: Deep Crawling with Domain/Depth Limits

**Goal**: Crawl a website systematically with control over scope and depth.

```python
import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai import BFSDeepCrawlStrategy, DFSDeepCrawlStrategy, BestFirstCrawlingStrategy
from crawl4ai import FilterChain, URLPatternFilter, DomainFilter

async def deep_crawl_documentation():
    # Define filters to control what gets crawled
    filter_chain = FilterChain([
        DomainFilter(
            allowed_domains=["docs.example.com"],
            blocked_domains=["ads.example.com"]
        ),
        URLPatternFilter(patterns=["*/docs/*", "*/api/*"])  # Only docs and API pages
    ])
    
    # Choose a crawl strategy
    deep_strategy = BFSDeepCrawlStrategy(
        max_depth=3,              # Crawl up to 3 levels deep
        max_pages=50,             # Limit total pages
        include_external=False,   # Stay within same domain
        filter_chain=filter_chain
    )
    
    crawler_config = CrawlerRunConfig(
        deep_crawl_strategy=deep_strategy,
        cache_mode=CacheMode.ENABLED,
        stream=True  # Process results as they arrive
    )
    
    async with AsyncWebCrawler() as crawler:
        async for result in await crawler.arun(
            url="https://docs.example.com",
            config=crawler_config
        ):
            depth = result.metadata.get("depth", 0)
            print(f"Depth {depth}: {result.url}")
            
            # Save to your vector database
            await save_to_vectordb(
                url=result.url,
                content=result.markdown.fit_markdown,
                metadata={"depth": depth, "title": result.metadata.get("title")}
            )

asyncio.run(deep_crawl_documentation())
```

**Deep Crawl Strategies**:
- `BFSDeepCrawlStrategy`: Breadth-first (all pages at depth N before depth N+1)
- `DFSDeepCrawlStrategy`: Depth-first (follow one path to max depth, then backtrack)
- `BestFirstCrawlingStrategy`: Prioritize by score (use with URL scorers)

**Filters**:
- `DomainFilter`: Allow/block specific domains
- `URLPatternFilter`: Match URL patterns (glob syntax: `*`, `?`)
- `ContentTypeFilter`: Filter by MIME type
- `SEOFilter`: Filter based on SEO metadata
- `ContentRelevanceFilter`: Filter by content similarity to query

---

### Use Case 4: Structured Data Extraction (No LLM)

**Goal**: Extract structured data using CSS/XPath selectors (fast, no API costs).

```python
import asyncio
import json
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai import JsonCssExtractionStrategy

async def extract_products():
    # Define extraction schema
    schema = {
        "name": "Product Listing",
        "baseSelector": "div.product-card",  # Container for each item
        "fields": [
            {
                "name": "title",
                "selector": "h2.product-title",
                "type": "text"
            },
            {
                "name": "price",
                "selector": "span.price",
                "type": "text"
            },
            {
                "name": "image",
                "selector": "img.product-image",
                "type": "attribute",
                "attribute": "src"
            },
            {
                "name": "link",
                "selector": "a.product-link",
                "type": "attribute",
                "attribute": "href"
            }
        ]
    }
    
    extraction_strategy = JsonCssExtractionStrategy(schema, verbose=True)
    
    crawler_config = CrawlerRunConfig(
        extraction_strategy=extraction_strategy,
        cache_mode=CacheMode.BYPASS
    )
    
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url="https://example.com/products",
            config=crawler_config
        )
        
        if result.success:
            products = json.loads(result.extracted_content)
            print(f"Extracted {len(products)} products")
            for product in products[:3]:
                print(json.dumps(product, indent=2))

asyncio.run(extract_products())
```

---

### Use Case 5: LLM-Based Extraction for AI Pipelines

**Goal**: Use an LLM to extract structured data with schema validation.

```python
import asyncio
import os
from pydantic import BaseModel, Field
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai import LLMExtractionStrategy, LLMConfig

# Define your data schema with Pydantic
class Article(BaseModel):
    title: str = Field(..., description="Article title")
    author: str = Field(..., description="Author name")
    publish_date: str = Field(..., description="Publication date")
    summary: str = Field(..., description="Brief summary of the article")
    key_points: list[str] = Field(..., description="List of key points")

async def extract_with_llm():
    browser_config = BrowserConfig(headless=True)
    
    crawler_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        extraction_strategy=LLMExtractionStrategy(
            llm_config=LLMConfig(
                provider="openai/gpt-4o",  # or "anthropic/claude-3-sonnet", "ollama/llama2"
                api_token=os.getenv("OPENAI_API_KEY")
            ),
            schema=Article.model_json_schema(),
            extraction_type="schema",
            instruction="Extract article information from the page content.",
            extra_args={"temperature": 0, "max_tokens": 2000}
        )
    )
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url="https://example.com/article",
            config=crawler_config
        )
        
        if result.success:
            import json
            article_data = json.loads(result.extracted_content)
            print(json.dumps(article_data, indent=2))

asyncio.run(extract_with_llm())
```

**Supported LLM Providers** (via LiteLLM):
- OpenAI: `openai/gpt-4o`, `openai/gpt-4o-mini`
- Anthropic: `anthropic/claude-3-sonnet`, `anthropic/claude-3-opus`
- Google: `gemini/gemini-pro`
- Local: `ollama/llama2`, `ollama/mistral`
- Many more: See [LiteLLM docs](https://docs.litellm.ai/docs/providers)

---

### Use Case 6: Dynamic Content with JavaScript

**Goal**: Crawl pages that require JavaScript execution (SPAs, lazy loading).

```python
import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

async def crawl_dynamic_page():
    browser_config = BrowserConfig(
        headless=True,
        java_script_enabled=True
    )
    
    # JavaScript to execute before capturing content
    js_code = """
    // Click "Load More" button
    const loadMoreBtn = document.querySelector('button.load-more');
    if (loadMoreBtn) loadMoreBtn.click();
    
    // Wait for content to load
    await new Promise(resolve => setTimeout(resolve, 2000));
    """
    
    crawler_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        js_code=js_code,
        wait_for="css:.content-loaded",  # Wait for this selector
        delay_before_return_html=2.0     # Additional wait time
    )
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url="https://example.com/dynamic-page",
            config=crawler_config
        )
        
        print(result.markdown[:500])

asyncio.run(crawl_dynamic_page())
```

**Infinite Scroll / Lazy Loading**:

```python
from crawl4ai import VirtualScrollConfig

scroll_config = VirtualScrollConfig(
    container_selector="[data-testid='feed']",
    scroll_count=20,              # Number of scrolls
    scroll_by="container_height", # or specific pixel value
    wait_after_scroll=1.0         # Wait time after each scroll
)

crawler_config = CrawlerRunConfig(
    virtual_scroll_config=scroll_config,
    cache_mode=CacheMode.BYPASS
)
```

---

## Configuration Patterns

### Pattern 1: Multi-Config for Different URL Types

Apply different configurations to different URLs in a single batch:

```python
from crawl4ai import CrawlerRunConfig, MatchMode

configs = [
    # Config for documentation pages
    CrawlerRunConfig(
        url_matcher=["*docs*", "*documentation*"],
        cache_mode=CacheMode.WRITE_ONLY,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter()
        )
    ),
    
    # Config for blog posts
    CrawlerRunConfig(
        url_matcher=lambda url: 'blog' in url or 'news' in url,
        cache_mode=CacheMode.BYPASS,
        word_count_threshold=500
    ),
    
    # Fallback config for everything else
    CrawlerRunConfig()
]

results = await crawler.arun_many(urls=mixed_urls, config=configs)
```

### Pattern 2: Session-Based Crawling

Maintain browser state across multiple requests (for authenticated sites):

```python
session_id = "my_session_123"

# First request: login
login_config = CrawlerRunConfig(
    session_id=session_id,
    js_code="document.querySelector('#login-form').submit();",
    cache_mode=CacheMode.BYPASS
)

result1 = await crawler.arun(url="https://example.com/login", config=login_config)

# Subsequent requests: use same session
crawl_config = CrawlerRunConfig(
    session_id=session_id,  # Reuse browser context
    cache_mode=CacheMode.BYPASS
)

result2 = await crawler.arun(url="https://example.com/dashboard", config=crawl_config)
```

### Pattern 3: Proxy Rotation

```python
from crawl4ai import ProxyConfig, RoundRobinProxyStrategy

proxies = [
    ProxyConfig(server="http://proxy1.example.com:8080"),
    ProxyConfig(server="http://proxy2.example.com:8080", username="user", password="pass")
]

proxy_strategy = RoundRobinProxyStrategy(proxies)

crawler_config = CrawlerRunConfig(
    proxy_rotation_strategy=proxy_strategy,
    cache_mode=CacheMode.BYPASS
)
```

### Pattern 4: Anti-Bot / Stealth Mode

```python
browser_config = BrowserConfig(
    browser_type="undetected",  # Use undetected-chromedriver
    headless=True,
    user_agent_mode="random",
    user_agent_generator_config={
        "device_type": "mobile",
        "os_type": "android"
    }
)

crawler_config = CrawlerRunConfig(
    magic=True,              # Enable anti-detection measures
    simulate_user=True,      # Simulate human behavior
    override_navigator=True  # Override navigator properties
)
```

---

## Output Formats

### 1. **Markdown** (Primary for AI/RAG)

```python
result.markdown                          # StringCompatibleMarkdown object
result.markdown.raw_markdown             # Full markdown (string)
result.markdown.fit_markdown             # Noise-reduced, AI-optimized (string)
result.markdown.markdown_with_citations  # With [1], [2] citations (string)
result.markdown.references_markdown      # Reference list (string)
```

**When to use**:
- `raw_markdown`: Full content preservation
- `fit_markdown`: **Recommended for AI/RAG** - removes noise, keeps core content
- `markdown_with_citations`: When you need source attribution
- `references_markdown`: List of all links as numbered references

### 2. **HTML Variants**

```python
result.html          # Original raw HTML
result.cleaned_html  # After scraping strategy processing
result.fit_html      # Preprocessed for schema extraction (size-limited)
```

### 3. **Structured JSON**

```python
result.extracted_content  # JSON string from extraction strategy
result.links              # {"internal": [...], "external": [...]}
result.media              # {"images": [...], "videos": [...], "audios": [...]}
result.tables             # List of table data
result.metadata           # Page metadata dict
```

### 4. **Binary Formats**

```python
result.screenshot  # Base64-encoded PNG (if screenshot=True)
result.pdf         # PDF bytes (if pdf=True)
result.mhtml       # MHTML archive (if mhtml=True)
```

---

## Quick Reference: Minimal Code Paths

### Simplest Crawl (One URL → Markdown)

```python
import asyncio
from crawl4ai import AsyncWebCrawler

async def main():
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url="https://example.com")
        print(result.markdown)

asyncio.run(main())
```

### AI/RAG Pipeline (One URL → Clean Markdown)

```python
import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai import DefaultMarkdownGenerator, PruningContentFilter

async def main():
    config = CrawlerRunConfig(
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilter(threshold=0.48)
        )
    )
    
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url="https://example.com", config=config)
        ai_content = result.markdown.fit_markdown  # Use this for RAG
        return ai_content

asyncio.run(main())
```

### Multiple URLs → Structured Data

```python
import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig

async def main():
    urls = ["https://example.com/1", "https://example.com/2"]
    
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun_many(urls=urls)
        
        for result in results:
            print(f"{result.url}: {len(result.markdown)} chars")

asyncio.run(main())
```

### Deep Crawl (Domain + Depth Limits)

```python
import asyncio
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai import BFSDeepCrawlStrategy

async def main():
    config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=2,
            max_pages=20,
            include_external=False
        )
    )
    
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun(url="https://docs.example.com", config=config)
        
        for result in results:
            depth = result.metadata.get("depth", 0)
            print(f"Depth {depth}: {result.url}")

asyncio.run(main())
```

---

## Summary

**For AI/RAG Use Cases**:
1. Use `AsyncWebCrawler` with `CrawlerRunConfig`
2. Get content via `result.markdown.fit_markdown` (noise-reduced)
3. For multiple URLs, use `arun_many()` with `stream=True`
4. For deep crawling, use `BFSDeepCrawlStrategy` with domain/depth limits
5. For structured extraction, use `LLMExtractionStrategy` or `JsonCssExtractionStrategy`

**Key Objects**:
- `AsyncWebCrawler`: Main crawler class
- `BrowserConfig`: Browser-level settings (headless, proxy, user agent)
- `CrawlerRunConfig`: Per-request settings (cache, extraction, filters)
- `CrawlResult`: Result object with markdown, HTML, metadata, etc.

**Output for AI**:
- **Best choice**: `result.markdown.fit_markdown` - optimized for LLMs
- Alternative: `result.markdown.raw_markdown` - full content
- Structured: `result.extracted_content` - JSON from extraction strategies

**Documentation**: https://docs.crawl4ai.com
**GitHub**: https://github.com/unclecode/crawl4ai
