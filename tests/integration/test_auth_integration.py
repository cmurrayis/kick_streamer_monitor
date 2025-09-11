"""
Integration tests for authentication and OAuth flows.

Tests the complete OAuth authentication flow with Kick.com API,
token management, refresh mechanisms, and error handling.

These tests require:
- Valid Kick.com OAuth application credentials
- Internet connectivity to Kick.com API
- Configuration for test OAuth credentials

Set these environment variables or use .env.test:
- TEST_KICK_CLIENT_ID
- TEST_KICK_CLIENT_SECRET
- TEST_KICK_REDIRECT_URI
"""

import pytest
import asyncio
import os
import aiohttp
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock, patch
from typing import Dict, Any, Optional
from urllib.parse import urlparse, parse_qs

from src.services.auth import OAuthService, TokenInfo
from src.lib.config import ConfigurationManager
from src.lib.errors import AuthenticationError, NetworkError


class TestOAuthIntegration:
    """Integration tests for OAuth authentication flow."""
    
    @pytest.fixture(autouse=True)
    def setup_auth_config(self):
        """Setup OAuth configuration for testing."""
        self.oauth_config = {
            "client_id": os.getenv("TEST_KICK_CLIENT_ID", "test_client_id"),
            "client_secret": os.getenv("TEST_KICK_CLIENT_SECRET", "test_client_secret"),
            "redirect_uri": os.getenv("TEST_KICK_REDIRECT_URI", "http://localhost:8080/auth/callback"),
            "base_url": "https://kick.com",
            "api_base_url": "https://kick.com/api"
        }
        
        # Skip tests if OAuth not configured for real testing
        if not os.getenv("TEST_KICK_CLIENT_ID"):
            pytest.skip("OAuth credentials required for integration tests")
    
    @pytest.fixture
    async def oauth_service(self):
        """Create OAuth service for testing."""
        config_manager = ConfigurationManager(auto_load=False)
        config_manager.load_from_dict({
            "kick": self.oauth_config
        })
        
        service = OAuthService(config_manager)
        yield service
        
        # Cleanup
        await service.close()
    
    @pytest.fixture
    async def mock_oauth_service(self):
        """Create OAuth service with mocked HTTP responses for testing."""
        config_manager = ConfigurationManager(auto_load=False)
        config_manager.load_from_dict({
            "kick": self.oauth_config
        })
        
        with patch('aiohttp.ClientSession') as mock_session:
            service = OAuthService(config_manager)
            yield service, mock_session
            await service.close()
    
    def test_oauth_authorization_url_generation(self, oauth_service):
        """Test OAuth authorization URL generation."""
        # Test basic authorization URL
        auth_url = oauth_service.get_authorization_url()
        
        assert auth_url is not None
        assert "kick.com" in auth_url
        assert "client_id=" in auth_url
        assert "redirect_uri=" in auth_url
        assert "response_type=code" in auth_url
        assert "scope=" in auth_url
        
        # Parse URL to validate parameters
        parsed = urlparse(auth_url)
        query_params = parse_qs(parsed.query)
        
        assert query_params["client_id"][0] == self.oauth_config["client_id"]
        assert query_params["redirect_uri"][0] == self.oauth_config["redirect_uri"]
        assert query_params["response_type"][0] == "code"
    
    def test_oauth_authorization_url_with_state(self, oauth_service):
        """Test OAuth authorization URL with custom state."""
        state = "custom_state_value_123"
        auth_url = oauth_service.get_authorization_url(state=state)
        
        parsed = urlparse(auth_url)
        query_params = parse_qs(parsed.query)
        
        assert query_params["state"][0] == state
    
    def test_oauth_authorization_url_with_scopes(self, oauth_service):
        """Test OAuth authorization URL with custom scopes."""
        scopes = ["read", "write", "admin"]
        auth_url = oauth_service.get_authorization_url(scopes=scopes)
        
        parsed = urlparse(auth_url)
        query_params = parse_qs(parsed.query)
        
        scope_param = query_params["scope"][0]
        for scope in scopes:
            assert scope in scope_param
    
    @pytest.mark.asyncio
    async def test_token_exchange_success(self, mock_oauth_service):
        """Test successful token exchange from authorization code."""
        oauth_service, mock_session = mock_oauth_service
        
        # Mock successful token response
        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "access_token": "test_access_token_123",
            "refresh_token": "test_refresh_token_456",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read write"
        })
        
        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
        
        # Test token exchange
        result = await oauth_service.exchange_code_for_token("test_auth_code_789")
        
        assert result is True
        
        # Verify token info
        token_info = oauth_service.get_token_info()
        assert token_info is not None
        assert token_info["access_token"] == "test_access_token_123"
        assert token_info["refresh_token"] == "test_refresh_token_456"
        assert token_info["token_type"] == "Bearer"
        assert token_info["expires_in"] == 3600
    
    @pytest.mark.asyncio
    async def test_token_exchange_invalid_code(self, mock_oauth_service):
        """Test token exchange with invalid authorization code."""
        oauth_service, mock_session = mock_oauth_service
        
        # Mock error response
        mock_response = Mock()
        mock_response.status = 400
        mock_response.json = AsyncMock(return_value={
            "error": "invalid_grant",
            "error_description": "Invalid authorization code"
        })
        
        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
        
        # Test token exchange should fail
        with pytest.raises(AuthenticationError) as exc_info:
            await oauth_service.exchange_code_for_token("invalid_code")
        
        assert "invalid_grant" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_token_refresh_success(self, mock_oauth_service):
        """Test successful token refresh."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set initial token
        initial_token = TokenInfo(
            access_token="old_access_token",
            refresh_token="refresh_token_123",
            token_type="Bearer",
            expires_in=3600,
            scope="read write",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)  # Expired
        )
        oauth_service._token = initial_token
        
        # Mock successful refresh response
        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "access_token": "new_access_token_456",
            "refresh_token": "new_refresh_token_789",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read write"
        })
        
        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
        
        # Test token refresh
        result = await oauth_service.refresh_token()
        
        assert result is True
        
        # Verify new token
        token_info = oauth_service.get_token_info()
        assert token_info["access_token"] == "new_access_token_456"
        assert token_info["refresh_token"] == "new_refresh_token_789"
    
    @pytest.mark.asyncio
    async def test_token_refresh_invalid_refresh_token(self, mock_oauth_service):
        """Test token refresh with invalid refresh token."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set token with invalid refresh token
        initial_token = TokenInfo(
            access_token="access_token",
            refresh_token="invalid_refresh_token",
            token_type="Bearer",
            expires_in=3600,
            scope="read write",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        oauth_service._token = initial_token
        
        # Mock error response
        mock_response = Mock()
        mock_response.status = 400
        mock_response.json = AsyncMock(return_value={
            "error": "invalid_grant",
            "error_description": "Invalid refresh token"
        })
        
        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
        
        # Test refresh should fail
        with pytest.raises(AuthenticationError):
            await oauth_service.refresh_token()
    
    @pytest.mark.asyncio
    async def test_api_request_with_valid_token(self, mock_oauth_service):
        """Test making API requests with valid token."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set valid token
        valid_token = TokenInfo(
            access_token="valid_access_token",
            refresh_token="refresh_token",
            token_type="Bearer",
            expires_in=3600,
            scope="read",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        oauth_service._token = valid_token
        
        # Mock API response
        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": 12345,
            "username": "test_user",
            "email": "test@example.com"
        })
        
        mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response
        
        # Test API request
        response = await oauth_service.make_authenticated_request("GET", "/user/profile")
        
        assert response is not None
        assert response["id"] == 12345
        assert response["username"] == "test_user"
        
        # Verify Authorization header was sent
        call_kwargs = mock_session.return_value.__aenter__.return_value.get.call_args[1]
        assert "headers" in call_kwargs
        assert "Authorization" in call_kwargs["headers"]
        assert call_kwargs["headers"]["Authorization"] == "Bearer valid_access_token"
    
    @pytest.mark.asyncio
    async def test_api_request_with_expired_token_auto_refresh(self, mock_oauth_service):
        """Test API request with expired token triggers automatic refresh."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set expired token
        expired_token = TokenInfo(
            access_token="expired_access_token",
            refresh_token="refresh_token_123",
            token_type="Bearer",
            expires_in=3600,
            scope="read",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)  # Expired
        )
        oauth_service._token = expired_token
        
        # Mock responses - first refresh, then API call
        responses = [
            # Token refresh response
            Mock(status=200, json=AsyncMock(return_value={
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "read"
            })),
            # API response
            Mock(status=200, json=AsyncMock(return_value={
                "data": "api_response_data"
            }))
        ]
        
        # Configure mock to return different responses for post vs get
        mock_session_instance = mock_session.return_value.__aenter__.return_value
        mock_session_instance.post.return_value.__aenter__.return_value = responses[0]
        mock_session_instance.get.return_value.__aenter__.return_value = responses[1]
        
        # Test API request should trigger refresh and succeed
        response = await oauth_service.make_authenticated_request("GET", "/user/profile")
        
        assert response is not None
        assert response["data"] == "api_response_data"
        
        # Verify token was refreshed
        token_info = oauth_service.get_token_info()
        assert token_info["access_token"] == "new_access_token"
    
    @pytest.mark.asyncio
    async def test_api_request_unauthorized(self, mock_oauth_service):
        """Test API request with unauthorized response."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set valid token
        valid_token = TokenInfo(
            access_token="revoked_access_token",
            refresh_token="refresh_token",
            token_type="Bearer",
            expires_in=3600,
            scope="read",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        oauth_service._token = valid_token
        
        # Mock unauthorized response
        mock_response = Mock()
        mock_response.status = 401
        mock_response.json = AsyncMock(return_value={
            "error": "unauthorized",
            "message": "Token has been revoked"
        })
        
        mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response
        
        # Test API request should raise AuthenticationError
        with pytest.raises(AuthenticationError) as exc_info:
            await oauth_service.make_authenticated_request("GET", "/user/profile")
        
        assert "unauthorized" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_api_request_rate_limited(self, mock_oauth_service):
        """Test API request with rate limiting."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set valid token
        valid_token = TokenInfo(
            access_token="valid_access_token",
            refresh_token="refresh_token",
            token_type="Bearer",
            expires_in=3600,
            scope="read",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        oauth_service._token = valid_token
        
        # Mock rate limited response
        mock_response = Mock()
        mock_response.status = 429
        mock_response.headers = {"Retry-After": "60"}
        mock_response.json = AsyncMock(return_value={
            "error": "rate_limited",
            "message": "Too many requests"
        })
        
        mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response
        
        # Test API request should handle rate limiting
        with pytest.raises(Exception) as exc_info:
            await oauth_service.make_authenticated_request("GET", "/user/profile")
        
        # Should indicate rate limiting
        assert "rate" in str(exc_info.value).lower() or "429" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_token_validation_and_introspection(self, mock_oauth_service):
        """Test token validation and introspection."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set token to validate
        token_to_validate = TokenInfo(
            access_token="token_to_validate",
            refresh_token="refresh_token",
            token_type="Bearer",
            expires_in=3600,
            scope="read write",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        oauth_service._token = token_to_validate
        
        # Mock token introspection response
        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "active": True,
            "scope": "read write",
            "client_id": self.oauth_config["client_id"],
            "username": "test_user",
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        })
        
        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
        
        # Test token validation
        is_valid = await oauth_service.validate_token()
        
        assert is_valid is True
    
    @pytest.mark.asyncio
    async def test_token_validation_inactive(self, mock_oauth_service):
        """Test token validation with inactive token."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set token to validate
        token_to_validate = TokenInfo(
            access_token="inactive_token",
            refresh_token="refresh_token",
            token_type="Bearer",
            expires_in=3600,
            scope="read",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )
        oauth_service._token = token_to_validate
        
        # Mock token introspection response for inactive token
        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "active": False
        })
        
        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
        
        # Test token validation
        is_valid = await oauth_service.validate_token()
        
        assert is_valid is False
    
    def test_token_storage_and_retrieval(self, oauth_service):
        """Test token storage and retrieval mechanisms."""
        # Test storing token
        test_token = TokenInfo(
            access_token="stored_access_token",
            refresh_token="stored_refresh_token",
            token_type="Bearer",
            expires_in=7200,
            scope="read write admin",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=2)
        )
        
        oauth_service.store_token(test_token)
        
        # Test retrieving token
        retrieved_token = oauth_service.get_token_info()
        
        assert retrieved_token is not None
        assert retrieved_token["access_token"] == "stored_access_token"
        assert retrieved_token["refresh_token"] == "stored_refresh_token"
        assert retrieved_token["token_type"] == "Bearer"
        assert retrieved_token["expires_in"] == 7200
        assert retrieved_token["scope"] == "read write admin"
        
        # Test token expiry check
        assert not retrieved_token["is_expired"]
    
    def test_token_expiry_detection(self, oauth_service):
        """Test token expiry detection."""
        # Test expired token
        expired_token = TokenInfo(
            access_token="expired_token",
            refresh_token="refresh_token",
            token_type="Bearer",
            expires_in=3600,
            scope="read",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)  # Expired
        )
        
        oauth_service.store_token(expired_token)
        token_info = oauth_service.get_token_info()
        
        assert token_info["is_expired"] is True
        
        # Test valid token
        valid_token = TokenInfo(
            access_token="valid_token",
            refresh_token="refresh_token",
            token_type="Bearer",
            expires_in=3600,
            scope="read",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)  # Valid
        )
        
        oauth_service.store_token(valid_token)
        token_info = oauth_service.get_token_info()
        
        assert token_info["is_expired"] is False
    
    def test_oauth_service_stats(self, oauth_service):
        """Test OAuth service statistics tracking."""
        # Get initial stats
        stats = oauth_service.get_stats()
        
        assert "total_requests" in stats
        assert "successful_requests" in stats
        assert "failed_requests" in stats
        assert "token_refreshes" in stats
        assert "last_request_time" in stats
        assert "uptime_seconds" in stats
        
        # All counts should start at 0
        assert stats["total_requests"] == 0
        assert stats["successful_requests"] == 0
        assert stats["failed_requests"] == 0
        assert stats["token_refreshes"] == 0
    
    def test_oauth_configuration_validation(self):
        """Test OAuth configuration validation."""
        # Test missing required configuration
        incomplete_config = ConfigurationManager(auto_load=False)
        incomplete_config.load_from_dict({
            "kick": {
                "client_id": "test_id"
                # Missing client_secret, redirect_uri
            }
        })
        
        with pytest.raises(Exception):  # Should raise configuration error
            OAuthService(incomplete_config)
        
        # Test complete configuration
        complete_config = ConfigurationManager(auto_load=False)
        complete_config.load_from_dict({
            "kick": self.oauth_config
        })
        
        # Should not raise exception
        service = OAuthService(complete_config)
        assert service is not None
    
    @pytest.mark.asyncio
    async def test_concurrent_token_refresh(self, mock_oauth_service):
        """Test concurrent token refresh requests."""
        oauth_service, mock_session = mock_oauth_service
        
        # Set expired token
        expired_token = TokenInfo(
            access_token="expired_token",
            refresh_token="refresh_token_123",
            token_type="Bearer",
            expires_in=3600,
            scope="read",
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5)
        )
        oauth_service._token = expired_token
        
        # Mock successful refresh response
        mock_response = Mock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read"
        })
        
        mock_session.return_value.__aenter__.return_value.post.return_value.__aenter__.return_value = mock_response
        
        # Launch multiple concurrent refresh requests
        refresh_tasks = [oauth_service.refresh_token() for _ in range(5)]
        results = await asyncio.gather(*refresh_tasks, return_exceptions=True)
        
        # Should handle concurrent requests gracefully
        # At least one should succeed, others might be skipped due to ongoing refresh
        successful_results = [r for r in results if r is True]
        assert len(successful_results) >= 1


