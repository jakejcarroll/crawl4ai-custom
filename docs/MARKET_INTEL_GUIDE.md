# Market Intelligence Collection System - Agent Guide

> **Purpose**: This guide provides a local AI agent with complete context to operate, debug, and extend the market intelligence collection system in this codebase.

---

## System Overview

This fork of crawl4ai includes a custom **market intelligence module** (`crawl4ai/market_intel/`) that:

1. **Discovers** SaaS product URLs via the SaaSHub API
2. **Crawls** product homepages to extract content
3. **Extracts** structured data using GPT-4o LLM

The system is designed for **rate-limit-aware, incremental collection** with state persistence.

---

## Module Architecture

```
crawl4ai/market_intel/
├── __init__.py          # Module exports
├── saashub.py           # SaaSHub API client with rate limiting
├── schemas.py           # Pydantic models for LLM extraction
├── state.py             # State management for resumable runs
├── url_discovery.py     # Extract homepage URLs from SaaSHub pages
└── collect.py           # Main CLI orchestrator (entry point)

configs/
└── market_intel_seeds.yml   # 150+ seed queries organized by category

.github/workflows/
└── market-intel.yml     # GitHub Actions workflow for scheduled runs
```

---

## Key Components

### 1. SaaSHub Client (`saashub.py`)

```python
from crawl4ai.market_intel import SaaSHubClient

client = SaaSHubClient(request_delay=12)  # 12s between requests (safe rate)
alternatives = await client.get_alternatives("notion")  # Returns list of product names
product_info = await client.get_product("notion")       # Returns product details
```

**Rate Limiting**:
- Default delay: 12 seconds between requests
- Raises `RateLimitError` on 429 responses
- Cloudflare may block requests from cloud IPs (GitHub Codespaces, Actions)

### 2. Schemas (`schemas.py`)

```python
from crawl4ai.market_intel.schemas import SaaSProductInfo, CollectedProduct

# SaaSProductInfo - Used for LLM extraction
# Fields: name, tagline, description, features, pricing_model, pricing_tiers,
#         target_audience, integrations, platforms, founded_year, headquarters, etc.

# CollectedProduct - Wraps extraction with metadata
# Fields: source_url, saashub_slug, discovered_at, extracted_at, extraction_confidence, data
```

**LLM Instruction**: `schemas.EXTRACTION_INSTRUCTION` contains the prompt for GPT-4o.

### 3. State Management (`state.py`)

```python
from crawl4ai.market_intel.state import CollectionState

state = CollectionState.load("output/market_intel")
state.add_discovered_url("notion", "https://notion.so", "Notion")
state.mark_crawled("https://notion.so")
state.mark_extracted("https://notion.so", success=True)
state.save()
```

**Halt Condition**: System halts after 3 consecutive LLM failures (rate limit protection).

### 4. URL Discovery (`url_discovery.py`)

```python
from crawl4ai.market_intel.url_discovery import extract_homepage_from_saashub

# Crawls SaaSHub product page to find the actual homepage URL
homepage = await extract_homepage_from_saashub(crawler, "notion")
# Returns: "https://notion.so" or None
```

### 5. Main Orchestrator (`collect.py`)

Entry point for the collection pipeline. Run via:

```bash
python -m crawl4ai.market_intel.collect [OPTIONS]
```

---

## Running the Collector

### Environment Requirements

```bash
# Required for LLM extraction
export OPENAI_API_KEY="sk-..."

# Optional: Custom output directory
export MARKET_INTEL_OUTPUT="./output/market_intel"
```

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--seeds` | all | Comma-separated seed names from config |
| `--custom-seeds` | - | Ad-hoc seeds not in config file |
| `--max-per-seed` | 10 | Max products to discover per seed |
| `--delay` | 12 | Seconds between SaaSHub API requests |
| `--skip-homepages` | false | Skip homepage crawling phase |
| `--skip-extraction` | false | Skip LLM extraction phase |
| `--output-dir` | output/market_intel | Output directory |
| `--verbose` | false | Enable detailed logging |

### Example Commands

```bash
# 1. Discovery only (no API key needed, test SaaSHub connectivity)
python -m crawl4ai.market_intel.collect \
  --seeds notion,slack \
  --max-per-seed 3 \
  --skip-homepages \
  --skip-extraction \
  --verbose

# 2. Full pipeline with specific seeds
python -m crawl4ai.market_intel.collect \
  --seeds notion \
  --max-per-seed 5 \
  --delay 15 \
  --verbose

# 3. Custom seeds (not in config)
python -m crawl4ai.market_intel.collect \
  --custom-seeds "figma,canva,miro" \
  --max-per-seed 3 \
  --verbose

# 4. Resume interrupted run (state auto-loads)
python -m crawl4ai.market_intel.collect \
  --seeds notion \
  --verbose
```

### Output Files

```
output/market_intel/
├── discovery_state.json    # URLs discovered per seed
├── collection_state.json   # Progress tracking (crawled, extracted, failures)
└── products.jsonl          # Extracted product data (one JSON per line)
```

---

## Diagnosing Issues

### Issue 1: SaaSHub API Returns 403 (Cloudflare Block)

**Symptom**:
```
API request failed: 403 - <!DOCTYPE html>...Sorry, you have been blocked
```

**Cause**: Cloudflare blocks requests from cloud provider IPs (GitHub Codespaces, Actions, AWS, etc.)

**Solutions**:
1. Run locally on your machine (residential IP)
2. Use a proxy service
3. Add delay: `--delay 30` (longer delays sometimes help)

**Verification**:
```bash
curl -I "https://www.saashub.com/api/alternatives/notion"
# Should return 200, not 403
```

### Issue 2: Rate Limit Hit (429 Response)

**Symptom**:
```
RateLimitError: Rate limited by SaaSHub API
```

**Cause**: Too many requests in short period (limit ~5/min)

**Solutions**:
1. Increase delay: `--delay 20` or higher
2. Reduce batch size: `--max-per-seed 3`
3. Wait and resume (state is preserved)

### Issue 3: LLM Extraction Fails Repeatedly

**Symptom**:
```
Halting: 3 consecutive LLM failures detected
```

**Cause**: OpenAI API rate limit or invalid API key

**Diagnosis**:
```bash
# Test OpenAI API directly
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

