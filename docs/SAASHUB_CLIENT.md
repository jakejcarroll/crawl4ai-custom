# SaaSHub API Client Documentation

A minimal Python client for the [SaaSHub public API](https://www.saashub.com/site/api) that provides access to product alternatives and product details.

## Overview

The SaaSHub API follows the [JSON API specification](https://jsonapi.org/) and provides two main endpoints:
- `/api/alternatives/{query}` - Get alternatives for a product
- `/api/product/{query}` - Get product details

## Setup

### 1. Get Your API Key

1. Create an account at [SaaSHub](https://www.saashub.com/)
2. Navigate to your profile: https://www.saashub.com/profile/api_key
3. Copy your API key

### 2. Set Environment Variable

**Option A: Export in your shell**
```bash
export SAASHUB_API_KEY='your_api_key_here'
```

**Option B: Create a `.env` file** (recommended for development)
```bash
# In the project root directory
echo "SAASHUB_API_KEY=your_api_key_here" > .env
```

The client will automatically load the `.env` file using `python-dotenv`.

### 3. Install Dependencies

Dependencies are already in `requirements.txt`:
```bash
pip install httpx python-dotenv
```

Or install all project dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Python API

#### Basic Usage

```python
from saashub_client import SaaSHubClient

# Initialize client (reads SAASHUB_API_KEY from environment)
with SaaSHubClient() as client:
    # Get alternatives for a product
    alternatives = client.get_alternatives("notion", limit=5)
    
    # Get product details
    product = client.get_product("basecamp")
```

#### Get Alternatives

```python
from saashub_client import SaaSHubClient

with SaaSHubClient() as client:
    # Get all alternatives
    alternatives = client.get_alternatives("notion")
    
    # Get limited number of alternatives
    alternatives = client.get_alternatives("notion", limit=10)
    
    # Process results
    for alt in alternatives:
        print(f"Name: {alt['attributes']['name']}")
        print(f"Tagline: {alt['attributes']['tagline']}")
        print(f"URL: {alt['attributes']['saashubUrl']}")
        print("---")
```

**Example Output (trimmed):**
```json
[
  {
    "id": "abc123",
    "type": "product",
    "attributes": {
      "name": "Coda",
      "saashubUrl": "https://www.saashub.com/coda",
      "tagline": "A new doc for teams. Coda brings words, data, and teams together."
    }
  },
  {
    "id": "def456",
    "type": "product",
    "attributes": {
      "name": "Airtable",
      "saashubUrl": "https://www.saashub.com/airtable",
      "tagline": "Organize anything, with anyone, from anywhere."
    }
  }
]
```

#### Get Product Details

```python
from saashub_client import SaaSHubClient

with SaaSHubClient() as client:
    # Get product details
    product = client.get_product("notion")
    
    if product:
        attrs = product["attributes"]
        print(f"Name: {attrs['name']}")
        print(f"Tagline: {attrs['tagline']}")
        print(f"URL: {attrs['saashubUrl']}")
    else:
        print("Product not found")
```

**Example Output (trimmed):**
```json
{
  "id": "xyz789",
  "type": "product",
  "attributes": {
    "name": "Notion",
    "saashubUrl": "https://www.saashub.com/notion",
    "tagline": "One workspace. Every team."
  }
}
```

#### Manual API Key

```python
from saashub_client import SaaSHubClient

# Pass API key directly (overrides environment variable)
client = SaaSHubClient(api_key="your_api_key_here")

alternatives = client.get_alternatives("slack")

# Don't forget to close when not using context manager
client.close()
```

#### Error Handling

```python
from saashub_client import SaaSHubClient, SaaSHubAPIError

try:
    with SaaSHubClient() as client:
        alternatives = client.get_alternatives("notion")
        
except ValueError as e:
    # API key not found
    print(f"Configuration error: {e}")
    
except SaaSHubAPIError as e:
    # API request failed
    print(f"API error: {e}")
```

### Command-Line Interface

The module includes a CLI for quick testing and manual queries.

#### Get Alternatives

```bash
# Get all alternatives for a product
python saashub_client.py --alternatives notion

# Limit results
python saashub_client.py --alternatives notion --limit 5

# With explicit API key
python saashub_client.py --alternatives slack --api-key "your_key"
```

**Example Output:**
```bash
$ python saashub_client.py --alternatives notion --limit 3

Fetching alternatives for 'notion'...

Found 3 alternative(s):

[
  {
    "id": "abc123",
    "type": "product",
    "attributes": {
      "name": "Coda",
      "saashubUrl": "https://www.saashub.com/coda",
      "tagline": "A new doc for teams."
    }
  },
  {
    "id": "def456",
    "type": "product",
    "attributes": {
      "name": "Airtable",
      "saashubUrl": "https://www.saashub.com/airtable",
      "tagline": "Organize anything, with anyone."
    }
  },
  {
    "id": "ghi789",
    "type": "product",
    "attributes": {
      "name": "ClickUp",
      "saashubUrl": "https://www.saashub.com/clickup",
      "tagline": "One app to replace them all."
    }
  }
]
```

#### Get Product Details

```bash
# Get product details
python saashub_client.py --product basecamp

# With explicit API key
python saashub_client.py --product slack --api-key "your_key"
```

**Example Output:**
```bash
$ python saashub_client.py --product basecamp

Fetching product details for 'basecamp'...

Product details:

{
  "id": "0ab5e650b476",
  "type": "product",
  "attributes": {
    "name": "Basecamp",
    "saashubUrl": "https://www.saashub.com/basecamp-alternatives",
    "tagline": "A simple and elegant project management system."
  }
}
```

#### CLI Help

```bash
python saashub_client.py --help
```

## API Reference

### `SaaSHubClient`

Main client class for interacting with the SaaSHub API.

#### Constructor

```python
SaaSHubClient(
    api_key: Optional[str] = None,
    base_url: str = "https://www.saashub.com/api/",
    timeout: float = 30.0
)
```

**Parameters:**
- `api_key` (str, optional): SaaSHub API key. If None, reads from `SAASHUB_API_KEY` environment variable.
- `base_url` (str): Base URL for the API. Default: `https://www.saashub.com/api/`
- `timeout` (float): Request timeout in seconds. Default: 30.0

**Raises:**
- `ValueError`: If no API key is provided or found in environment

#### Methods

##### `get_alternatives(query: str, limit: Optional[int] = None) -> list[dict]`

Get alternatives for a given product.

**Parameters:**
- `query` (str): Product name or identifier (e.g., "notion", "basecamp")
- `limit` (int, optional): Maximum number of alternatives to return. None = all

**Returns:**
- `list[dict]`: List of product dictionaries with structure:
  ```python
  {
      "id": str,           # Product identifier
      "type": str,         # Resource type (typically "product")
      "attributes": {
          "name": str,           # Product name
          "saashubUrl": str,     # SaaSHub page URL
          "tagline": str,        # Product tagline/description
          # ... other attributes
      }
  }
  ```

**Raises:**
- `SaaSHubAPIError`: If the API request fails

##### `get_product(query: str) -> Optional[dict]`

Get product details for a given query.

**Parameters:**
- `query` (str): Product name or identifier (e.g., "notion", "basecamp")

**Returns:**
- `dict | None`: Product dictionary with same structure as alternatives, or None if not found

**Raises:**
- `SaaSHubAPIError`: If the API request fails (except 404)

##### `close()`

Close the HTTP client and cleanup resources. Called automatically when using context manager.

### Exceptions

#### `SaaSHubAPIError`

Base exception for all SaaSHub API errors. Raised when:
- HTTP request fails (4xx, 5xx errors)
- Network errors occur
- Rate limits are exceeded
- Response structure is unexpected

## Features

### Automatic Retry Logic

The client automatically retries failed requests up to 3 times with exponential backoff:
- Initial delay: 1 second
- Exponential backoff: 2x multiplier
- Retries on: Network errors, timeouts, 5xx server errors
- No retry on: 4xx client errors (except 429 rate limit)

### Rate Limit Handling

The client respects rate limits (HTTP 429) and automatically:
- Waits for the duration specified in `Retry-After` header
- Uses exponential backoff if no header is present
- Retries the request after waiting

### Context Manager Support

Use the client with Python's `with` statement for automatic resource cleanup:

```python
with SaaSHubClient() as client:
    alternatives = client.get_alternatives("notion")
# Client is automatically closed
```

## Integration Example

Here's a complete example for a market intelligence pipeline:

```python
from saashub_client import SaaSHubClient, SaaSHubAPIError
import json

def analyze_product_landscape(product_name: str, max_alternatives: int = 20):
    """
    Analyze the competitive landscape for a product.
    
    Args:
        product_name: Name of the product to analyze
        max_alternatives: Maximum number of alternatives to fetch
        
    Returns:
        dict: Analysis results with product and alternatives
    """
    try:
        with SaaSHubClient() as client:
            # Get the main product
            product = client.get_product(product_name)
            if not product:
                return {"error": f"Product '{product_name}' not found"}
            
            # Get alternatives
            alternatives = client.get_alternatives(product_name, limit=max_alternatives)
            
            # Build analysis
            analysis = {
                "product": {
                    "name": product["attributes"]["name"],
                    "tagline": product["attributes"]["tagline"],
                    "url": product["attributes"]["saashubUrl"]
                },
                "alternatives_count": len(alternatives),
                "alternatives": [
                    {
                        "name": alt["attributes"]["name"],
                        "tagline": alt["attributes"]["tagline"],
                        "url": alt["attributes"]["saashubUrl"]
                    }
                    for alt in alternatives
                ]
            }
            
            return analysis
            
    except SaaSHubAPIError as e:
        return {"error": f"API error: {e}"}

# Usage
if __name__ == "__main__":
    result = analyze_product_landscape("notion", max_alternatives=10)
    print(json.dumps(result, indent=2))
```

## Troubleshooting

### "API key is required" Error

**Problem:** Client can't find your API key.

**Solution:**
1. Check that `SAASHUB_API_KEY` is set: `echo $SAASHUB_API_KEY`
2. If using `.env` file, ensure it's in the correct directory
3. Try passing the key explicitly: `SaaSHubClient(api_key="your_key")`

### Rate Limit Errors

**Problem:** Getting HTTP 429 errors.

**Solution:**
- The client automatically handles rate limits with retry logic
- If you're making many requests, add delays between calls
- Consider caching results to reduce API calls

### Network Errors

**Problem:** Timeouts or connection errors.

**Solution:**
- Check your internet connection
- Increase timeout: `SaaSHubClient(timeout=60.0)`
- The client automatically retries network errors

### Product Not Found

**Problem:** `get_product()` returns `None`.

**Solution:**
- Verify the product exists on SaaSHub: https://www.saashub.com/
- Try different query variations (e.g., "github" vs "github-alternatives")
- Check for typos in the product name
