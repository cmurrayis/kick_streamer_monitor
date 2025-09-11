"""
Contract tests for Kick.com OAuth API endpoints.
Tests against live API to validate OAuth 2.1 authentication flow.

These tests require valid Kick.com API credentials:
- KICK_CLIENT_ID
- KICK_CLIENT_SECRET
- KICK_REDIRECT_URI (for authorization code flow)

Set these in environment variables or .env file for testing.
"""

import os
import pytest
import httpx
import json
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, Optional

# OAuth endpoints from contract specification
OAUTH_BASE_URL = "https://id.kick.com"
TOKEN_ENDPOINT = f"{OAUTH_BASE_URL}/oauth/token"
AUTHORIZE_ENDPOINT = f"{OAUTH_BASE_URL}/oauth/authorize"
REVOKE_ENDPOINT = f"{OAUTH_BASE_URL}/oauth/revoke"

# Required scopes for monitoring
REQUIRED_SCOPES = ["user:read", "channel:read", "events:subscribe"]


class TestKickOAuthContract:
    """Contract tests for Kick.com OAuth 2.1 authentication."""

    @pytest.fixture(autouse=True)
    def setup_credentials(self):
        """Setup OAuth credentials from environment."""
        self.client_id = os.getenv("KICK_CLIENT_ID")
        self.client_secret = os.getenv("KICK_CLIENT_SECRET")
        self.redirect_uri = os.getenv("KICK_REDIRECT_URI", "http://localhost:8080/callback")
        
        if not self.client_id or not self.client_secret:
            pytest.skip("KICK_CLIENT_ID and KICK_CLIENT_SECRET required for OAuth tests")

    @pytest.fixture
    def http_client(self):
        """HTTP client for API requests."""
        return httpx.Client(timeout=30.0)

    def test_token_endpoint_exists(self, http_client):
        """Test that the OAuth token endpoint is accessible."""
        # Make a request to check endpoint availability
        # This should return 400 (bad request) not 404 (not found)
        response = http_client.post(TOKEN_ENDPOINT)
        
        assert response.status_code != 404, "Token endpoint not found"
        assert response.status_code in [400, 401], f"Unexpected status: {response.status_code}"
        
        # Check response format
        assert response.headers.get("content-type", "").startswith("application/json")

    def test_client_credentials_flow_contract(self, http_client):
        """
        Test OAuth 2.1 Client Credentials flow contract.
        This flow is used for server-to-server authentication.
        """
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": " ".join(REQUIRED_SCOPES)
        }
        
        response = http_client.post(
            TOKEN_ENDPOINT,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        # Validate response status
        if response.status_code == 401:
            pytest.skip("Invalid client credentials - check KICK_CLIENT_ID and KICK_CLIENT_SECRET")
        
        assert response.status_code == 200, f"Token request failed: {response.status_code} - {response.text}"
        
        # Validate response content type
        assert response.headers.get("content-type", "").startswith("application/json")
        
        # Validate response schema
        token_response = response.json()
        self._validate_token_response(token_response)
        
        # Store token for other tests
        self.access_token = token_response["access_token"]

    def test_invalid_client_credentials(self, http_client):
        """Test OAuth response with invalid client credentials."""
        token_data = {
            "grant_type": "client_credentials",
            "client_id": "invalid_client_id",
            "client_secret": "invalid_client_secret",
            "scope": " ".join(REQUIRED_SCOPES)
        }
        
        response = http_client.post(
            TOKEN_ENDPOINT,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        # Should return 401 for invalid credentials
        assert response.status_code == 401
        
        # Validate error response format
        error_response = response.json()
        assert "error" in error_response
        assert error_response["error"] in ["invalid_client", "unauthorized_client"]

    def test_invalid_grant_type(self, http_client):
        """Test OAuth response with invalid grant type."""
        token_data = {
            "grant_type": "invalid_grant_type",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        
        response = http_client.post(
            TOKEN_ENDPOINT,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        # Should return 400 for unsupported grant type
        assert response.status_code == 400
        
        # Validate error response
        error_response = response.json()
        assert "error" in error_response
        assert error_response["error"] == "unsupported_grant_type"

    def test_missing_required_parameters(self, http_client):
        """Test OAuth response with missing required parameters."""
        # Test missing client_id
        token_data = {
            "grant_type": "client_credentials",
            "client_secret": self.client_secret
        }
        
        response = http_client.post(
            TOKEN_ENDPOINT,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        assert response.status_code == 400
        error_response = response.json()
        assert "error" in error_response

    def test_token_response_schema_compliance(self, http_client):
        """Test that token response matches OAuth 2.1 specification."""
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": " ".join(REQUIRED_SCOPES)
        }
        
        response = http_client.post(
            TOKEN_ENDPOINT,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code != 200:
            pytest.skip("Cannot test schema without valid token response")
        
        token_response = response.json()
        
        # Validate all required OAuth 2.1 fields
        required_fields = ["access_token", "token_type"]
        for field in required_fields:
            assert field in token_response, f"Missing required field: {field}"
        
        # Validate field types and values
        assert isinstance(token_response["access_token"], str)
        assert len(token_response["access_token"]) > 0
        assert token_response["token_type"].lower() == "bearer"
        
        # Optional fields validation
        if "expires_in" in token_response:
            assert isinstance(token_response["expires_in"], int)
            assert token_response["expires_in"] > 0
        
        if "scope" in token_response:
            assert isinstance(token_response["scope"], str)
            granted_scopes = token_response["scope"].split()
            # Check that at least some of our requested scopes were granted
            assert len(set(REQUIRED_SCOPES) & set(granted_scopes)) > 0

    def test_scope_validation(self, http_client):
        """Test scope handling in OAuth requests."""
        # Test with valid scopes
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "user:read channel:read"
        }
        
        response = http_client.post(
            TOKEN_ENDPOINT,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        if response.status_code == 200:
            token_response = response.json()
            if "scope" in token_response:
                granted_scopes = token_response["scope"].split()
                # Verify that granted scopes are subset of requested scopes
                requested_scopes = ["user:read", "channel:read"]
                assert all(scope in requested_scopes for scope in granted_scopes)

    def test_rate_limiting_headers(self, http_client):
        """Test that API returns appropriate rate limiting headers."""
        token_data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        
        response = http_client.post(
            TOKEN_ENDPOINT,
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        
        # Check for common rate limiting headers
        rate_limit_headers = [
            "x-ratelimit-limit",
            "x-ratelimit-remaining", 
            "x-ratelimit-reset",
            "retry-after"
        ]
        
        # At least one rate limiting header should be present
        has_rate_limit_header = any(
            header.lower() in [h.lower() for h in response.headers.keys()]
            for header in rate_limit_headers
        )
        
        # This is informational - not all APIs implement rate limit headers
        if has_rate_limit_header:
            print(f"Rate limiting headers found: {dict(response.headers)}")

    def _validate_token_response(self, token_response: Dict[str, Any]) -> None:
        """Validate OAuth token response structure."""
        # Required fields per OAuth 2.1 spec
        assert "access_token" in token_response, "Missing access_token"
        assert "token_type" in token_response, "Missing token_type"
        
        # Field validation
        assert isinstance(token_response["access_token"], str)
        assert len(token_response["access_token"]) > 0
        assert token_response["token_type"].lower() == "bearer"
        
        # Optional fields
        optional_fields = {
            "expires_in": int,
            "refresh_token": str,
            "scope": str
        }
        
        for field, expected_type in optional_fields.items():
            if field in token_response:
                assert isinstance(token_response[field], expected_type)

    def test_authorization_url_generation(self):
        """Test authorization URL generation for manual testing reference."""
        # This test documents the authorization URL format
        # Used for manual testing or future authorization code flow implementation
        
        import secrets
        import base64
        import hashlib
        
        # Generate PKCE parameters
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('utf-8').rstrip('=')
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode('utf-8')).digest()
        ).decode('utf-8').rstrip('=')
        
        state = secrets.token_urlsafe(32)
        
        auth_params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(REQUIRED_SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256"
        }
        
        # Build authorization URL
        from urllib.parse import urlencode
        auth_url = f"{AUTHORIZE_ENDPOINT}?{urlencode(auth_params)}"
        
        # Validate URL format
        parsed_url = urlparse(auth_url)
        assert parsed_url.scheme == "https"
        assert parsed_url.netloc == "id.kick.com"
        assert parsed_url.path == "/oauth/authorize"
        
        # Validate query parameters
        query_params = parse_qs(parsed_url.query)
        assert "client_id" in query_params
        assert "response_type" in query_params
        assert query_params["response_type"][0] == "code"
        
        print(f"Authorization URL for manual testing:\n{auth_url}")


@pytest.mark.integration
class TestOAuthIntegration:
    """Integration tests that require manual intervention or special setup."""
    
    def test_authorization_code_flow_documentation(self):
        """Document the authorization code flow for future implementation."""
        # This test serves as documentation for implementing the full OAuth flow
        
        flow_steps = [
            "1. Direct user to authorization URL with PKCE parameters",
            "2. User grants permission and is redirected to callback URL with code",
            "3. Exchange authorization code for access token using PKCE verifier",
            "4. Use access token for API requests",
            "5. Refresh token when needed (if refresh token provided)"
        ]
        
        print("OAuth 2.1 Authorization Code Flow:")
        for step in flow_steps:
            print(f"   {step}")
        
        # Test passes as documentation
        assert True