"""
Resilient rate limiter for API calls with automatic pause/resume.

Handles rate limits from Product Hunt and OpenAI by:
1. Detecting 429 responses or rate limit errors
2. Saving state before sleeping
3. Sleeping for the appropriate retry period
4. Automatically resuming from where it left off

This ensures the script never crashes due to rate limits.
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Any, TypeVar, Awaitable

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimitSource(str, Enum):
    """Sources that may impose rate limits."""
    PRODUCT_HUNT = "producthunt"
    OPENAI = "openai"
    SAASHUB = "saashub"


@dataclass
class RateLimitConfig:
    """Configuration for a rate limit source."""
    source: RateLimitSource
    requests_per_period: int
    period_seconds: int
    default_retry_seconds: int
    max_retry_seconds: int = 3600  # 1 hour max
    backoff_multiplier: float = 2.0
    
    @property
    def min_delay_seconds(self) -> float:
        """Minimum delay between requests to stay under limit."""
        return self.period_seconds / self.requests_per_period


# Default configurations for known APIs
RATE_LIMIT_CONFIGS = {
    RateLimitSource.PRODUCT_HUNT: RateLimitConfig(
        source=RateLimitSource.PRODUCT_HUNT,
        requests_per_period=500,
        period_seconds=15 * 60,  # 15 minutes
        default_retry_seconds=15 * 60,  # 15 minutes
    ),
    RateLimitSource.OPENAI: RateLimitConfig(
        source=RateLimitSource.OPENAI,
        requests_per_period=60,  # Varies by tier, conservative default
        period_seconds=60,
        default_retry_seconds=60,
        max_retry_seconds=300,
        backoff_multiplier=2.0,
    ),
    RateLimitSource.SAASHUB: RateLimitConfig(
        source=RateLimitSource.SAASHUB,
        requests_per_period=5,
        period_seconds=60,
        default_retry_seconds=60,
    ),
}


@dataclass
class RateLimitState:
    """Tracks rate limit state for a source."""
    source: RateLimitSource
    last_request_time: float = 0.0
    request_count: int = 0
    period_start_time: float = 0.0
    consecutive_failures: int = 0
    current_retry_delay: float = 0.0
    paused_at: Optional[str] = None
    resume_at: Optional[str] = None
    
    def reset_period(self) -> None:
        """Reset the rate limit period."""
        self.request_count = 0
        self.period_start_time = time.time()
    
    def reset_failures(self) -> None:
        """Reset failure tracking after a successful request."""
        self.consecutive_failures = 0
        self.current_retry_delay = 0.0
        self.paused_at = None
        self.resume_at = None


class RateLimiter:
    """
    Resilient rate limiter with automatic pause/resume.
    
    Usage:
        limiter = RateLimiter()
        
        # Simple delay-based limiting
        await limiter.wait(RateLimitSource.PRODUCT_HUNT)
        
        # With automatic retry on rate limit errors
        result = await limiter.execute_with_retry(
            RateLimitSource.OPENAI,
            some_async_function,
            on_rate_limit=save_state_callback,
        )
    """
    
    def __init__(
        self,
        configs: Optional[dict[RateLimitSource, RateLimitConfig]] = None,
        on_pause: Optional[Callable[[RateLimitSource, int], Awaitable[None]]] = None,
    ):
        """
        Initialize the rate limiter.
        
        Args:
            configs: Optional custom rate limit configurations
            on_pause: Optional callback when rate limit pause begins
        """
        self.configs = configs or RATE_LIMIT_CONFIGS.copy()
        self.states: dict[RateLimitSource, RateLimitState] = {}
        self.on_pause = on_pause
        
        # Initialize states for all configured sources
        for source in self.configs:
            self.states[source] = RateLimitState(source=source)
    
    def _get_config(self, source: RateLimitSource) -> RateLimitConfig:
        """Get configuration for a source."""
        if source not in self.configs:
            raise ValueError(f"No configuration for source: {source}")
        return self.configs[source]
    
    def _get_state(self, source: RateLimitSource) -> RateLimitState:
        """Get or create state for a source."""
        if source not in self.states:
            self.states[source] = RateLimitState(source=source)
        return self.states[source]
    
    async def wait(self, source: RateLimitSource) -> None:
        """
        Wait for the minimum delay before the next request.
        
        This is a simple delay-based limiter that ensures we don't
        exceed the rate limit by spacing out requests.
        """
        config = self._get_config(source)
        state = self._get_state(source)
        
        now = time.time()
        
        # Check if we need to reset the period
        if now - state.period_start_time >= config.period_seconds:
            state.reset_period()
        
        # Calculate required delay
        elapsed = now - state.last_request_time
        required_delay = config.min_delay_seconds - elapsed
        
        if required_delay > 0:
            logger.debug(f"[{source.value}] Waiting {required_delay:.1f}s before next request")
            await asyncio.sleep(required_delay)
        
        # Update state
        state.last_request_time = time.time()
        state.request_count += 1
    
    async def handle_rate_limit(
        self,
        source: RateLimitSource,
        retry_after: Optional[int] = None,
    ) -> int:
        """
        Handle a rate limit error by pausing appropriately.
        
        Args:
            source: The source that rate limited us
            retry_after: Optional retry-after header value in seconds
            
        Returns:
            The number of seconds we waited
        """
        config = self._get_config(source)
        state = self._get_state(source)
        
        # Calculate retry delay
        if retry_after:
            delay = retry_after
        elif state.consecutive_failures == 0:
            delay = config.default_retry_seconds
        else:
            # Exponential backoff
            delay = min(
                state.current_retry_delay * config.backoff_multiplier,
                config.max_retry_seconds
            )
        
        # Update state
        state.consecutive_failures += 1
        state.current_retry_delay = delay
        state.paused_at = datetime.utcnow().isoformat() + "Z"
        state.resume_at = datetime.fromtimestamp(
            time.time() + delay
        ).isoformat() + "Z"
        
        # Log the pause
        logger.warning(
            f"[{source.value}] Rate limited! Pausing for {delay}s "
            f"(attempt {state.consecutive_failures}). "
            f"Will resume at {state.resume_at}"
        )
        
        # Call pause callback if provided
        if self.on_pause:
            await self.on_pause(source, delay)
        
        # Sleep
        await asyncio.sleep(delay)
        
        # Reset period after sleeping
        state.reset_period()
        
        return delay
    
    async def execute_with_retry(
        self,
        source: RateLimitSource,
        func: Callable[..., Awaitable[T]],
        *args,
        max_retries: int = 5,
        on_rate_limit: Optional[Callable[[], Awaitable[None]]] = None,
        **kwargs,
    ) -> T:
        """
        Execute an async function with automatic rate limit retry.
        
        Args:
            source: The rate limit source for this call
            func: The async function to execute
            *args: Positional arguments for the function
            max_retries: Maximum number of retries on rate limit
            on_rate_limit: Optional callback before sleeping (e.g., save state)
            **kwargs: Keyword arguments for the function
            
        Returns:
            The result of the function
            
        Raises:
            Exception: If max retries exceeded or non-rate-limit error
        """
        state = self._get_state(source)
        
        for attempt in range(max_retries + 1):
            # Wait for rate limit before attempting
            await self.wait(source)
            
            try:
                result = await func(*args, **kwargs)
                
                # Success - reset failures
                state.reset_failures()
                return result
                
            except Exception as e:
                # Check if this is a rate limit error
                is_rate_limit = self._is_rate_limit_error(e, source)
                
                if not is_rate_limit:
                    # Not a rate limit error, re-raise
                    raise
                
                if attempt >= max_retries:
                    logger.error(
                        f"[{source.value}] Max retries ({max_retries}) exceeded"
                    )
                    raise
                
                # Extract retry-after if available
                retry_after = self._extract_retry_after(e)
                
                # Call the callback before sleeping
                if on_rate_limit:
                    await on_rate_limit()
                
                # Handle the rate limit
                await self.handle_rate_limit(source, retry_after)
        
        # Should never reach here
        raise RuntimeError("Unexpected end of retry loop")
    
    def _is_rate_limit_error(self, error: Exception, source: RateLimitSource) -> bool:
        """Check if an error is a rate limit error."""
        error_str = str(error).lower()
        
        # Check for common rate limit indicators
        if "429" in error_str or "rate limit" in error_str:
            return True
        
        if "too many requests" in error_str:
            return True
        
        # OpenAI specific
        if source == RateLimitSource.OPENAI:
            if "rate_limit_exceeded" in error_str:
                return True
            if "quota" in error_str:
                return True
        
        # Product Hunt specific
        if source == RateLimitSource.PRODUCT_HUNT:
            if "throttle" in error_str:
                return True
        
        return False
    
    def _extract_retry_after(self, error: Exception) -> Optional[int]:
        """Extract retry-after value from an error if available."""
        # Check for retry-after attribute
        if hasattr(error, "retry_after"):
            return error.retry_after
        
        # Check for response with retry-after header
        if hasattr(error, "response"):
            response = error.response
            if hasattr(response, "headers"):
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    try:
                        return int(retry_after)
                    except ValueError:
                        pass
        
        return None
    
    def get_state_dict(self) -> dict:
        """Get the current state as a dict for serialization."""
        return {
            source.value: {
                "last_request_time": state.last_request_time,
                "request_count": state.request_count,
                "period_start_time": state.period_start_time,
                "consecutive_failures": state.consecutive_failures,
                "current_retry_delay": state.current_retry_delay,
                "paused_at": state.paused_at,
                "resume_at": state.resume_at,
            }
            for source, state in self.states.items()
        }
    
    def load_state_dict(self, state_dict: dict) -> None:
        """Load state from a dict."""
        for source_str, state_data in state_dict.items():
            try:
                source = RateLimitSource(source_str)
                state = self._get_state(source)
                state.last_request_time = state_data.get("last_request_time", 0.0)
                state.request_count = state_data.get("request_count", 0)
                state.period_start_time = state_data.get("period_start_time", 0.0)
                state.consecutive_failures = state_data.get("consecutive_failures", 0)
                state.current_retry_delay = state_data.get("current_retry_delay", 0.0)
                state.paused_at = state_data.get("paused_at")
                state.resume_at = state_data.get("resume_at")
            except ValueError:
                # Unknown source, skip
                pass
