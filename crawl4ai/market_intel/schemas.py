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


class ProductFeature(BaseModel):
    """Detailed information about a single product feature."""
    name: str = Field(..., description="Feature name (e.g., 'Real-time collaboration')")
    description: Optional[str] = Field(None, description="Detailed description of what this feature does and how it works")
    benefit: Optional[str] = Field(None, description="The business benefit or value this feature provides to users")
    category: Optional[str] = Field(None, description="Feature category (e.g., 'Collaboration', 'Analytics', 'Automation', 'Security', 'Integration')")


class Differentiator(BaseModel):
    """What makes this product unique compared to alternatives."""
    aspect: str = Field(..., description="The differentiating aspect (e.g., 'AI-powered automation', 'No-code interface')")
    description: str = Field(..., description="Detailed explanation of how this differentiates the product")
    competitive_advantage: Optional[str] = Field(None, description="Why this matters vs competitors")


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
    value_proposition: Optional[str] = Field(None, description="The core value proposition - what main problem does this solve and for whom?")
    
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
    
    # Detailed Features & Capabilities
    key_features: List[str] = Field(
        default_factory=list,
        description="Main product features as simple list (top 10-15)"
    )
    detailed_features: List[ProductFeature] = Field(
        default_factory=list,
        description="Detailed breakdown of major features with descriptions and benefits"
    )
    core_capabilities: List[str] = Field(
        default_factory=list,
        description="Core technical capabilities (e.g., 'Real-time sync', 'Offline support', 'API access', 'Webhooks')"
    )
    ai_features: List[str] = Field(
        default_factory=list,
        description="AI/ML-powered features if any (e.g., 'AI writing assistant', 'Smart suggestions', 'Predictive analytics')"
    )
    automation_capabilities: List[str] = Field(
        default_factory=list,
        description="Automation features (e.g., 'Workflow automation', 'Scheduled tasks', 'Triggers and actions')"
    )
    collaboration_features: List[str] = Field(
        default_factory=list,
        description="Team collaboration features (e.g., 'Real-time editing', 'Comments', 'Mentions', 'Shared workspaces')"
    )
    security_features: List[str] = Field(
        default_factory=list,
        description="Security and compliance features (e.g., 'SSO', 'SOC2', 'GDPR', '2FA', 'Encryption')"
    )
    
    # Differentiators & Positioning
    differentiators: List[Differentiator] = Field(
        default_factory=list,
        description="What makes this product unique and different from competitors"
    )
    unique_selling_points: List[str] = Field(
        default_factory=list,
        description="Key unique selling points highlighted on the page"
    )
    competitive_positioning: Optional[str] = Field(
        None,
        description="How the product positions itself in the market (e.g., 'Enterprise-grade alternative to X', 'Simpler than Y')"
    )
    mentioned_competitors: List[str] = Field(
        default_factory=list,
        description="Any competitor products explicitly mentioned or compared against"
    )
    
    # Integrations & Ecosystem
    integrations: List[str] = Field(
        default_factory=list,
        description="Notable integrations mentioned (e.g., 'Slack', 'Salesforce', 'Zapier')"
    )
    integration_categories: List[str] = Field(
        default_factory=list,
        description="Categories of integrations available (e.g., 'CRM', 'Communication', 'Storage', 'Analytics')"
    )
    api_available: bool = Field(False, description="Whether a public API is available")
    platforms: List[str] = Field(
        default_factory=list,
        description="Supported platforms (e.g., 'Web', 'iOS', 'Android', 'Desktop', 'Mac', 'Windows')"
    )
    
    # Target Market
    target_audience: Optional[str] = Field(
        None,
        description="Primary target audience (e.g., 'Small businesses', 'Enterprise', 'Developers')"
    )
    target_company_size: List[str] = Field(
        default_factory=list,
        description="Target company sizes (e.g., 'Startup', 'SMB', 'Mid-market', 'Enterprise')"
    )
    target_roles: List[str] = Field(
        default_factory=list,
        description="Target job roles/personas (e.g., 'Product Managers', 'Developers', 'Marketing Teams')"
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
    
    # Social Proof & Traction
    customer_count: Optional[str] = Field(None, description="Number of customers if mentioned (e.g., '10,000+')")
    user_count: Optional[str] = Field(None, description="Number of users if mentioned (e.g., '2 million users')")
    notable_customers: List[str] = Field(
        default_factory=list,
        description="Notable customer names mentioned"
    )
    testimonials: List[str] = Field(
        default_factory=list,
        description="Key testimonial quotes or endorsements"
    )
    awards_recognition: List[str] = Field(
        default_factory=list,
        description="Awards, rankings, or recognition mentioned (e.g., 'G2 Leader', '#1 on Product Hunt')"
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
Extract comprehensive market intelligence about this SaaS product from the webpage.

## DETAILED EXTRACTION GUIDELINES:

### 1. Product Identity & Positioning
- Extract the official product name, tagline, and description
- Identify the core VALUE PROPOSITION: What main problem does this solve? For whom?
- Note any COMPETITIVE POSITIONING: How does it position itself vs alternatives?
- List any COMPETITORS explicitly mentioned or compared against

### 2. Features - Be Thorough and Specific
For DETAILED_FEATURES, extract 5-10 major features with:
- Feature name (be specific, e.g., "Real-time collaborative editing" not just "collaboration")
- Description: HOW the feature works, not just what it is
- Benefit: What business value or outcome does this provide?
- Category: Collaboration, Analytics, Automation, Security, Integration, etc.

Also extract:
- AI_FEATURES: Any AI/ML capabilities (smart suggestions, AI writing, predictive analytics, etc.)
- AUTOMATION_CAPABILITIES: Workflow automation, triggers, scheduled tasks, no-code builders
- COLLABORATION_FEATURES: Real-time editing, comments, mentions, sharing, permissions
- SECURITY_FEATURES: SSO, 2FA, SOC2, GDPR, encryption, audit logs, etc.
- CORE_CAPABILITIES: Technical capabilities like API, webhooks, offline support, mobile apps

### 3. Differentiators - What Makes It Unique
For each DIFFERENTIATOR, explain:
- What specific aspect sets this product apart
- HOW it's different from typical solutions
- WHY this matters (competitive advantage)

Look for phrases like: "the only", "unlike others", "first to", "best-in-class", "unique"

### 4. Integrations & Ecosystem  
- List specific integration names (Slack, Salesforce, Zapier, etc.)
- Note integration categories available
- Check if API is mentioned as available

### 5. Target Market
- WHO is this for? (company sizes, roles, industries)
- WHAT use cases are highlighted?
- Any specific personas or team types mentioned?

### 6. Social Proof
- Customer/user counts
- Notable customer logos/names
- Testimonial quotes
- Awards, rankings (G2, Capterra, Product Hunt, etc.)

### 7. Pricing
- Pricing model and tiers
- Free tier/trial availability
- Starting prices if shown

BE THOROUGH: Extract as much detail as possible. If a feature is mentioned, explain what it does.
If information is not on the page, leave those fields empty rather than guessing.
"""


def get_extraction_schema() -> dict:
    """Get the JSON schema for LLM extraction."""
    return SaaSProductInfo.model_json_schema()
