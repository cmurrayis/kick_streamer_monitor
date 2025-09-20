"""
OAuth authentication service with live Kick.com API integration.

Handles OAuth 2.1 client credentials flow with Kick.com API including
token management, refresh logic, and rate limiting compliance.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

import aiohttp
from aiohttp import ClientSession, ClientTimeout, ClientError
from pydantic import BaseModel, Field

# Import browser client for fallback
try:
    from .browser_client import BrowserAPIClient
    BROWSER_AVAILABLE = True
except ImportError:
    BROWSER_AVAILABLE = False

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Base exception for authentication errors."""
    pass


class TokenExpiredError(AuthenticationError):
    """Token has expired and needs refresh."""
    pass


class InvalidCredentialsError(AuthenticationError):
    """Invalid client credentials provided."""
    pass


class RateLimitError(AuthenticationError):
    """API rate limit exceeded."""
    pass


class TokenResponse(BaseModel):
    """OAuth token response model."""
    
    access_token: str = Field(..., description="Access token")
    token_type: str = Field("Bearer", description="Token type")
    expires_in: int = Field(..., description="Token lifetime in seconds")
    scope: Optional[str] = Field(None, description="Granted scopes")
    refresh_token: Optional[str] = Field(None, description="Refresh token")
    
    # Calculated fields
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = Field(None, description="Calculated expiration time")
    
    def __post_init_post_parse__(self):
        """Calculate expiration time."""
        if self.expires_at is None:
            self.expires_at = self.issued_at + timedelta(seconds=self.expires_in)
    
    def is_expired(self, margin_seconds: int = 300) -> bool:
        """Check if token is expired or will expire soon."""
        if not self.expires_at:
            return False
        
        margin = timedelta(seconds=margin_seconds)
        return datetime.now(timezone.utc) >= (self.expires_at - margin)
    
    def time_until_expiry(self) -> Optional[timedelta]:
        """Get time until token expires."""
        if not self.expires_at:
            return None
        
        return self.expires_at - datetime.now(timezone.utc)


class OAuthConfig:
    """OAuth configuration for Kick.com API."""
    
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str = "https://id.kick.com/oauth/token",
        api_base_url: str = "https://kick.com/api/v1",
        scopes: Optional[List[str]] = None,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        retry_delay_seconds: int = 1,
        refresh_margin_seconds: int = 300
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url
        self.api_base_url = api_base_url
        self.scopes = scopes or []
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.refresh_margin_seconds = refresh_margin_seconds
    
    def validate(self) -> None:
        """Validate configuration parameters."""
        if not self.client_id or not self.client_id.strip():
            raise ValueError("Client ID is required")
        
        if not self.client_secret or not self.client_secret.strip():
            raise ValueError("Client secret is required")
        
        if not self.token_url:
            raise ValueError("Token URL is required")
        
        if not self.api_base_url:
            raise ValueError("API base URL is required")


