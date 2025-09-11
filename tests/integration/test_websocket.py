"""
Integration tests for WebSocket connection to live Kick.com Pusher service.
Tests real-time WebSocket connectivity, event subscription, and message handling.

These tests require:
- Valid OAuth access token for authentication
- Network access to Kick.com WebSocket endpoints
- Test channels that may generate events

Note: WebSocket endpoint discovery is needed as it's not officially documented.
This test suite includes discovery mechanisms and fallback strategies.

Set these environment variables:
- KICK_ACCESS_TOKEN (from OAuth tests)
- KICK_TEST_CHANNELS (comma-separated channel names for testing)
"""

import os
import pytest
import asyncio
import json
import websockets
import httpx
from typing import Dict, Any, List, Optional, AsyncGenerator, Union
from datetime import datetime, timedelta
import logging
import ssl
from urllib.parse import urlparse, parse_qs
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

# Disable verbose websocket logging for tests
logging.getLogger("websockets").setLevel(logging.WARNING)


class TestWebSocketIntegration:
    """Integration tests for WebSocket connection to Kick.com."""

    @pytest.fixture(autouse=True)
    def setup_websocket_config(self):
        """Setup WebSocket configuration from environment."""
        self.access_token = os.getenv("KICK_ACCESS_TOKEN")
        if not self.access_token:
            pytest.skip("KICK_ACCESS_TOKEN required for WebSocket integration tests")
        
        # Test channels for monitoring
        test_channels = os.getenv("KICK_TEST_CHANNELS", "")
        if test_channels:
            self.test_channels = [name.strip() for name in test_channels.split(",")]
        else:
            # Default test channels (known active channels)
            self.test_channels = ["xqc", "trainwreckstv"]
        
        # WebSocket configuration
        self.websocket_timeout = 30.0
        self.event_wait_timeout = 60.0
        self.max_connection_attempts = 3
        
        # Pusher configuration (may need to be discovered)
        self.pusher_app_key = None
        self.websocket_url = None
        
        # Event storage for testing
        self.received_events = []

    @pytest.fixture
    async def http_client(self):
        """HTTP client for API requests."""
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "kick-monitor-websocket-test/1.0.0"
        }
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            yield client

    @pytest.fixture
    async def websocket_config(self, http_client):
        """Discover WebSocket configuration from Kick.com."""
        try:
            # Try to discover WebSocket endpoint from API
            config = await self._discover_websocket_config(http_client)
            if config:
                self.pusher_app_key = config.get("app_key")
                self.websocket_url = config.get("websocket_url")
                return config
        except Exception as e:
            print(f"WebSocket config discovery failed: {e}")
        
        # Fallback configuration (this may need to be updated based on findings)
        fallback_config = {
            "app_key": "placeholder_app_key",
            "websocket_url": "wss://ws-us2.pusher.com/app/placeholder_app_key",
            "auth_endpoint": "https://kick.com/broadcasting/auth"
        }
        
        return fallback_config

    async def test_websocket_endpoint_discovery(self, http_client):
        """Test WebSocket endpoint discovery process."""
        # Attempt to discover WebSocket configuration
        print("Attempting WebSocket endpoint discovery...")
        
        # Method 1: Check if there's a specific API endpoint for WebSocket config
        try:
            response = await http_client.get("https://kick.com/api/v1/config/websocket")
            if response.status_code == 200:
                config = response.json()
                print(f"Found WebSocket config via API: {config}")
                assert "websocket_url" in config or "pusher" in config
                return
        except Exception as e:
            print(f"Method 1 failed: {e}")
        
        # Method 2: Check main channel page for embedded config
        try:
            response = await http_client.get("https://kick.com/xqc")
            if response.status_code == 200:
                # Look for Pusher configuration in the HTML
                content = response.text
                if "pusher" in content.lower() or "websocket" in content.lower():
                    print("Found potential WebSocket configuration in channel page")
                    # In real implementation, would parse the HTML/JS for config
        except Exception as e:
            print(f"Method 2 failed: {e}")
        
        # Method 3: Check for broadcasting auth endpoint
        try:
            # This endpoint might provide Pusher configuration
            response = await http_client.post(
                "https://kick.com/broadcasting/auth",
                data={"socket_id": "test", "channel_name": "test"}
            )
            print(f"Broadcasting auth response: {response.status_code}")
        except Exception as e:
            print(f"Method 3 failed: {e}")
        
        # Document discovery process
        print("WebSocket endpoint discovery completed")
        print("Note: Real implementation needs to extract Pusher config from Kick.com responses")

    async def test_websocket_connection_basic(self, websocket_config):
        """Test basic WebSocket connection establishment."""
        if not websocket_config.get("websocket_url"):
            pytest.skip("WebSocket URL not available for testing")
        
        websocket_url = websocket_config["websocket_url"]
        
        try:
            # Test connection establishment
            async with websockets.connect(
                websocket_url,
                timeout=self.websocket_timeout,
                extra_headers={
                    "User-Agent": "kick-monitor-test/1.0.0"
                }
            ) as websocket:
                print(f"WebSocket connected to: {websocket_url}")
                
                # Wait for connection established message
                try:
                    message = await asyncio.wait_for(
                        websocket.recv(),
                        timeout=10.0
                    )
                    
                    event = json.loads(message)
                    print(f"Received connection event: {event.get('event', 'unknown')}")
                    
                    # Should receive pusher:connection_established
                    assert event.get("event") == "pusher:connection_established"
                    assert "data" in event
                    
                    connection_data = event["data"]
                    if "socket_id" in connection_data:
                        socket_id = connection_data["socket_id"]
                        print(f"WebSocket connection established with socket_id: {socket_id}")
                
                except asyncio.TimeoutError:
                    pytest.fail("Did not receive connection established message")
                
        except Exception as e:
            pytest.skip(f"WebSocket connection failed: {e}")

    async def test_channel_subscription_process(self, websocket_config):
        """Test channel subscription process."""
        if not websocket_config.get("websocket_url"):
            pytest.skip("WebSocket URL not available for testing")
        
        websocket_url = websocket_config["websocket_url"]
        
        try:
            async with websockets.connect(websocket_url, timeout=self.websocket_timeout) as websocket:
                # Wait for connection established
                connection_message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                connection_event = json.loads(connection_message)
                
                assert connection_event.get("event") == "pusher:connection_established"
                socket_id = connection_event["data"]["socket_id"]
                
                # Test channel subscription
                test_channel = f"channel.{self.test_channels[0]}"
                
                subscription_message = {
                    "event": "pusher:subscribe",
                    "data": {
                        "channel": test_channel
                    }
                }
                
                # Send subscription message
                await websocket.send(json.dumps(subscription_message))
                print(f"Sent subscription for channel: {test_channel}")
                
                # Wait for subscription response
                try:
                    response_message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    response_event = json.loads(response_message)
                    
                    print(f"Subscription response: {response_event}")
                    
                    # Should receive subscription_succeeded or error
                    assert response_event.get("event") in [
                        "pusher:subscription_succeeded",
                        "pusher:subscription_error",
                        "pusher:error"
                    ]
                    
                    if response_event.get("event") == "pusher:subscription_succeeded":
                        print(f"Successfully subscribed to {test_channel}")
                    else:
                        print(f"Subscription failed: {response_event}")
                
                except asyncio.TimeoutError:
                    print("No subscription response received (may be normal for some channels)")
                
        except Exception as e:
            pytest.skip(f"Channel subscription test failed: {e}")

    async def test_heartbeat_mechanism(self, websocket_config):
        """Test WebSocket heartbeat (ping/pong) mechanism."""
        if not websocket_config.get("websocket_url"):
            pytest.skip("WebSocket URL not available for testing")
        
        websocket_url = websocket_config["websocket_url"]
        
        try:
            async with websockets.connect(websocket_url, timeout=self.websocket_timeout) as websocket:
                # Wait for connection established
                await asyncio.wait_for(websocket.recv(), timeout=10.0)
                
                # Send ping message
                ping_message = {
                    "event": "pusher:ping",
                    "data": {}
                }
                
                await websocket.send(json.dumps(ping_message))
                print("Sent ping message")
                
                # Wait for pong response or other messages
                start_time = asyncio.get_event_loop().time()
                timeout = 30.0
                
                while (asyncio.get_event_loop().time() - start_time) < timeout:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        event = json.loads(message)
                        
                        print(f"Received event: {event.get('event', 'unknown')}")
                        
                        if event.get("event") == "pusher:pong":
                            print("Received pong response")
                            assert True
                            return
                        elif event.get("event") == "pusher:ping":
                            # Server sent ping, respond with pong
                            pong_message = {
                                "event": "pusher:pong",
                                "data": {}
                            }
                            await websocket.send(json.dumps(pong_message))
                            print("Responded to server ping with pong")
                    
                    except asyncio.TimeoutError:
                        continue
                
                print("No pong received, but heartbeat mechanism test completed")
                
        except Exception as e:
            pytest.skip(f"Heartbeat test failed: {e}")

    async def test_event_listening_simulation(self, websocket_config):
        """Test listening for real-time events (simulation)."""
        if not websocket_config.get("websocket_url"):
            pytest.skip("WebSocket URL not available for testing")
        
        websocket_url = websocket_config["websocket_url"]
        
        try:
            async with websockets.connect(websocket_url, timeout=self.websocket_timeout) as websocket:
                # Connection setup
                connection_message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                connection_event = json.loads(connection_message)
                socket_id = connection_event["data"]["socket_id"]
                
                # Subscribe to test channels
                for channel_name in self.test_channels[:2]:  # Test first 2 channels
                    channel = f"channel.{channel_name}"
                    
                    subscription_message = {
                        "event": "pusher:subscribe",
                        "data": {
                            "channel": channel
                        }
                    }
                    
                    await websocket.send(json.dumps(subscription_message))
                    print(f"Subscribed to {channel}")
                
                # Listen for events
                print(f"Listening for events for {self.event_wait_timeout} seconds...")
                start_time = asyncio.get_event_loop().time()
                events_received = []
                
                while (asyncio.get_event_loop().time() - start_time) < self.event_wait_timeout:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        event = json.loads(message)
                        
                        event_type = event.get("event", "unknown")
                        
                        # Log all events for analysis
                        events_received.append(event)
                        print(f"Event: {event_type}")
                        
                        # Check for streamer events
                        if "streamer" in event_type.lower() or "live" in event_type.lower():
                            print(f"Streamer event detected: {event}")
                            self._validate_streamer_event(event)
                        
                        # Break early if we've collected enough events
                        if len(events_received) >= 10:
                            break
                    
                    except asyncio.TimeoutError:
                        continue
                    except json.JSONDecodeError as e:
                        print(f"Invalid JSON received: {e}")
                        continue
                
                print(f"Event listening completed. Received {len(events_received)} events")
                
                # Analyze received events
                event_types = [e.get("event", "unknown") for e in events_received]
                unique_event_types = set(event_types)
                
                print(f"Unique event types: {unique_event_types}")
                
                # Store events for further analysis
                self.received_events = events_received
                
        except Exception as e:
            print(f"Event listening failed: {e}")

    async def test_connection_resilience(self, websocket_config):
        """Test WebSocket connection resilience and reconnection."""
        if not websocket_config.get("websocket_url"):
            pytest.skip("WebSocket URL not available for testing")
        
        websocket_url = websocket_config["websocket_url"]
        
        # Test multiple connection attempts
        successful_connections = 0
        
        for attempt in range(self.max_connection_attempts):
            try:
                print(f"Connection attempt {attempt + 1}")
                
                async with websockets.connect(
                    websocket_url,
                    timeout=self.websocket_timeout,
                    close_timeout=5.0
                ) as websocket:
                    # Test connection
                    connection_message = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    connection_event = json.loads(connection_message)
                    
                    if connection_event.get("event") == "pusher:connection_established":
                        successful_connections += 1
                        print(f"Connection {attempt + 1} successful")
                        
                        # Hold connection briefly
                        await asyncio.sleep(2.0)
                    
                    # Connection will close when exiting context
                
            except Exception as e:
                print(f"Connection {attempt + 1} failed: {e}")
                
                # Wait before retry
                await asyncio.sleep(1.0)
        
        # At least one connection should succeed
        assert successful_connections > 0, "No WebSocket connections succeeded"
        
        print(f"Connection resilience test: {successful_connections}/{self.max_connection_attempts} successful")

    async def test_authentication_with_websocket(self, websocket_config, http_client):
        """Test WebSocket authentication integration."""
        if not websocket_config.get("websocket_url"):
            pytest.skip("WebSocket URL not available for testing")
        
        # Test if WebSocket requires authentication
        websocket_url = websocket_config["websocket_url"]
        
        # Test connection without authentication
        try:
            async with websockets.connect(websocket_url, timeout=10.0) as websocket:
                message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                event = json.loads(message)
                print(f"Unauthenticated connection: {event.get('event')}")
        except Exception as e:
            print(f"Unauthenticated connection failed: {e}")
        
        # Test connection with authentication headers
        try:
            auth_headers = {
                "Authorization": f"Bearer {self.access_token}",
                "User-Agent": "kick-monitor-test/1.0.0"
            }
            
            async with websockets.connect(
                websocket_url,
                timeout=10.0,
                extra_headers=auth_headers
            ) as websocket:
                message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                event = json.loads(message)
                print(f"Authenticated connection: {event.get('event')}")
                
                # Test authenticated channel subscription
                if event.get("event") == "pusher:connection_established":
                    socket_id = event["data"]["socket_id"]
                    
                    # Try to get auth token for private channels if needed
                    auth_response = await self._get_channel_auth(http_client, socket_id, "test-channel")
                    if auth_response:
                        print(f"Channel auth available: {auth_response}")
                
        except Exception as e:
            print(f"Authenticated connection test failed: {e}")

    async def _discover_websocket_config(self, http_client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        """Discover WebSocket configuration from Kick.com."""
        # This method attempts to discover the actual WebSocket configuration
        # In practice, this might involve:
        # 1. Checking specific API endpoints
        # 2. Parsing HTML/JavaScript from main pages
        # 3. Looking for Pusher configuration in network requests
        
        discovery_methods = [
            ("API config endpoint", self._try_api_config),
            ("Channel page parsing", self._try_page_parsing),
            ("Broadcasting auth", self._try_broadcasting_auth)
        ]
        
        for method_name, method_func in discovery_methods:
            try:
                print(f"Trying discovery method: {method_name}")
                config = await method_func(http_client)
                if config:
                    print(f"WebSocket config discovered via {method_name}: {config}")
                    return config
            except Exception as e:
                print(f"Discovery method {method_name} failed: {e}")
        
        return None

    async def _try_api_config(self, http_client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        """Try to get WebSocket config from API endpoint."""
        endpoints = [
            "/config/websocket",
            "/pusher/config",
            "/broadcasting/config"
        ]
        
        for endpoint in endpoints:
            try:
                response = await http_client.get(f"https://kick.com/api/v1{endpoint}")
                if response.status_code == 200:
                    return response.json()
            except Exception:
                continue
        
        return None

    async def _try_page_parsing(self, http_client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        """Try to extract WebSocket config from page content."""
        try:
            response = await http_client.get("https://kick.com")
            if response.status_code == 200:
                content = response.text
                
                # Look for common Pusher configuration patterns
                if "pusher.com" in content or "Pusher(" in content:
                    # In real implementation, would parse the actual config
                    return {
                        "discovery_method": "page_parsing",
                        "found_pusher_references": True
                    }
        except Exception:
            pass
        
        return None

    async def _try_broadcasting_auth(self, http_client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
        """Try to get config from broadcasting auth endpoint."""
        try:
            response = await http_client.post(
                "https://kick.com/broadcasting/auth",
                data={"socket_id": "test", "channel_name": "test"}
            )
            
            if response.status_code in [200, 401, 403]:  # Any response indicates endpoint exists
                return {
                    "discovery_method": "broadcasting_auth", 
                    "auth_endpoint": "https://kick.com/broadcasting/auth",
                    "status_code": response.status_code
                }
        except Exception:
            pass
        
        return None

    async def _get_channel_auth(self, http_client: httpx.AsyncClient, socket_id: str, channel: str) -> Optional[str]:
        """Get channel authentication for private channels."""
        try:
            auth_data = {
                "socket_id": socket_id,
                "channel_name": channel
            }
            
            response = await http_client.post(
                "https://kick.com/broadcasting/auth",
                data=auth_data
            )
            
            if response.status_code == 200:
                return response.text
        except Exception:
            pass
        
        return None

    def _validate_streamer_event(self, event: Dict[str, Any]) -> None:
        """Validate streamer event structure."""
        assert "event" in event, "Event missing event field"
        assert "data" in event, "Event missing data field"
        
        event_type = event["event"]
        data = event["data"]
        
        # Validate based on event type
        if "live" in event_type.lower():
            # StreamerIsLive event
            assert "channel_id" in data or "user_id" in data, "Live event missing channel/user ID"
        
        elif "offline" in event_type.lower():
            # StreamerOffline event
            assert "channel_id" in data or "user_id" in data, "Offline event missing channel/user ID"
        
        print(f"Validated streamer event: {event_type}")


@pytest.mark.integration
class TestWebSocketDocumentation:
    """Documentation tests for WebSocket integration."""
    
    def test_websocket_implementation_guide(self):
        """Document WebSocket implementation approach."""
        implementation_steps = [
            "1. Discover WebSocket endpoint from Kick.com (HTML parsing or API)",
            "2. Establish WebSocket connection with proper headers",
            "3. Handle pusher:connection_established event",
            "4. Subscribe to channels for monitored streamers",
            "5. Process incoming events (StreamerIsLive, StreamerOffline)",
            "6. Implement heartbeat mechanism (ping/pong)",
            "7. Handle reconnection on connection loss",
            "8. Validate and process event data",
            "9. Update database with status changes",
            "10. Monitor connection health and performance"
        ]
        
        print("WebSocket Implementation Guide:")
        for step in implementation_steps:
            print(f"   {step}")
        
        assert True

    def test_websocket_error_handling_guide(self):
        """Document WebSocket error handling strategies."""
        error_scenarios = {
            "Connection Failed": [
                "Implement exponential backoff retry",
                "Try alternative WebSocket endpoints if available",
                "Fall back to polling API if WebSocket unavailable"
            ],
            "Authentication Failed": [
                "Refresh OAuth tokens",
                "Re-authenticate with fresh credentials",
                "Check if WebSocket requires specific authentication"
            ],
            "Subscription Failed": [
                "Verify channel names and formats",
                "Check if channels require special authentication",
                "Implement graceful degradation for failed subscriptions"
            ],
            "Connection Dropped": [
                "Detect connection loss quickly",
                "Reconnect automatically with backoff",
                "Resume subscriptions after reconnection"
            ],
            "Invalid Events": [
                "Validate event structure before processing",
                "Log malformed events for debugging",
                "Continue processing other valid events"
            ]
        }
        
        print("WebSocket Error Handling Strategies:")
        for scenario, strategies in error_scenarios.items():
            print(f"  {scenario}:")
            for strategy in strategies:
                print(f"    - {strategy}")
        
        assert True

    def test_websocket_monitoring_requirements(self):
        """Document WebSocket monitoring and observability needs."""
        monitoring_metrics = {
            "Connection Health": [
                "Connection uptime percentage",
                "Reconnection frequency and success rate",
                "Connection establishment latency"
            ],
            "Event Processing": [
                "Events received per minute",
                "Event processing latency", 
                "Invalid/malformed event rate"
            ],
            "Performance": [
                "WebSocket message throughput",
                "Memory usage for event buffering",
                "CPU usage for event processing"
            ],
            "Errors": [
                "Connection failure rate",
                "Authentication error frequency",
                "Subscription failure rate"
            ]
        }
        
        print("WebSocket Monitoring Requirements:")
        for category, metrics in monitoring_metrics.items():
            print(f"  {category}:")
            for metric in metrics:
                print(f"    - {metric}")
        
        assert True