@pytest.mark.integration
class TestOAuthRealAPI:
    """Integration tests against real Kick.com OAuth API (when credentials available)."""
    
    @pytest.fixture(autouse=True)
    def check_real_api_credentials(self):
        """Check if real API credentials are available."""
        if not all([
            os.getenv("REAL_KICK_CLIENT_ID"),
            os.getenv("REAL_KICK_CLIENT_SECRET"),
            os.getenv("REAL_KICK_REDIRECT_URI")
        ]):
            pytest.skip("Real OAuth credentials required for real API tests")
        
        self.real_oauth_config = {
            "client_id": os.getenv("REAL_KICK_CLIENT_ID"),
            "client_secret": os.getenv("REAL_KICK_CLIENT_SECRET"),
            "redirect_uri": os.getenv("REAL_KICK_REDIRECT_URI"),
            "base_url": "https://kick.com",
            "api_base_url": "https://kick.com/api"
        }
    
    @pytest.fixture
    async def real_oauth_service(self):
        """Create OAuth service for real API testing."""
        config_manager = ConfigurationManager(auto_load=False)
        config_manager.load_from_dict({
            "kick": self.real_oauth_config
        })
        
        service = OAuthService(config_manager)
        yield service
        await service.close()
    
    def test_real_authorization_url_accessibility(self, real_oauth_service):
        """Test that authorization URL is accessible."""
        auth_url = real_oauth_service.get_authorization_url()
        
        # URL should be properly formatted and accessible
        assert auth_url.startswith("https://")
        assert "kick.com" in auth_url
        
        # Note: We don't actually test HTTP access here to avoid hitting the API
        # This would require network connectivity testing
    
    @pytest.mark.skip(reason="Requires manual authorization flow")
    async def test_real_token_exchange_flow(self, real_oauth_service):
        """
        Test real token exchange flow.
        
        This test is skipped by default as it requires manual intervention
        to complete the OAuth flow and obtain an authorization code.
        """
        # This would require:
        # 1. User to visit authorization URL
        # 2. Complete OAuth flow
        # 3. Provide authorization code
        # 4. Test token exchange
        
        # Example implementation:
        # auth_code = input("Enter authorization code from OAuth flow: ")
        # result = await real_oauth_service.exchange_code_for_token(auth_code)
        # assert result is True
        
        pytest.skip("Manual OAuth flow required")