**Solutions**:
1. Verify `OPENAI_API_KEY` is set and valid
2. Check OpenAI usage dashboard for quota
3. Wait for rate limit reset (usually 1 minute)
4. Delete `collection_state.json` to reset failure counter

### Issue 4: No URLs Discovered

**Symptom**:
```
Discovered 0 URLs for seed: notion
```

**Cause**: SaaSHub API returned empty or seed doesn't exist

**Diagnosis**:
```bash
# Test API response
curl "https://www.saashub.com/api/alternatives/notion" | jq
```

**Solutions**:
1. Verify seed exists on SaaSHub website
2. Try alternative seed names (e.g., "notion-app" vs "notion")
3. Check `configs/market_intel_seeds.yml` for valid seeds

### Issue 5: Homepage Extraction Returns None

**Symptom**:
```
Could not extract homepage for: some-product
```

**Cause**: SaaSHub page structure changed or product has no homepage link

**Diagnosis**:
```python
# In Python, test the extraction
from crawl4ai import AsyncWebCrawler
from crawl4ai.market_intel.url_discovery import extract_homepage_from_saashub

async with AsyncWebCrawler() as crawler:
    url = await extract_homepage_from_saashub(crawler, "notion")
    print(url)
```

**Solutions**:
1. Check if product page exists: `https://www.saashub.com/notion`
2. Update `url_discovery.py` selectors if page structure changed

### Issue 6: Import Errors

**Symptom**:
```
ModuleNotFoundError: No module named 'crawl4ai.market_intel'
```

**Solutions**:
1. Ensure you're in the repo root directory
2. Install in dev mode: `pip install -e .`
3. Check `crawl4ai/market_intel/__init__.py` exists

---

## State File Reference

### `discovery_state.json`

```json
{
  "notion": [
    {"url": "https://www.saashub.com/notion", "name": "Notion", "homepage": "https://notion.so"},
    {"url": "https://www.saashub.com/coda", "name": "Coda", "homepage": null}
  ]
}
```

### `collection_state.json`

```json
{
  "discovered_urls": {"notion": [...]},
  "crawled_urls": ["https://notion.so"],
  "extracted_urls": ["https://notion.so"],
  "failed_urls": {},
  "consecutive_llm_failures": 0,
  "last_updated": "2025-11-25T10:30:00Z"
}
```

### `products.jsonl`

```json
{"source_url": "https://notion.so", "saashub_slug": "notion", "discovered_at": "...", "extracted_at": "...", "extraction_confidence": 0.85, "data": {"name": "Notion", "tagline": "...", ...}}
```

---

## Extending the System

### Adding New Seed Categories

Edit `configs/market_intel_seeds.yml`:

```yaml
new_category:
  - seed1
  - seed2
```

### Adding New Extraction Fields

Edit `crawl4ai/market_intel/schemas.py`:

```python
class SaaSProductInfo(BaseModel):
    # ... existing fields ...
    new_field: Optional[str] = Field(None, description="Description for LLM")
```

Update `EXTRACTION_INSTRUCTION` to mention the new field.

### Adding Alternative URL Sources

Create new discovery function in `url_discovery.py`:

```python
async def discover_from_new_source(crawler, query: str) -> List[str]:
    # Implement discovery logic
    pass
```

Integrate in `collect.py` discovery phase.

---

## GitHub Actions Workflow

The workflow (`.github/workflows/market-intel.yml`) supports:

- **Manual trigger**: Actions → Market Intel Collection → Run workflow
- **Scheduled runs**: Daily at 2 AM UTC (configurable)
- **Modes**: `discover_only`, `full`, `extract_only`

### Required Secrets

Set in repo Settings → Secrets → Actions:

- `OPENAI_API_KEY`: For LLM extraction

### Artifacts

Each run uploads:
- `market-intel-state`: State files for resuming
- `market-intel-data`: Extracted products JSONL

---

## Quick Debugging Checklist

1. ✅ Is `OPENAI_API_KEY` set? (`echo $OPENAI_API_KEY`)
2. ✅ Can you reach SaaSHub? (`curl -I https://www.saashub.com`)
3. ✅ Is the module installed? (`python -c "from crawl4ai.market_intel import SaaSHubClient"`)
4. ✅ Does state file exist? Check `output/market_intel/collection_state.json`
5. ✅ Are there consecutive failures? Check `consecutive_llm_failures` in state
6. ✅ Is the seed valid? Test on `https://www.saashub.com/search?q=<seed>`

---

## Code Flow Summary

```
collect.py main()
    │
    ├─► Phase 1: Discovery
    │   └─► saashub.get_alternatives(seed)
    │       └─► For each product: url_discovery.extract_homepage_from_saashub()
    │
    ├─► Phase 2: Homepage Crawling
    │   └─► AsyncWebCrawler.arun(homepage_url)
    │       └─► Store markdown content
    │
    └─► Phase 3: LLM Extraction
        └─► LLMExtractionStrategy(schema=SaaSProductInfo)
            └─► Write to products.jsonl
```

Each phase checks state to skip already-processed URLs, enabling resumable runs.
