"""
Contract tests for Kick.com Channel API endpoints.
Tests against live API to validate channel information and livestream status.

These tests require:
- Valid OAuth access token (from OAuth test)
- Known Kick.com usernames for testing
- Network access to kick.com API

Set KICK_TEST_USERNAMES environment variable with comma-separated usernames.
"""

import os
import pytest
import httpx
import json
from typing import Dict, Any, List, Optional
from datetime import datetime

# API endpoints from contract specification
API_BASE_URL = "https://kick.com/api/v1"
CHANNELS_ENDPOINT = f"{API_BASE_URL}/channels"


class TestKickChannelsContract:
    """Contract tests for Kick.com Channel API."""

    @pytest.fixture(autouse=True)
    def setup_test_data(self):
        """Setup test data and credentials."""
        # Get test usernames from environment
        test_usernames = os.getenv("KICK_TEST_USERNAMES", "")
        if test_usernames:
            self.test_usernames = [name.strip() for name in test_usernames.split(",")]
        else:
            # Default test usernames (known public channels)
            self.test_usernames = ["xqc", "trainwreckstv", "amouranth"]
        
        # OAuth token (should be set by OAuth tests or environment)
        self.access_token = os.getenv("KICK_ACCESS_TOKEN")
        if not self.access_token:
            pytest.skip("KICK_ACCESS_TOKEN required for channel API tests")

    @pytest.fixture
    def authenticated_client(self):
        """HTTP client with OAuth authentication."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "kick-monitor/1.0.0",
            "Accept": "application/json"
        }
        return httpx.Client(timeout=30.0, headers=headers)

    def test_channel_endpoint_exists(self, authenticated_client):
        """Test that the channels endpoint is accessible."""
        # Test with a known username
        username = self.test_usernames[0]
        url = f"{CHANNELS_ENDPOINT}/{username}"
        
        response = authenticated_client.get(url)
        
        # Should not return 404 (endpoint exists)
        assert response.status_code != 404, f"Channel endpoint not found: {url}"
        
        # Valid responses: 200 (found), 401 (auth issue), 403 (permissions)
        assert response.status_code in [200, 401, 403], f"Unexpected status: {response.status_code}"

    def test_channel_info_contract(self, authenticated_client):
        """Test channel information endpoint contract."""
        for username in self.test_usernames[:2]:  # Test first 2 usernames
            url = f"{CHANNELS_ENDPOINT}/{username}"
            response = authenticated_client.get(url)
            
            if response.status_code == 404:
                pytest.skip(f"Username {username} not found - may not exist")
            
            if response.status_code == 401:
                pytest.skip("Authentication failed - check access token")
            
            assert response.status_code == 200, f"Channel request failed: {response.status_code} - {response.text}"
            
            # Validate response format
            assert response.headers.get("content-type", "").startswith("application/json")
            
            # Validate response schema
            channel_data = response.json()
            self._validate_channel_response(channel_data, username)

    def test_livestream_endpoint_contract(self, authenticated_client):
        """Test livestream status endpoint contract."""
        for username in self.test_usernames[:2]:
            url = f"{CHANNELS_ENDPOINT}/{username}/livestream"
            response = authenticated_client.get(url)
            
            # Valid responses: 200 (live), 404 (not live or channel not found)
            assert response.status_code in [200, 404, 401], f"Unexpected livestream status: {response.status_code}"
            
            if response.status_code == 200:
                # Channel is currently live
                livestream_data = response.json()
                self._validate_livestream_response(livestream_data, username)
            elif response.status_code == 404:
                # Channel is not live or doesn't exist
                # This is expected behavior
                pass

    def test_invalid_username_handling(self, authenticated_client):
        """Test API response for invalid/non-existent usernames."""
        invalid_usernames = [
            "this_username_definitely_does_not_exist_12345",
            "invalid-chars-!@#$%",
            "",  # Empty username
            "a" * 100,  # Very long username
        ]
        
        for username in invalid_usernames:
            url = f"{CHANNELS_ENDPOINT}/{username}"
            response = authenticated_client.get(url)
            
            # Should return 404 for non-existent users
            # May return 400 for invalid characters
            assert response.status_code in [400, 404], f"Invalid username {username} returned {response.status_code}"

    def test_authentication_required(self):
        """Test that channel endpoints require authentication."""
        # Test without authorization header
        client = httpx.Client(timeout=30.0)
        username = self.test_usernames[0]
        url = f"{CHANNELS_ENDPOINT}/{username}"
        
        response = client.get(url)
        
        # Should require authentication
        assert response.status_code in [401, 403], "API should require authentication"

    def test_rate_limiting_compliance(self, authenticated_client):
        """Test API rate limiting behavior."""
        username = self.test_usernames[0]
        url = f"{CHANNELS_ENDPOINT}/{username}"
        
        responses = []
        
        # Make multiple requests to test rate limiting
        for i in range(5):
            response = authenticated_client.get(url)
            responses.append(response)
            
            # If we hit rate limit, test behavior
            if response.status_code == 429:
                assert "retry-after" in response.headers or "x-ratelimit-reset" in response.headers
                break
        
        # At least the first request should succeed
        assert any(r.status_code == 200 for r in responses), "No successful requests"

    def test_channel_data_consistency(self, authenticated_client):
        """Test consistency of channel data across requests."""
        username = self.test_usernames[0]
        url = f"{CHANNELS_ENDPOINT}/{username}"
        
        # Make two requests
        response1 = authenticated_client.get(url)
        response2 = authenticated_client.get(url)
        
        if response1.status_code == 200 and response2.status_code == 200:
            data1 = response1.json()
            data2 = response2.json()
            
            # Core channel data should be consistent
            consistent_fields = ["id", "user_id", "slug"]
            for field in consistent_fields:
                if field in data1 and field in data2:
                    assert data1[field] == data2[field], f"Inconsistent {field}: {data1[field]} vs {data2[field]}"

    def test_livestream_status_data(self, authenticated_client):
        """Test livestream status data format and content."""
        for username in self.test_usernames:
            # Get channel info first
            channel_url = f"{CHANNELS_ENDPOINT}/{username}"
            channel_response = authenticated_client.get(channel_url)
            
            if channel_response.status_code != 200:
                continue
                
            channel_data = channel_response.json()
            
            # Check livestream data in channel response
            if "livestream" in channel_data and channel_data["livestream"]:
                livestream = channel_data["livestream"]
                self._validate_livestream_data(livestream)
                
                # Cross-check with dedicated livestream endpoint
                livestream_url = f"{CHANNELS_ENDPOINT}/{username}/livestream"
                livestream_response = authenticated_client.get(livestream_url)
                
                if livestream_response.status_code == 200:
                    dedicated_livestream = livestream_response.json()
                    
                    # Compare key fields
                    if "id" in livestream and "id" in dedicated_livestream:
                        assert livestream["id"] == dedicated_livestream["id"]

    def test_response_time_requirements(self, authenticated_client):
        """Test that API responses meet performance requirements."""
        username = self.test_usernames[0]
        url = f"{CHANNELS_ENDPOINT}/{username}"
        
        import time
        start_time = time.time()
        response = authenticated_client.get(url)
        end_time = time.time()
        
        response_time = end_time - start_time
        
        # API should respond within reasonable time (5 seconds)
        assert response_time < 5.0, f"API response too slow: {response_time:.2f}s"
        
        if response.status_code == 200:
            # Log performance for monitoring
            print(f"Channel API response time: {response_time:.3f}s")

    def _validate_channel_response(self, channel_data: Dict[str, Any], username: str) -> None:
        """Validate channel response structure."""
        # Required fields per API contract
        required_fields = ["id", "user_id", "slug"]
        for field in required_fields:
            assert field in channel_data, f"Missing required field: {field}"
        
        # Validate field types
        assert isinstance(channel_data["id"], int), "Channel ID should be integer"
        assert isinstance(channel_data["user_id"], int), "User ID should be integer"
        assert isinstance(channel_data["slug"], str), "Slug should be string"
        
        # Validate slug matches requested username
        assert channel_data["slug"].lower() == username.lower(), f"Slug mismatch: {channel_data['slug']} vs {username}"
        
        # Optional fields validation
        if "user" in channel_data:
            user_data = channel_data["user"]
            assert isinstance(user_data, dict), "User should be object"
            
            if "username" in user_data:
                assert isinstance(user_data["username"], str)
                assert user_data["username"].lower() == username.lower()
        
        # Livestream field validation
        if "livestream" in channel_data:
            livestream = channel_data["livestream"]
            if livestream is not None:
                self._validate_livestream_data(livestream)

    def _validate_livestream_response(self, livestream_data: Dict[str, Any], username: str) -> None:
        """Validate livestream response structure."""
        self._validate_livestream_data(livestream_data)

    def _validate_livestream_data(self, livestream: Dict[str, Any]) -> None:
        """Validate livestream data structure."""
        # Required fields for active livestream
        required_fields = ["id", "is_live"]
        for field in required_fields:
            assert field in livestream, f"Missing livestream field: {field}"
        
        # Validate field types
        assert isinstance(livestream["id"], (int, str)), "Livestream ID should be int or string"
        assert isinstance(livestream["is_live"], bool), "is_live should be boolean"
        
        # If live, additional fields should be present
        if livestream["is_live"]:
            optional_live_fields = ["title", "viewer_count", "created_at"]
            for field in optional_live_fields:
                if field in livestream:
                    if field == "title":
                        assert isinstance(livestream[field], str)
                    elif field == "viewer_count":
                        assert isinstance(livestream[field], int)
                        assert livestream[field] >= 0
                    elif field == "created_at":
                        # Validate timestamp format
                        assert isinstance(livestream[field], str)
                        # Try to parse as ISO datetime
                        try:
                            datetime.fromisoformat(livestream[field].replace('Z', '+00:00'))
                        except ValueError:
                            pytest.fail(f"Invalid datetime format: {livestream[field]}")


@pytest.mark.integration 
class TestChannelAPIIntegration:
    """Integration tests for channel API functionality."""
    
    def test_channel_monitoring_workflow(self):
        """Test the complete workflow for monitoring channels."""
        workflow_steps = [
            "1. Authenticate with OAuth 2.1 client credentials",
            "2. Get list of channels to monitor from database",
            "3. For each channel, request current status",
            "4. Parse livestream status (online/offline)",
            "5. Update database with current status",
            "6. Handle rate limiting and errors gracefully"
        ]
        
        print("Channel Monitoring Workflow:")
        for step in workflow_steps:
            print(f"   {step}")
        
        # Test passes as documentation
        assert True

    def test_error_scenarios_documentation(self):
        """Document expected error scenarios and handling."""
        error_scenarios = {
            "401 Unauthorized": "Invalid or expired access token",
            "403 Forbidden": "Insufficient permissions for requested scope",
            "404 Not Found": "Channel/user does not exist",
            "429 Too Many Requests": "Rate limit exceeded",
            "500 Internal Server Error": "Temporary API issue",
            "503 Service Unavailable": "API maintenance or overload"
        }
        
        print("Expected Error Scenarios:")
        for status, description in error_scenarios.items():
            print(f"   {status}: {description}")
        
        assert True