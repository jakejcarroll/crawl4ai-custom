# Market Intelligence Collection System

> **Purpose**: Collect structured market data on SaaS products using a two-phase approach: target building from Product Hunt, then detailed extraction from product homepages.

---

## Quick Start

```bash
# 1. Set environment variables
export PRODUCTHUNT_ACCESS_TOKEN="your-token"  # Get from https://www.producthunt.com/v2/oauth/applications
export OPENAI_API_KEY="sk-..."

# 2. Build target list from Product Hunt
python -m crawl4ai.market_intel.collect target-build -v

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
├── __init__.py          # Module exports
├── collect.py           # CLI orchestrator with subcommands
├── producthunt.py       # Product Hunt GraphQL client
├── rate_limiter.py      # Resilient rate limiting with auto-recovery
├── targets.py           # Target list manager (JSONL storage)
├── schemas.py           # Pydantic models for LLM extraction
├── state.py             # Legacy state management
├── saashub.py           # Legacy SaaSHub client
└── url_discovery.py     # Legacy homepage URL extraction

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
# Basic usage - discover trending and popular products
python -m crawl4ai.market_intel.collect target-build -v

# With specific topics
python -m crawl4ai.market_intel.collect target-build \
  --topics "Developer Tools" "Productivity" "SaaS" -v

# Control discovery scope
python -m crawl4ai.market_intel.collect target-build \
  --max-per-source 50 \
  --min-votes 50 \
  --no-trending \
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

## Rate Limits

### Product Hunt API
- **Limit**: 500 requests per 15 minutes
- **Behavior**: Auto-pause for 15 minutes on 429, then resume
- **Min interval**: 1.8 seconds between requests

### OpenAI API
- **Behavior**: Exponential backoff starting at 60 seconds
- **Max backoff**: 10 minutes
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

The system auto-recovers. Just wait 15 minutes or let it run.

```
[WARNING] Rate limited! Pausing for 900s. Will resume at 2025-11-25T10:30:00Z
```

### Issue: OpenAI rate limit errors

Uses exponential backoff automatically. If persistent:
1. Check your OpenAI usage dashboard
2. Reduce batch size: `--max-targets 5`
3. Wait for quota reset

### Issue: "No pending targets to extract"

Build targets first:
```bash
python -m crawl4ai.market_intel.collect target-build -v
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
from crawl4ai.market_intel import (
    ProductHuntClient,
    TargetManager,
    Target,
    RateLimiter,
)

async def main():
    # Initialize rate limiter
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

---

## Agent Instructions

When operating this system as an AI agent:

1. **Always check status first**: `python -m crawl4ai.market_intel.collect status`

2. **Build targets before extracting**: Run `target-build` if no pending targets

3. **Use verbose mode**: Add `-v` to see progress

4. **Handle rate limits gracefully**: The system auto-recovers, just wait

5. **Check environment variables**: Ensure tokens are set before running

6. **Monitor progress**: Use `status` command between runs

7. **Reset failed on retry**: Use `reset --failed-only` before re-extracting

8. **Commit target list**: The targets.jsonl file should be version controlled
