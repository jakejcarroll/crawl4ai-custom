# Market Intelligence Collection System

> **Purpose**: Collect structured market data on SaaS products using a two-phase approach: target building from Product Hunt, then detailed extraction from product homepages.

---

## Quick Start

```bash
# 1. Set environment variables
export PRODUCTHUNT_ACCESS_TOKEN="your-token"  # Get from https://www.producthunt.com/v2/oauth/applications
export OPENAI_API_KEY="sk-..."

# 2. Build target list from Product Hunt (fast mode - skip URL resolution)
python -m crawl4ai.market_intel.collect target-build --no-resolve -v

# 3. Check status
python -m crawl4ai.market_intel.collect status

# 4. Extract data from targets
python -m crawl4ai.market_intel.collect extract -v
```

---

## Architecture Overview

### Two-Phase Collection

```
Phase 1: Target Building                 Phase 2: Data Extraction
┌─────────────────────────┐             ┌─────────────────────────┐
│   Product Hunt API      │             │   Target Homepage       │
│   ─────────────────     │             │   ─────────────────     │
│   • Trending products   │             │   • Crawl homepage      │
│   • Popular products    │             │   • Extract with LLM    │
│   • Topic-based search  │             │   • Structure data      │
└───────────┬─────────────┘             └───────────┬─────────────┘
            │                                       │
            ▼                                       ▼
┌─────────────────────────┐             ┌─────────────────────────┐
│   targets/targets.jsonl │────────────▶│   data/products.jsonl   │
│   ─────────────────────-│             │   ─────────────────────-│
│   • URL + metadata      │             │   • Structured product  │
│   • Status tracking     │             │     information         │
│   • Completion flags    │             │   • Features, pricing   │
└─────────────────────────┘             └─────────────────────────┘
```

### Why Two Phases?

1. **Decoupled workflows**: Build targets once, extract many times
2. **Rate limit resilience**: Each phase handles its own rate limits
3. **Progress tracking**: See exactly where you are in collection
4. **Resumable**: Pick up where you left off after interruptions

---

## Module Structure

```
crawl4ai/market_intel/
├── __init__.py          # Module exports (collect.py excluded - CLI only)
├── collect.py           # CLI orchestrator with subcommands
├── producthunt.py       # Product Hunt GraphQL client
├── rate_limiter.py      # Reactive rate limiting (pause only on 429)
├── targets.py           # Target list manager (JSONL storage)
├── schemas.py           # Pydantic models for LLM extraction
├── state.py             # Legacy state management
├── saashub.py           # Legacy SaaSHub client
└── url_discovery.py     # Homepage URL resolution

targets/
└── targets.jsonl        # Target list (created during collection)

data/
└── market_intel_products.jsonl  # Extracted product data
```

---

## CLI Commands

### `target-build` - Build Target List

Query Product Hunt API and add products to the target list.

```bash
# Basic usage - discover trending and popular products (fast mode)
python -m crawl4ai.market_intel.collect target-build --no-resolve -v

# With URL resolution (slower but gets final homepage URLs)
python -m crawl4ai.market_intel.collect target-build -v

# With specific topics
python -m crawl4ai.market_intel.collect target-build \
  --topics "Developer Tools" "Productivity" "SaaS" --no-resolve -v

# Control discovery scope
python -m crawl4ai.market_intel.collect target-build \
  --max-per-source 50 \
  --min-votes 50 \
  --no-trending \
  --no-resolve \
  -v
```

**Options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--topics` | None | Specific topics to search |
| `--max-per-source` | 100 | Max products per source (trending/popular/topic) |
| `--min-votes` | 20 | Minimum votes to include product |
| `--no-trending` | false | Skip trending products |
| `--no-popular` | false | Skip popular products |
| `--no-resolve` | false | Skip URL resolution (faster, uses Product Hunt redirect URLs) |
| `--targets-file` | targets/targets.jsonl | Target list file |

### `extract` - Extract Product Data

Scrape pending targets and extract structured data using LLM.

```bash
# Extract all pending targets
python -m crawl4ai.market_intel.collect extract -v

