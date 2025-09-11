"""
Integration tests for OAuth authentication flow against live Kick.com API.
Tests the complete authentication workflow including token management and refresh.

These tests require:
- Valid Kick.com API credentials (ClientID, Client Secret)
- Network access to Kick.com OAuth endpoints
- Proper environment configuration

Set these environment variables:
- KICK_CLIENT_ID
- KICK_CLIENT_SECRET
- KICK_REDIRECT_URI (for authorization code flow testing)

These tests validate:
- OAuth 2.1 client credentials flow
- Token validation and usage
- Token refresh mechanisms
- Error handling and recovery
- API integration with authentication
"""

import os
import pytest
import httpx
import asyncio
import json
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
import secrets
import base64
import hashlib


class TestOAuthAuthenticationIntegration:
    """Integration tests for OAuth authentication flow."""

    @pytest.fixture(autouse=True)
    def setup_oauth_config(self):
        """Setup OAuth configuration from environment."""
        self.client_id = os.getenv("KICK_CLIENT_ID")
        self.client_secret = os.getenv("KICK_CLIENT_SECRET")
        self.redirect_uri = os.getenv("KICK_REDIRECT_URI", "http://localhost:8080/callback")
        
        if not self.client_id or not self.client_secret:
            pytest.skip("KICK_CLIENT_ID and KICK_CLIENT_SECRET required for OAuth integration tests")
        
        # OAuth endpoints
        self.oauth_base_url = "https://id.kick.com"
        self.token_endpoint = f"{self.oauth_base_url}/oauth/token"
        self.authorize_endpoint = f"{self.oauth_base_url}/oauth/authorize"
        self.revoke_endpoint = f"{self.oauth_base_url}/oauth/revoke"
        
        # API endpoints for testing authenticated requests
        self.api_base_url = "https://kick.com/api/v1"
        
        # Required scopes for monitoring
        self.required_scopes = ["user:read", "channel:read", "events:subscribe"]
        
        # Store tokens for cleanup
        self.active_tokens = []

    @pytest.fixture
    def http_client(self):
        """HTTP client for OAuth requests."""
        return httpx.Client(timeout=30.0)

    @pytest.fixture
    async def async_http_client(self):
        """Async HTTP client for OAuth requests."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            yield client

    def teardown_method(self):
        """Clean up tokens after each test."""
        # Revoke any active tokens
        for token in self.active_tokens:
            try:
                self._revoke_token(token)
            except Exception:
                pass  # Ignore cleanup errors
        self.active_tokens.clear()

    def test_client_credentials_flow_integration(self, http_client):
        """Test complete client credentials OAuth flow."""
        # Step 1: Request access token
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": " ".join(self.required_scopes)
        }
        
        response = http_client.post(
            self.token_endpoint,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert response.status_code == 200, f"Token request failed: {response.status_code} - {response.text}"
        
        token_response = response.json()
        access_token = token_response["access_token"]
        token_type = token_response["token_type"]
        
        assert access_token is not None
        assert token_type.lower() == "bearer"
        
        # Store for cleanup
        self.active_tokens.append(access_token)
        
        # Step 2: Test authenticated API request
        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "kick-monitor-integration-test/1.0.0"
        }
        
        # Test API access with token
        api_response = http_client.get(
            f"{self.api_base_url}/channels/xqc",  # Known public channel
            headers=auth_headers
        )
        
        # Should be able to access API with valid token
        assert api_response.status_code in [200, 404], f"API request failed: {api_response.status_code}"
        
        # Step 3: Validate token properties
        if "expires_in" in token_response:
            expires_in = token_response["expires_in"]
            assert isinstance(expires_in, int)
            assert expires_in > 0
            
            # Token should be valid for reasonable duration
            assert expires_in >= 3600, "Token expires too quickly"

    def test_token_validation_and_usage(self, http_client):
        """Test token validation and API usage patterns."""
        # Get access token
        access_token = self._get_access_token(http_client)
        
        # Test multiple API requests with same token
        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "kick-monitor-integration-test/1.0.0"
        }
        
        # Test different API endpoints
        test_endpoints = [
            "/channels/xqc",
            "/channels/trainwreckstv",
        ]
        
        for endpoint in test_endpoints:
            response = http_client.get(
                f"{self.api_base_url}{endpoint}",
                headers=auth_headers
            )
            
            # Token should work for multiple requests
            assert response.status_code in [200, 404, 429], f"Token failed for {endpoint}: {response.status_code}"
            
            # Check for rate limiting
            if response.status_code == 429:
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    print(f"Rate limited, retry after: {retry_after}s")
                break

    def test_invalid_token_handling(self, http_client):
        """Test handling of invalid or expired tokens."""
        # Test with completely invalid token
        invalid_token = "invalid_token_12345"
        
        auth_headers = {
            "Authorization": f"Bearer {invalid_token}",
            "User-Agent": "kick-monitor-integration-test/1.0.0"
        }
        
        response = http_client.get(
            f"{self.api_base_url}/channels/xqc",
            headers=auth_headers
        )
        
        # Should return 401 for invalid token
        assert response.status_code == 401, f"Expected 401 for invalid token, got {response.status_code}"

    def test_token_refresh_simulation(self, http_client):
        """Test token refresh workflow simulation."""
        # Note: Client credentials flow doesn't typically provide refresh tokens
        # This test documents the refresh process for authorization code flow
        
        # Get initial token
        token_response = self._get_token_response(http_client)
        
        if "refresh_token" in token_response:
            # Test refresh token usage
            refresh_data = {
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": token_response["refresh_token"]
            }
            
            refresh_response = http_client.post(
                self.token_endpoint,
                data=refresh_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            
            if refresh_response.status_code == 200:
                new_token_response = refresh_response.json()
                assert "access_token" in new_token_response
                assert new_token_response["access_token"] != token_response["access_token"]
        else:
            # Document refresh token workflow for authorization code flow
            print("Refresh token workflow (for authorization code flow):")
            print("1. Store refresh_token from initial authorization")
            print("2. When access_token expires, use refresh_token to get new access_token")
            print("3. Update stored tokens and continue API operations")

    @pytest.mark.asyncio
    async def test_concurrent_token_usage(self, async_http_client):
        """Test concurrent API requests with same token."""
        # Get access token
        access_token = await self._get_access_token_async(async_http_client)
        
        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "kick-monitor-integration-test/1.0.0"
        }
        
        # Define concurrent API requests
        async def make_api_request(endpoint: str, request_id: int):
            """Make API request with authentication."""
            try:
                response = await async_http_client.get(
                    f"{self.api_base_url}{endpoint}",
                    headers=auth_headers
                )
                return {
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "success": response.status_code in [200, 404]
                }
            except Exception as e:
                return {
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "error": str(e),
                    "success": False
                }
        
        # Make concurrent requests
        test_requests = [
            ("/channels/xqc", 1),
            ("/channels/trainwreckstv", 2),
            ("/channels/amouranth", 3),
        ]
        
        tasks = [make_api_request(endpoint, req_id) for endpoint, req_id in test_requests]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Analyze results
        successful_requests = [r for r in results if isinstance(r, dict) and r.get("success")]
        
        # At least some requests should succeed (rate limiting may affect others)
        assert len(successful_requests) > 0, "No concurrent requests succeeded"
        
        print(f"Concurrent requests: {len(successful_requests)}/{len(results)} succeeded")

    def test_oauth_error_scenarios(self, http_client):
        """Test OAuth error handling scenarios."""
        # Test invalid client ID
        invalid_client_data = {
            "grant_type": "client_credentials",
            "client_id": "invalid_client_id_12345",
            "client_secret": self.client_secret,
            "scope": " ".join(self.required_scopes)
        }
        
        response = http_client.post(
            self.token_endpoint,
            data=invalid_client_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert response.status_code == 401
        error_response = response.json()
        assert "error" in error_response
        
        # Test invalid client secret
        invalid_secret_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": "invalid_secret_12345",
            "scope": " ".join(self.required_scopes)
        }
        
        response = http_client.post(
            self.token_endpoint,
            data=invalid_secret_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert response.status_code == 401
        
        # Test malformed request
        malformed_data = {
            "grant_type": "invalid_grant",
            "client_id": self.client_id
        }
        
        response = http_client.post(
            self.token_endpoint,
            data=malformed_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert response.status_code == 400

    def test_scope_validation_integration(self, http_client):
        """Test scope validation and access control."""
        # Test with minimal scopes
        minimal_token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "user:read"  # Only one scope
        }
        
        response = http_client.post(
            self.token_endpoint,
            data=minimal_token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code == 200:
            token_response = response.json()
            limited_token = token_response["access_token"]
            
            # Test API access with limited scope
            auth_headers = {
                "Authorization": f"Bearer {limited_token}",
                "User-Agent": "kick-monitor-integration-test/1.0.0"
            }
            
            # Should still be able to access basic channel info
            api_response = http_client.get(
                f"{self.api_base_url}/channels/xqc",
                headers=auth_headers
            )
            
            # Document scope behavior
            print(f"Limited scope API access: {api_response.status_code}")
            
            # Store for cleanup
            self.active_tokens.append(limited_token)

    def test_authentication_workflow_integration(self, http_client):
        """Test complete authentication workflow for monitoring service."""
        # Simulate the authentication workflow that the monitoring service would use
        
        # Step 1: Service startup - get initial token
        print("Step 1: Service startup authentication")
        access_token = self._get_access_token(http_client)
        assert access_token is not None
        
        # Step 2: Validate token works for required API calls
        print("Step 2: Validate API access")
        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "kick-monitor/1.0.0"
        }
        
        # Test channel access (required for monitoring)
        channel_response = http_client.get(
            f"{self.api_base_url}/channels/xqc",
            headers=auth_headers
        )
        
        assert channel_response.status_code in [200, 404], "Channel API access failed"
        
        # Step 3: Simulate token usage over time
        print("Step 3: Simulated long-running usage")
        
        # Make multiple requests to simulate monitoring activity
        for i in range(3):
            response = http_client.get(
                f"{self.api_base_url}/channels/xqc",
                headers=auth_headers
            )
            
            if response.status_code == 401:
                # Token expired - would need refresh in real implementation
                print("Token expired, would trigger refresh in production")
                break
            elif response.status_code == 429:
                # Rate limited - would need backoff in real implementation
                print("Rate limited, would implement backoff in production")
                break
            
            # Small delay between requests
            time.sleep(0.5)
        
        print("Authentication workflow test completed")

    def _get_access_token(self, http_client: httpx.Client) -> str:
        """Get access token for testing."""
        token_response = self._get_token_response(http_client)
        access_token = token_response["access_token"]
        self.active_tokens.append(access_token)
        return access_token

    def _get_token_response(self, http_client: httpx.Client) -> Dict[str, Any]:
        """Get full token response for testing."""
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": " ".join(self.required_scopes)
        }
        
        response = http_client.post(
            self.token_endpoint,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert response.status_code == 200, f"Token request failed: {response.text}"
        return response.json()

    async def _get_access_token_async(self, async_client: httpx.AsyncClient) -> str:
        """Get access token asynchronously."""
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": " ".join(self.required_scopes)
        }
        
        response = await async_client.post(
            self.token_endpoint,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert response.status_code == 200, f"Async token request failed: {response.text}"
        token_response = response.json()
        access_token = token_response["access_token"]
        self.active_tokens.append(access_token)
        return access_token

    def _revoke_token(self, access_token: str):
        """Revoke access token for cleanup."""
        try:
            with httpx.Client(timeout=10.0) as client:
                revoke_data = {
                    "token": access_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                }
                
                client.post(
                    self.revoke_endpoint,
                    data=revoke_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"}
                )
        except Exception:
            # Ignore revocation errors during cleanup
            pass


@pytest.mark.integration
class TestAuthenticationFlowDocumentation:
    """Documentation tests for authentication flows."""
    
    def test_authorization_code_flow_documentation(self):
        """Document authorization code flow for future implementation."""
        # This documents the full OAuth 2.1 authorization code flow
        # that would be needed for user-specific permissions
        
        flow_steps = [
            "1. Generate PKCE code_verifier and code_challenge",
            "2. Redirect user to authorization URL with state and PKCE challenge",
            "3. User grants permission and is redirected to callback with code",
            "4. Exchange authorization code for access_token using PKCE verifier",
            "5. Store access_token and refresh_token securely",
            "6. Use access_token for API requests",
            "7. Refresh access_token when it expires using refresh_token",
            "8. Handle refresh_token rotation if implemented"
        ]
        
        print("OAuth 2.1 Authorization Code Flow with PKCE:")
        for step in flow_steps:
            print(f"   {step}")
        
        # PKCE example
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode('utf-8')).digest()
        ).decode('utf-8').rstrip('=')
        
        print(f"\nPKCE Example:")
        print(f"   code_verifier: {code_verifier[:20]}...")
        print(f"   code_challenge: {code_challenge[:20]}...")
        
        assert True

    def test_token_management_best_practices(self):
        """Document token management best practices."""
        best_practices = {
            "Storage": [
                "Store tokens securely (encrypted at rest)",
                "Never log tokens in plaintext",
                "Use secure memory for token handling",
                "Implement token rotation"
            ],
            "Usage": [
                "Include User-Agent header in API requests",
                "Implement exponential backoff for rate limiting",
                "Handle 401 responses with token refresh",
                "Monitor token expiration proactively"
            ],
            "Security": [
                "Use HTTPS for all OAuth communications",
                "Validate redirect URIs strictly",
                "Implement CSRF protection with state parameter",
                "Use PKCE for authorization code flow"
            ],
            "Error Handling": [
                "Retry failed token requests with backoff",
                "Fall back to token refresh on 401 errors",
                "Log authentication failures for monitoring",
                "Implement circuit breaker for repeated failures"
            ]
        }
        
        print("Token Management Best Practices:")
        for category, practices in best_practices.items():
            print(f"  {category}:")
            for practice in practices:
                print(f"    - {practice}")
        
        assert True

    def test_monitoring_service_auth_requirements(self):
        """Document authentication requirements for monitoring service."""
        auth_requirements = {
            "Required Scopes": [
                "user:read - Access basic user information",
                "channel:read - Access channel information and status",
                "events:subscribe - Subscribe to real-time events (if available)"
            ],
            "Token Lifecycle": [
                "Obtain token on service startup",
                "Refresh token before expiration",
                "Handle token refresh failures gracefully",
                "Revoke tokens on service shutdown"
            ],
            "Rate Limiting": [
                "Respect API rate limits",
                "Implement exponential backoff",
                "Monitor rate limit headers",
                "Use efficient polling strategies"
            ],
            "Error Recovery": [
                "Automatic retry on network failures",
                "Token refresh on authentication errors",
                "Graceful degradation on API unavailability",
                "Alert on persistent authentication failures"
            ]
        }
        
        print("Monitoring Service Authentication Requirements:")
        for category, requirements in auth_requirements.items():
            print(f"  {category}:")
            for req in requirements:
                print(f"    - {req}")
        
        assert True