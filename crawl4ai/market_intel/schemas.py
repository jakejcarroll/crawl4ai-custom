"""
Pydantic schemas for market intelligence data extraction.

These schemas are used with LLMExtractionStrategy to extract structured
market data from SaaS product homepages.
"""

from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


class PricingModel(str, Enum):
    """Common SaaS pricing models."""
    FREE = "free"
    FREEMIUM = "freemium"
    FREE_TRIAL = "free_trial"
    SUBSCRIPTION = "subscription"
    ONE_TIME = "one_time"
    USAGE_BASED = "usage_based"
    PER_SEAT = "per_seat"
    TIERED = "tiered"
    CUSTOM = "custom"
    UNKNOWN = "unknown"


class PricingTier(BaseModel):
    """Individual pricing tier information."""
    name: str = Field(..., description="Name of the pricing tier (e.g., 'Pro', 'Enterprise')")
    price: Optional[str] = Field(None, description="Price as displayed (e.g., '$10/month', 'Custom')")
    billing_period: Optional[str] = Field(None, description="Billing period (e.g., 'monthly', 'yearly')")
    features: List[str] = Field(default_factory=list, description="Key features included in this tier")


class SaaSProductInfo(BaseModel):
    """
    Structured information about a SaaS product.
    
    This schema is designed to capture market intelligence data from
    product homepages, including pricing, features, and positioning.
    """
    
    # Basic Information
    name: str = Field(..., description="Official product name")
    tagline: Optional[str] = Field(None, description="Product tagline or slogan")
    description: Optional[str] = Field(None, description="Brief product description (1-2 sentences)")
    
    # Pricing Information
    pricing_model: PricingModel = Field(
        default=PricingModel.UNKNOWN,
        description="Primary pricing model"
    )
    has_free_tier: bool = Field(False, description="Whether product offers a free tier")
    has_free_trial: bool = Field(False, description="Whether product offers a free trial")
    trial_length_days: Optional[int] = Field(None, description="Free trial length in days")
    starting_price: Optional[str] = Field(None, description="Lowest paid price point (e.g., '$5/month')")
    pricing_tiers: List[PricingTier] = Field(
        default_factory=list,
        description="List of pricing tiers if available"
    )
    
    # Features & Capabilities
    key_features: List[str] = Field(
        default_factory=list,
        description="Main product features (top 5-10)"
    )
    integrations: List[str] = Field(
        default_factory=list,
        description="Notable integrations mentioned (e.g., 'Slack', 'Salesforce')"
    )
    platforms: List[str] = Field(
        default_factory=list,
        description="Supported platforms (e.g., 'Web', 'iOS', 'Android', 'Desktop')"
    )
    
    # Target Market
    target_audience: Optional[str] = Field(
        None,
        description="Primary target audience (e.g., 'Small businesses', 'Enterprise', 'Developers')"
    )
    use_cases: List[str] = Field(
        default_factory=list,
        description="Primary use cases mentioned"
    )
    industries: List[str] = Field(
        default_factory=list,
        description="Target industries if mentioned"
    )
    
    # Company Information
    company_name: Optional[str] = Field(None, description="Parent company name if different from product")
    founded_year: Optional[int] = Field(None, description="Year company/product was founded")
    headquarters: Optional[str] = Field(None, description="Company headquarters location")
    
    # Social Proof
    customer_count: Optional[str] = Field(None, description="Number of customers if mentioned (e.g., '10,000+')")
    notable_customers: List[str] = Field(
        default_factory=list,
        description="Notable customer names mentioned"
    )


class ProductDiscovery(BaseModel):
    """
    Minimal product info for URL discovery from SaaSHub pages.
    Used when crawling SaaSHub to extract homepage URLs.
    """
    name: str = Field(..., description="Product name")
    homepage_url: Optional[str] = Field(None, description="Product's official homepage URL")
    saashub_url: str = Field(..., description="SaaSHub page URL")
    saashub_id: Optional[str] = Field(None, description="SaaSHub product ID")
    tagline: Optional[str] = Field(None, description="Product tagline from SaaSHub")
    category: Optional[str] = Field(None, description="Primary category")


class CollectedProduct(BaseModel):
    """
    Complete collected product data combining discovery and extraction.
    This is the final output format written to JSONL.
    """
    # Discovery metadata
    source: str = Field("saashub", description="Source of discovery (e.g., 'saashub')")
    seed_query: str = Field(..., description="Seed query that discovered this product")
    discovered_at: str = Field(..., description="ISO timestamp of discovery")
    
    # URLs
    homepage_url: str = Field(..., description="Product homepage URL")
    saashub_url: Optional[str] = Field(None, description="SaaSHub page URL")
    
    # Extracted data
    product_info: Optional[SaaSProductInfo] = Field(
        None,
        description="Extracted product information (None if extraction failed)"
    )
    
    # Extraction metadata
    extraction_success: bool = Field(False, description="Whether LLM extraction succeeded")
    extraction_error: Optional[str] = Field(None, description="Error message if extraction failed")
    extracted_at: Optional[str] = Field(None, description="ISO timestamp of extraction")


# LLM extraction instruction for SaaSProductInfo
EXTRACTION_INSTRUCTION = """
Extract structured information about this SaaS product from the webpage.
Focus on:
1. Product name and description
2. Pricing information (tiers, prices, free trial availability)
3. Key features and capabilities
4. Target audience and use cases
5. Company information

If information is not clearly stated on the page, leave those fields empty.
For pricing, extract exact prices if shown (e.g., "$10/month").
For features, list the main ones mentioned (limit to top 10).
"""


def get_extraction_schema() -> dict:
    """Get the JSON schema for LLM extraction."""
    return SaaSProductInfo.model_json_schema()