# Extract limited batch
python -m crawl4ai.market_intel.collect extract --max-targets 10 -v

# Use different LLM provider
python -m crawl4ai.market_intel.collect extract \
  --llm-provider "openai/gpt-4o-mini" -v
```

**Options:**
| Option | Default | Description |
|--------|---------|-------------|
| `--max-targets` | 0 (all) | Max targets to process |
| `--llm-provider` | openai/gpt-4o | LLM for extraction |
| `--targets-file` | targets/targets.jsonl | Target list file |
| `--output` | data/market_intel_products.jsonl | Output file |

**Performance Optimizations:**
The extraction phase uses optimized Crawl4AI settings for faster scraping:
- `text_mode=True` - Skip image loading
- `light_mode=True` - Minimal browser features
- `PruningContentFilter` - Remove boilerplate content
- `excluded_tags` - Skip nav, footer, header, aside elements
- `page_timeout=30000` - 30 second timeout (reduced from default)

### `status` - Check Progress

```bash
# Basic status
python -m crawl4ai.market_intel.collect status

# Show pending targets
python -m crawl4ai.market_intel.collect status --show-pending

# Show failed targets
python -m crawl4ai.market_intel.collect status --show-failed --limit 50
```

### `reset` - Reset Targets

```bash
# Reset only failed targets to pending
python -m crawl4ai.market_intel.collect reset --failed-only

# Delete entire target list
python -m crawl4ai.market_intel.collect reset
```

### `legacy` - SaaSHub Collection (Deprecated)

```bash
python -m crawl4ai.market_intel.collect legacy --seeds notion slack -v
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PRODUCTHUNT_ACCESS_TOKEN` | For target-build | Product Hunt API token |
| `OPENAI_API_KEY` | For extract | OpenAI API key for LLM extraction |

### Getting a Product Hunt Token

1. Go to https://www.producthunt.com/v2/oauth/applications
2. Create a new application
3. Generate a "Developer Token" (no OAuth flow needed)
4. Set: `export PRODUCTHUNT_ACCESS_TOKEN="your-token"`

---

## Rate Limiting

### Reactive Rate Limiting

The rate limiter uses a **reactive approach** - it only pauses when an actual 429 error is received, rather than proactively throttling requests. This maximizes throughput while still handling rate limits gracefully.

### Product Hunt API
- **Limit**: ~6250 requests per 15 minutes (generous)
- **Behavior**: On 429, pause for `retry-after` header value (or 60s default)
- **Auto-recovery**: Automatically resumes after pause

### OpenAI API
- **Behavior**: Exponential backoff starting at 60 seconds
- **Max backoff**: 5 minutes
- **Auto-recovery**: Continues after rate limit clears

---

## Data Formats

### Target Entry (`targets/targets.jsonl`)

```json
{
  "id": "ph_12345",
  "name": "Notion",
  "homepage_url": "https://notion.so",
  "producthunt_url": "https://www.producthunt.com/posts/notion",
  "status": "pending",
  "discovered_at": "2025-11-25T10:00:00Z",
  "votes_count": 1500,
  "tagline": "The all-in-one workspace",
  "topics": ["Productivity", "Note Taking"],
  "makers": [{"name": "Ivan Zhao", "username": "ivzhao"}]
}
```

**Status values:**
- `pending` - Not yet extracted
- `completed` - Successfully extracted
- `failed` - Extraction failed (with failure_reason)

### Extracted Product (`data/market_intel_products.jsonl`)

```json
{
  "source": "producthunt",
  "seed_query": "Productivity",
  "discovered_at": "2025-11-25T10:00:00Z",
  "homepage_url": "https://notion.so",
  "saashub_url": "https://www.producthunt.com/posts/notion",
  "product_info": {
    "name": "Notion",
    "tagline": "The all-in-one workspace",
    "description": "...",
    "features": ["Notes", "Databases", "Wikis"],
    "pricing_model": "freemium",
    "pricing_tiers": [...],
    "target_audience": ["Teams", "Individuals"],
    "integrations": ["Slack", "Google Drive"],
    "platforms": ["Web", "iOS", "Android", "Mac", "Windows"]
  },
  "extraction_success": true,
  "extracted_at": "2025-11-25T10:05:00Z"
}
```

---

## Troubleshooting

### Issue: "Product Hunt token required"

```bash
export PRODUCTHUNT_ACCESS_TOKEN="your-token"
```

Get token from https://www.producthunt.com/v2/oauth/applications

### Issue: Rate limited by Product Hunt (429)

The system auto-recovers. It will pause for 60 seconds (or the `retry-after` value) then resume.

```
[WARNING] Rate limited! Pausing for 60s. Will resume at 2025-11-25T10:01:00Z
```

### Issue: OpenAI rate limit errors

Uses exponential backoff automatically. If persistent:
1. Check your OpenAI usage dashboard
2. Reduce batch size: `--max-targets 5`
3. Wait for quota reset

### Issue: "No pending targets to extract"

Build targets first:
```bash
python -m crawl4ai.market_intel.collect target-build --no-resolve -v
```

### Issue: Many failed extractions

Check what failed:
```bash
python -m crawl4ai.market_intel.collect status --show-failed
```

Reset and retry:
```bash
python -m crawl4ai.market_intel.collect reset --failed-only
python -m crawl4ai.market_intel.collect extract -v
```

### Issue: RuntimeWarning about 'crawl4ai.market_intel.collect'

This warning has been fixed. If you see it, ensure you have the latest version where `collect.py` is not imported in `__init__.py`.

---

## Priority Topics for SaaS Discovery

The following topics yield high-quality B2B SaaS products:

```python
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
```

---

## Python API Usage

```python
import asyncio
from crawl4ai.market_intel.producthunt import ProductHuntClient, PRIORITY_TOPICS
from crawl4ai.market_intel.targets import TargetManager, Target
from crawl4ai.market_intel.rate_limiter import RateLimiter