class KickOAuthService:
    """
    OAuth 2.1 service for Kick.com API authentication.
    
    Implements client credentials flow with automatic token refresh,
    rate limiting compliance, and browser fallback for Cloudflare bypass.
    """
    
    def __init__(self, config: OAuthConfig, enable_browser_fallback: bool = True):
        self.config = config
        self.config.validate()
        
        self._session: Optional[ClientSession] = None
        self._current_token: Optional[TokenResponse] = None
        self._token_lock = asyncio.Lock()
        self._last_request_time: Optional[datetime] = None
        self._request_count = 0
        
        # Browser fallback configuration
        self.enable_browser_fallback = enable_browser_fallback and BROWSER_AVAILABLE
        self._browser_client: Optional[BrowserAPIClient] = None
        self._oauth_blocked = False  # Track if OAuth is being blocked
        self._consecutive_oauth_failures = 0
        
        # Rate limiting (adjust based on Kick.com limits)
        self._rate_limit_requests = 100
        self._rate_limit_window = 60  # seconds
        self._request_times: List[datetime] = []
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def start(self) -> None:
        """Initialize the OAuth service."""
        if self._session:
            logger.warning("OAuth service already started")
            return
        
        timeout = ClientTimeout(total=self.config.timeout_seconds)
        self._session = ClientSession(
            timeout=timeout,
            headers={
                "User-Agent": "KickMonitor/1.0.0",
                "Accept": "application/json",
                "Content-Type": "application/json"
            }
        )
        
        # Initialize browser fallback if enabled
        if self.enable_browser_fallback:
            try:
                from .browser_client import BrowserAPIClient
                self._browser_client = BrowserAPIClient(headless=True)
                await self._browser_client.start()
                logger.info("OAuth service started with browser fallback")
            except Exception as e:
                logger.warning(f"Browser fallback initialization failed: {e}")
                logger.info("OAuth service started without browser fallback")
                self.enable_browser_fallback = False  # Disable since initialization failed
        else:
            logger.info("OAuth service started")
    
    async def close(self) -> None:
        """Close the OAuth service."""
        if self._session:
            await self._session.close()
            self._session = None
        
        if self._browser_client:
            await self._browser_client.close()
            self._browser_client = None
        
        self._current_token = None
        logger.info("OAuth service closed")
    
    async def get_access_token(self) -> str:
        """
        Get valid access token, refreshing if necessary.
        
        Returns:
            Valid access token
            
        Raises:
            AuthenticationError: If authentication fails
        """
        async with self._token_lock:
            # Check if we have a valid token
            if self._current_token and not self._current_token.is_expired(
                margin_seconds=self.config.refresh_margin_seconds
            ):
                return self._current_token.access_token
            
            # Need to get new token
            logger.info("Getting new access token")
            self._current_token = await self._request_token()
            
            logger.info(f"Access token obtained, expires in {self._current_token.expires_in} seconds")
            return self._current_token.access_token
    
    async def _request_token(self) -> TokenResponse:
        """Request new access token using client credentials flow."""
        if not self._session:
            raise AuthenticationError("OAuth service not started")
        
        # Prepare token request
        data = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret
        }
        
        # Only include scope if scopes are specified
        if self.config.scopes:
            data["scope"] = " ".join(self.config.scopes)
        
        # Apply rate limiting
        await self._apply_rate_limiting()
        
        for attempt in range(self.config.max_retries):
            try:
                logger.debug(f"Token request attempt {attempt + 1}/{self.config.max_retries}")
                
                async with self._session.post(
                    self.config.token_url,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                ) as response:
                    
                    self._record_request()
                    
                    if response.status == 200:
                        token_data = await response.json()
                        token = TokenResponse(**token_data)
                        token.__post_init_post_parse__()
                        return token
                    
                    elif response.status == 401:
                        error_data = await response.json() if response.content_type == 'application/json' else {}
                        error_msg = error_data.get('error_description', 'Invalid credentials')
                        raise InvalidCredentialsError(f"Authentication failed: {error_msg}")
                    
                    elif response.status == 429:
                        retry_after = response.headers.get('Retry-After', '60')
                        raise RateLimitError(f"Rate limit exceeded, retry after {retry_after} seconds")
                    
                    else:
                        error_text = await response.text()
                        logger.warning(f"Token request failed with status {response.status}: {error_text}")
                        
                        if attempt == self.config.max_retries - 1:
                            raise AuthenticationError(f"Token request failed: HTTP {response.status}")
            
            except ClientError as e:
                logger.warning(f"Token request network error (attempt {attempt + 1}): {e}")
                
                if attempt == self.config.max_retries - 1:
                    raise AuthenticationError(f"Token request failed: {e}") from e
            
            # Wait before retry
            if attempt < self.config.max_retries - 1:
                delay = self.config.retry_delay_seconds * (2 ** attempt)  # Exponential backoff
                logger.debug(f"Waiting {delay} seconds before retry")
                await asyncio.sleep(delay)
        
        raise AuthenticationError("Token request failed after all retries")
    
    async def make_authenticated_request(
        self,
        method: str,
        endpoint: str,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Make authenticated API request.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (relative to base URL)
            **kwargs: Additional arguments for aiohttp request
            
        Returns:
            JSON response data
            
        Raises:
            AuthenticationError: If authentication fails
        """
        if not self._session:
            raise AuthenticationError("OAuth service not started")
        
        # Get access token
        access_token = await self.get_access_token()
        
        # Prepare request
        url = f"{self.config.api_base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        headers = kwargs.pop('headers', {})
        headers.update({
            'Authorization': f"Bearer {access_token}",
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://kick.com/',
            'Origin': 'https://kick.com'
        })
        
        # Apply rate limiting
        await self._apply_rate_limiting()
        
        for attempt in range(self.config.max_retries):
            try:
                logger.debug(f"Request: {method.upper()} {url}")
                
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    **kwargs
                ) as response:
                    
                    self._record_request()
                    
                    if response.status == 200:
                        response_data = await response.json()
                        logger.debug(f"Success: {method} {endpoint}")
                        return response_data
                    
                    elif response.status == 401:
                        error_text = await response.text()
                        logger.error(f"401 Unauthorized: {error_text[:100]}")
                        # Token might be invalid, clear it and retry once
                        if attempt == 0:
                            logger.info("Token invalid, retrying with new token")
                            async with self._token_lock:
                                self._current_token = None
                            continue
                        else:
                            raise AuthenticationError("Authentication failed after token refresh")
                    
                    elif response.status == 403:
                        error_text = await response.text()
                        logger.error(f"403 BLOCKED by Cloudflare - URL: {url}")
                        logger.error(f"Response: {error_text[:200]}")
                        
                        # Track OAuth blocking
                        self._consecutive_oauth_failures += 1
                        if self._consecutive_oauth_failures >= 3:
                            self._oauth_blocked = True
                            logger.warning("OAuth consistently blocked, marking for fallback")
                        
                        if attempt == self.config.max_retries - 1:
                            raise AuthenticationError(f"Cloudflare blocked request: {response.status}")
                    
                    elif response.status == 429:
                        retry_after = response.headers.get('Retry-After', '60')
                        logger.warning(f"429 Rate Limited - retry after {retry_after}s")
                        raise RateLimitError(f"Rate limit exceeded, retry after {retry_after} seconds")
                    
                    elif response.status == 404:
                        logger.warning(f"404 Not Found: {endpoint}")
                        raise AuthenticationError(f"API endpoint not found: {endpoint}")
                    
                    else:
                        error_text = await response.text()
                        logger.warning(f"{response.status} Error: {error_text[:100]}")
                        
                        if attempt == self.config.max_retries - 1:
                            raise AuthenticationError(f"API request failed: HTTP {response.status}")
            
            except ClientError as e:
                logger.warning(f"Network error (attempt {attempt + 1}): {e}")
                
                if attempt == self.config.max_retries - 1:
                    raise AuthenticationError(f"API request failed: {e}") from e
            
            # Wait before retry
            if attempt < self.config.max_retries - 1:
                delay = self.config.retry_delay_seconds * (2 ** attempt)
                logger.debug(f"Waiting {delay}s before retry...")
                await asyncio.sleep(delay)
        
        raise AuthenticationError("API request failed after all retries")
    
    async def discover_websocket_config(self) -> Optional[Dict[str, Any]]:
        """
        Discover WebSocket configuration from Kick.com API.
        
        Returns:
            Dictionary with WebSocket config or None if not available
        """
        logger.info("Discovering WebSocket configuration from Kick.com API")
        
        # Try different potential endpoints for WebSocket discovery
        potential_endpoints = [
            "/api/v1/websocket/config",
            "/api/v1/socket/config", 
            "/api/v1/realtime/config",
            "/api/v2/websocket/config",
            "/api/internal/websocket/config"
        ]
        
        for endpoint in potential_endpoints:
            try:
                logger.debug(f"Trying WebSocket config endpoint: {endpoint}")
                response_data = await self.make_authenticated_request("GET", endpoint)
                
                if response_data:
                    logger.info(f"WebSocket config discovered from {endpoint}")
                    return response_data
                    
            except Exception as e:
                logger.debug(f"WebSocket config endpoint {endpoint} failed: {e}")
                continue
        
        logger.warning("Could not discover WebSocket configuration from any API endpoint")
        return None
    
    async def get_channel_websocket_info(self, channel_username: str) -> Optional[Dict[str, Any]]:
        """
        Get WebSocket information for a specific channel.
        
        Args:
            channel_username: The channel username to get WebSocket info for
            
        Returns:
            Dictionary with channel WebSocket info or None if not available
        """
        logger.info(f"Getting WebSocket info for channel: {channel_username}")
        
        try:
            # Try channel-specific endpoints
            endpoints_to_try = [
                f"/api/v1/channels/{channel_username}/websocket",
                f"/api/v1/channels/{channel_username}/socket",
                f"/api/v1/channels/{channel_username}/realtime",
                f"/api/v2/channels/{channel_username}/websocket"
            ]
            
            for endpoint in endpoints_to_try:
                try:
                    logger.debug(f"Trying channel WebSocket endpoint: {endpoint}")
                    response_data = await self.make_authenticated_request("GET", endpoint)
                    
                    if response_data:
                        logger.info(f"Channel WebSocket info found at {endpoint}")
                        return response_data
                        
                except Exception as e:
                    logger.debug(f"Channel WebSocket endpoint {endpoint} failed: {e}")
                    continue
        
        except Exception as e:
            logger.warning(f"Failed to get WebSocket info for channel {channel_username}: {e}")
        
        return None
    
    async def get_channel_info(self, username: str) -> Dict[str, Any]:
        """
        Get channel information for a streamer with browser fallback.
        
        Args:
            username: Streamer username
            
        Returns:
            Channel information from Kick.com API
        """
        # If OAuth is known to be blocked and browser is available, use browser directly
        if self._oauth_blocked and self.enable_browser_fallback:
            logger.debug(f"Using browser fallback for {username} (OAuth blocked)")
            return await self._get_channel_info_browser(username)
        
        # Try OAuth first
        try:
            result = await self.make_authenticated_request("GET", f"channels/{username}")
            # Reset failure counter on success
            self._consecutive_oauth_failures = 0
            return result
            
        except AuthenticationError as e:
            if "403" in str(e) or "Cloudflare" in str(e):
                # OAuth blocked, try browser fallback
                if self.enable_browser_fallback:
                    logger.info(f"OAuth blocked for {username}, trying browser fallback")
                    try:
                        return await self._get_channel_info_browser(username)
                    except Exception as browser_error:
                        logger.error(f"Browser fallback failed for {username}: {browser_error}")
                        raise AuthenticationError(f"Both OAuth and browser fallback failed: {browser_error}")
                else:
                    logger.error(f"OAuth blocked for {username}, browser fallback disabled")
                    raise
            else:
                # Other auth error, re-raise
                raise
    
    async def _get_channel_info_browser(self, username: str) -> Dict[str, Any]:
        """Get channel info using browser automation."""
        if not self._browser_client:
            raise AuthenticationError("Browser client not initialized")

        data = await self._browser_client.fetch_channel_data(username)
        if not data:
            raise AuthenticationError(f"Browser failed to fetch data for {username}")

        return data
    
    async def test_authentication(self) -> Dict[str, Any]:
        """
        Test authentication by checking if we can get channel info.
        
        Returns:
            Test result information
        """
        try:
            token = await self.get_access_token()
            
            # Test with a known username instead of blocked endpoints
            test_username = "xqc"  # Known public streamer
            
            try:
                # This will use browser fallback if OAuth is blocked
                result = await self.get_channel_info(test_username)
                return {
                    "status": "success", 
                    "token_expires_in": self._current_token.time_until_expiry().total_seconds() if self._current_token else None,
                    "test_result": f"Successfully fetched {test_username} channel info",
                    "browser_fallback_used": self._oauth_blocked
                }
            except Exception as api_error:
                # If even channel info fails, just return token status
                return {
                    "status": "token_obtained",
                    "token_expires_in": self._current_token.time_until_expiry().total_seconds() if self._current_token else None,
                    "note": f"Token obtained but API calls blocked: {str(api_error)}",
                    "browser_fallback_available": self.enable_browser_fallback
                }
        
        except Exception as e:
            return {
                "status": "failed",
                "error": str(e)
            }
    
    async def _apply_rate_limiting(self) -> None:
        """Apply rate limiting to avoid exceeding API limits."""
        now = datetime.now(timezone.utc)
        
        # Clean old request times
        cutoff = now - timedelta(seconds=self._rate_limit_window)
        self._request_times = [t for t in self._request_times if t > cutoff]
        
        # Check if we're at the limit
        if len(self._request_times) >= self._rate_limit_requests:
            # Calculate how long to wait
            oldest_request = min(self._request_times)
            wait_time = self._rate_limit_window - (now - oldest_request).total_seconds()
            
            if wait_time > 0:
                logger.info(f"Rate limit reached, waiting {wait_time:.1f} seconds")
                await asyncio.sleep(wait_time)
        
        # Also ensure minimum time between requests (more conservative)
        if self._last_request_time:
            min_interval = 1.0  # 1 second minimum between requests to avoid security policy blocking
            time_since_last = (now - self._last_request_time).total_seconds()
            if time_since_last < min_interval:
                await asyncio.sleep(min_interval - time_since_last)
    
    def _record_request(self) -> None:
        """Record request for rate limiting."""
        now = datetime.now(timezone.utc)
        self._request_times.append(now)
        self._last_request_time = now
        self._request_count += 1
    
    def get_token_info(self) -> Optional[Dict[str, Any]]:
        """Get current token information."""
        if not self._current_token:
            return None
        
        return {
            "token_type": self._current_token.token_type,
            "expires_at": self._current_token.expires_at.isoformat() if self._current_token.expires_at else None,
            "expires_in_seconds": self._current_token.time_until_expiry().total_seconds() if self._current_token.expires_at else None,
            "is_expired": self._current_token.is_expired(self.config.refresh_margin_seconds),
            "scope": self._current_token.scope
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            "total_requests": self._request_count,
            "recent_requests": len(self._request_times),
            "rate_limit_window": self._rate_limit_window,
            "rate_limit_max": self._rate_limit_requests,
            "last_request_time": self._last_request_time.isoformat() if self._last_request_time else None,
            "current_token": self.get_token_info(),
            "browser_fallback": {
                "enabled": self.enable_browser_fallback,
                "available": BROWSER_AVAILABLE,
                "oauth_blocked": self._oauth_blocked,
                "consecutive_failures": self._consecutive_oauth_failures
            }
        }
    
    async def invalidate_token(self) -> None:
        """Invalidate current token to force refresh on next request."""
        async with self._token_lock:
            self._current_token = None
        logger.info("Current token invalidated")