async def main():
    # Initialize rate limiter (reactive - only pauses on 429)
    limiter = RateLimiter()
    
    # Discover products from Product Hunt
    async with ProductHuntClient(rate_limiter=limiter, min_votes=20) as client:
        async for product in client.get_popular_products(limit=50):
            print(f"{product.name}: {product.homepage_url}")
    
    # Manage targets
    manager = TargetManager("targets/targets.jsonl")
    
    # Add a target
    target = Target(
        id="ph_12345",
        name="My Product",
        homepage_url="https://example.com",
    )
    manager.add_target(target)
    
    # Get pending targets
    for target in manager.get_pending():
        print(f"Pending: {target.name}")
    
    # Mark as completed
    manager.mark_completed("ph_12345")

asyncio.run(main())
```

> **Note**: The `collect` module is a CLI entry point and is intentionally not exported from `crawl4ai.market_intel`. Import components directly from their modules if needed for programmatic use.

---

## Agent Instructions

When operating this system as an AI agent:

1. **Always check status first**: `python -m crawl4ai.market_intel.collect status`

2. **Build targets before extracting**: Run `target-build --no-resolve` if no pending targets

3. **Use verbose mode**: Add `-v` to see progress

4. **Handle rate limits gracefully**: The system auto-recovers with reactive rate limiting

5. **Check environment variables**: Ensure tokens are set before running

6. **Monitor progress**: Use `status` command between runs

7. **Reset failed on retry**: Use `reset --failed-only` before re-extracting

8. **Commit target list**: The targets.jsonl file should be version controlled

9. **Use --no-resolve for speed**: Skip URL resolution during target building for faster collection
