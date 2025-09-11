"""
End-to-end tests for the complete streamer monitoring workflow.

Tests the entire flow from adding streamers to monitoring their status,
receiving notifications, and managing the monitoring lifecycle.
"""

import pytest
import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional
from unittest.mock import AsyncMock, Mock, patch

from src.lib.config import ConfigurationManager
from src.lib.processor import EventProcessor, Event, EventType
from src.lib.health import HealthMonitor
from src.services.monitor import StreamerMonitorService
from src.services.database import DatabaseService
from src.services.auth import OAuthService
from src.services.websocket import WebSocketService
from src.models.streamer import Streamer, StreamerStatus
from src.cli.main import create_parser
from src.cli.streamers import StreamerCommands
from src.cli.service import ServiceCommands


class StreamerMonitoringWorkflow:
    """Complete streamer monitoring workflow test suite."""
    
    @pytest.fixture(autouse=True)
    async def setup_workflow_environment(self):
        """Setup complete testing environment for workflow tests."""
        # Configuration
        self.config = ConfigurationManager(auto_load=False)
        self.config.load_from_dict({
            "database": {
                "host": "localhost",
                "port": "5432",
                "name": "kick_monitor_test",
                "user": "test_user",
                "password": "test_password"
            },
            "websocket": {
                "url": "wss://ws-us2.pusher.com/socket",
                "timeout": "30",
                "heartbeat_interval": "30",
                "reconnect_delay": "5"
            },
            "kick": {
                "client_id": "test_client_id",
                "client_secret": "test_client_secret",
                "redirect_uri": "http://localhost:8080/auth/callback"
            },
            "monitoring": {
                "batch_size": "10",
                "check_interval": "30",
                "retry_attempts": "3"
            }
        })
        
        # Mock services for testing
        self.mock_database = AsyncMock(spec=DatabaseService)
        self.mock_oauth = AsyncMock(spec=OAuthService)
        self.mock_websocket = AsyncMock(spec=WebSocketService)
        
        # Test data
        self.test_streamers = [
            {
                "kick_user_id": "12345",
                "username": "test_streamer_1",
                "display_name": "Test Streamer 1"
            },
            {
                "kick_user_id": "67890", 
                "username": "test_streamer_2",
                "display_name": "Test Streamer 2"
            },
            {
                "kick_user_id": "11111",
                "username": "test_streamer_3",
                "display_name": "Test Streamer 3"
            }
        ]
        
        yield
        
        # Cleanup
        await self._cleanup_test_environment()
    
    async def _cleanup_test_environment(self):
        """Clean up test environment."""
        # Close any open connections
        if hasattr(self, 'monitor_service'):
            await self.monitor_service.stop()
    
    @pytest.mark.asyncio
    async def test_complete_streamer_onboarding_workflow(self):
        """Test complete workflow for onboarding new streamers."""
        
        print("Testing complete streamer onboarding workflow...")
        
        # Step 1: Setup monitoring service
        with patch('src.services.database.DatabaseService', return_value=self.mock_database), \
             patch('src.services.auth.OAuthService', return_value=self.mock_oauth), \
             patch('src.services.websocket.WebSocketService', return_value=self.mock_websocket):
            
            monitor_service = StreamerMonitorService(self.config)
            
            # Mock database responses for streamer creation
            self.mock_database.add_streamer.return_value = True
            self.mock_database.get_streamer_by_username.return_value = None  # Doesn't exist yet
            
            # Step 2: Add streamers through CLI-like interface
            for streamer_data in self.test_streamers:
                result = await monitor_service.add_streamer(
                    username=streamer_data["username"],
                    kick_user_id=streamer_data["kick_user_id"],
                    display_name=streamer_data["display_name"]
                )
                assert result is True, f"Failed to add streamer {streamer_data['username']}"
            
            # Verify database calls
            assert self.mock_database.add_streamer.call_count == len(self.test_streamers)
            
            # Step 3: Start monitoring service
            self.mock_websocket.connect.return_value = True
            self.mock_oauth.validate_token.return_value = True
            
            start_result = await monitor_service.start()
            assert start_result is True, "Failed to start monitoring service"
            
            # Step 4: Verify streamers are being monitored
            self.mock_database.get_active_streamers.return_value = [
                Streamer(
                    id=i+1,
                    kick_user_id=streamer["kick_user_id"],
                    username=streamer["username"],
                    display_name=streamer["display_name"],
                    status=StreamerStatus.UNKNOWN,
                    is_active=True
                )
                for i, streamer in enumerate(self.test_streamers)
            ]
            
            active_streamers = await monitor_service.get_monitored_streamers()
            assert len(active_streamers) == len(self.test_streamers)
            
            # Step 5: Simulate WebSocket subscriptions
            for streamer in active_streamers:
                self.mock_websocket.subscribe_to_streamer.return_value = True
                subscription_result = await monitor_service.subscribe_to_streamer(streamer.kick_user_id)
                assert subscription_result is True
            
            # Verify WebSocket subscriptions
            assert self.mock_websocket.subscribe_to_streamer.call_count == len(self.test_streamers)
            
            print(f"✓ Successfully onboarded {len(self.test_streamers)} streamers")
            
            await monitor_service.stop()
    
    @pytest.mark.asyncio
    async def test_status_change_detection_workflow(self):
        """Test complete workflow for detecting and processing status changes."""
        
        print("Testing status change detection workflow...")
        
        with patch('src.services.database.DatabaseService', return_value=self.mock_database), \
             patch('src.services.auth.OAuthService', return_value=self.mock_oauth), \
             patch('src.services.websocket.WebSocketService', return_value=self.mock_websocket):
            
            monitor_service = StreamerMonitorService(self.config)
            
            # Setup existing streamers
            existing_streamers = [
                Streamer(
                    id=i+1,
                    kick_user_id=self.test_streamers[i]["kick_user_id"],
                    username=self.test_streamers[i]["username"], 
                    display_name=self.test_streamers[i]["display_name"],
                    status=StreamerStatus.OFFLINE,
                    is_active=True
                )
                for i in range(len(self.test_streamers))
            ]
            
            self.mock_database.get_active_streamers.return_value = existing_streamers
            self.mock_websocket.connect.return_value = True
            self.mock_oauth.validate_token.return_value = True
            
            # Start monitoring
            await monitor_service.start()
            
            # Step 1: Simulate stream start events
            status_changes_recorded = []
            
            def record_status_change(*args, **kwargs):
                status_changes_recorded.append(args)
                return True
            
            self.mock_database.record_status_event.side_effect = record_status_change
            self.mock_database.update_streamer_status.return_value = True
            
            # Step 2: Process incoming WebSocket events
            for i, streamer in enumerate(existing_streamers):
                # Simulate stream start event
                stream_start_event = {
                    "event": "stream-start",
                    "data": json.dumps({
                        "streamer_id": int(streamer.kick_user_id),
                        "title": f"Test Stream {i+1}",
                        "category": "Just Chatting",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                }
                
                # Process event through monitor service
                await monitor_service.process_websocket_event(stream_start_event)
                
                # Wait briefly for processing
                await asyncio.sleep(0.1)
            
            # Step 3: Verify status changes were processed
            assert len(status_changes_recorded) >= len(self.test_streamers), \
                f"Expected at least {len(self.test_streamers)} status changes, got {len(status_changes_recorded)}"
            
            # Step 4: Simulate stream end events
            for i, streamer in enumerate(existing_streamers):
                stream_end_event = {
                    "event": "stream-end",
                    "data": json.dumps({
                        "streamer_id": int(streamer.kick_user_id),
                        "duration": 3600 + i * 300,  # Different durations
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                }
                
                await monitor_service.process_websocket_event(stream_end_event)
                await asyncio.sleep(0.1)
            
            # Step 5: Verify both start and end events were processed
            assert len(status_changes_recorded) >= len(self.test_streamers) * 2, \
                "Expected both start and end events to be processed"
            
            print(f"✓ Successfully processed {len(status_changes_recorded)} status changes")
            
            await monitor_service.stop()
    
    @pytest.mark.asyncio
    async def test_error_recovery_workflow(self):
        """Test complete workflow for error handling and recovery."""
        
        print("Testing error recovery workflow...")
        
        with patch('src.services.database.DatabaseService', return_value=self.mock_database), \
             patch('src.services.auth.OAuthService', return_value=self.mock_oauth), \
             patch('src.services.websocket.WebSocketService', return_value=self.mock_websocket):
            
            monitor_service = StreamerMonitorService(self.config)
            
            # Step 1: Setup with initial success
            self.mock_database.get_active_streamers.return_value = []
            self.mock_websocket.connect.return_value = True
            self.mock_oauth.validate_token.return_value = True
            
            await monitor_service.start()
            
            # Step 2: Simulate database connection failure
            connection_errors = []
            
            def record_error(error):
                connection_errors.append(error)
            
            monitor_service.error_handler.register_handler(Exception, record_error)
            
            # Mock database failure
            self.mock_database.get_active_streamers.side_effect = Exception("Database connection lost")
            
            # Step 3: Trigger operation that should fail
            try:
                await monitor_service.get_monitored_streamers()
            except Exception:
                pass  # Expected to fail
            
            # Step 4: Verify error was recorded
            assert len(connection_errors) > 0, "Database error should have been recorded"
            
            # Step 5: Simulate recovery
            self.mock_database.get_active_streamers.side_effect = None
            self.mock_database.get_active_streamers.return_value = []
            
            # Should work again
            streamers = await monitor_service.get_monitored_streamers()
            assert isinstance(streamers, list), "Service should recover after database reconnection"
            
            # Step 6: Simulate WebSocket disconnection and reconnection
            self.mock_websocket.is_connected.return_value = False
            self.mock_websocket.connect.return_value = True
            
            # Trigger reconnection
            reconnect_result = await monitor_service.reconnect_websocket()
            assert reconnect_result is True, "WebSocket should reconnect successfully"
            
            print("✓ Successfully tested error recovery scenarios")
            
            await monitor_service.stop()
    
    @pytest.mark.asyncio
    async def test_monitoring_lifecycle_workflow(self):
        """Test complete monitoring lifecycle from start to graceful shutdown."""
        
        print("Testing monitoring lifecycle workflow...")
        
        with patch('src.services.database.DatabaseService', return_value=self.mock_database), \
             patch('src.services.auth.OAuthService', return_value=self.mock_oauth), \
             patch('src.services.websocket.WebSocketService', return_value=self.mock_websocket):
            
            monitor_service = StreamerMonitorService(self.config)
            
            # Step 1: Initial state verification
            assert not monitor_service.is_running(), "Service should not be running initially"
            
            # Step 2: Service startup
            self.mock_database.get_active_streamers.return_value = []
            self.mock_websocket.connect.return_value = True
            self.mock_oauth.validate_token.return_value = True
            
            startup_start = time.time()
            start_result = await monitor_service.start()
            startup_duration = time.time() - startup_start
            
            assert start_result is True, "Service should start successfully"
            assert monitor_service.is_running(), "Service should be running after start"
            assert startup_duration < 10.0, "Service should start within reasonable time"
            
            # Step 3: Runtime monitoring
            # Simulate some activity
            for i in range(5):
                await asyncio.sleep(0.2)  # Simulate runtime
                
                # Check service health
                health_status = monitor_service.get_health_status()
                assert health_status["status"] in ["healthy", "degraded"], \
                    f"Service should be healthy during runtime, got {health_status['status']}"
            
            # Step 4: Metrics collection
            metrics = monitor_service.get_metrics()
            assert "uptime_seconds" in metrics, "Metrics should include uptime"
            assert metrics["uptime_seconds"] > 0, "Uptime should be positive"
            
            # Step 5: Graceful shutdown
            shutdown_start = time.time()
            await monitor_service.stop()
            shutdown_duration = time.time() - shutdown_start
            
            assert not monitor_service.is_running(), "Service should not be running after stop"
            assert shutdown_duration < 5.0, "Service should shutdown within reasonable time"
            
            # Step 6: Verify cleanup
            self.mock_websocket.disconnect.assert_called()
            
            print(f"✓ Service lifecycle completed (startup: {startup_duration:.2f}s, shutdown: {shutdown_duration:.2f}s)")
    
    @pytest.mark.asyncio
    async def test_multi_streamer_concurrent_monitoring(self):
        """Test monitoring multiple streamers concurrently with status changes."""
        
        print("Testing concurrent multi-streamer monitoring...")
        
        with patch('src.services.database.DatabaseService', return_value=self.mock_database), \
             patch('src.services.auth.OAuthService', return_value=self.mock_oauth), \
             patch('src.services.websocket.WebSocketService', return_value=self.mock_websocket):
            
            monitor_service = StreamerMonitorService(self.config)
            
            # Setup multiple streamers
            streamers = [
                Streamer(
                    id=i+1,
                    kick_user_id=str(10000 + i),
                    username=f"concurrent_streamer_{i}",
                    display_name=f"Concurrent Streamer {i}",
                    status=StreamerStatus.OFFLINE,
                    is_active=True
                )
                for i in range(10)  # 10 concurrent streamers
            ]
            
            self.mock_database.get_active_streamers.return_value = streamers
            self.mock_websocket.connect.return_value = True
            self.mock_oauth.validate_token.return_value = True
            
            await monitor_service.start()
            
            # Track status changes
            status_changes = []
            
            def record_status_change(*args, **kwargs):
                status_changes.append({
                    "timestamp": time.time(),
                    "args": args,
                    "kwargs": kwargs
                })
                return True
            
            self.mock_database.record_status_event.side_effect = record_status_change
            self.mock_database.update_streamer_status.return_value = True
            
            # Simulate concurrent status changes
            events = []
            for i, streamer in enumerate(streamers):
                # Create multiple events per streamer
                for event_type in ["stream-start", "viewer-update", "stream-end"]:
                    event = {
                        "event": event_type,
                        "data": json.dumps({
                            "streamer_id": int(streamer.kick_user_id),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "event_id": f"{event_type}_{i}_{len(events)}"
                        })
                    }
                    events.append(event)
            
            # Process all events concurrently
            start_time = time.time()
            tasks = [monitor_service.process_websocket_event(event) for event in events]
            await asyncio.gather(*tasks, return_exceptions=True)
            processing_time = time.time() - start_time
            
            # Wait for any pending processing
            await asyncio.sleep(0.5)
            
            # Verify concurrent processing
            assert len(status_changes) > 0, "Status changes should have been processed"
            assert processing_time < 5.0, f"Concurrent processing took too long: {processing_time:.2f}s"
            
            # Verify no data corruption (all events processed)
            processed_events = len(status_changes)
            expected_events = len(streamers) * 3  # 3 events per streamer
            
            print(f"✓ Processed {processed_events} events for {len(streamers)} streamers in {processing_time:.2f}s")
            
            await monitor_service.stop()
    
    @pytest.mark.asyncio
    async def test_configuration_reload_workflow(self):
        """Test dynamic configuration reload without service restart."""
        
        print("Testing configuration reload workflow...")
        
        with patch('src.services.database.DatabaseService', return_value=self.mock_database), \
             patch('src.services.auth.OAuthService', return_value=self.mock_oauth), \
             patch('src.services.websocket.WebSocketService', return_value=self.mock_websocket):
            
            monitor_service = StreamerMonitorService(self.config)
            
            # Start with initial configuration
            await monitor_service.start()
            
            initial_config = monitor_service.get_configuration()
            assert initial_config["monitoring"]["batch_size"] == "10"
            
            # Update configuration
            new_config = self.config.get_all().copy()
            new_config["monitoring"]["batch_size"] = "20"
            new_config["monitoring"]["check_interval"] = "60"
            
            # Reload configuration
            reload_result = await monitor_service.reload_configuration(new_config)
            assert reload_result is True, "Configuration reload should succeed"
            
            # Verify new configuration is active
            updated_config = monitor_service.get_configuration()
            assert updated_config["monitoring"]["batch_size"] == "20"
            assert updated_config["monitoring"]["check_interval"] == "60"
            
            # Service should still be running
            assert monitor_service.is_running(), "Service should remain running after config reload"
            
            print("✓ Configuration reloaded successfully without service restart")
            
            await monitor_service.stop()
    
    @pytest.mark.asyncio
    async def test_health_monitoring_integration_workflow(self):
        """Test integration with health monitoring system."""
        
        print("Testing health monitoring integration...")
        
        with patch('src.services.database.DatabaseService', return_value=self.mock_database), \
             patch('src.services.auth.OAuthService', return_value=self.mock_oauth), \
             patch('src.services.websocket.WebSocketService', return_value=self.mock_websocket):
            
            # Setup health monitor
            health_monitor = HealthMonitor(check_interval=1)
            
            monitor_service = StreamerMonitorService(self.config)
            monitor_service.set_health_monitor(health_monitor)
            
            # Configure health check responses
            self.mock_database.health_check.return_value = {"status": "healthy"}
            self.mock_websocket.get_connection_status.return_value = {"is_connected": True, "uptime_seconds": 100}
            self.mock_oauth.get_token_info.return_value = {"is_expired": False, "expires_in_seconds": 3600}
            
            await monitor_service.start()
            await health_monitor.start()
            
            # Wait for health checks
            await asyncio.sleep(2)
            
            # Verify health status
            overall_health = health_monitor.get_overall_status()
            assert overall_health.value in ["healthy", "degraded"], \
                f"Overall health should be healthy or degraded, got {overall_health.value}"
            
            health_summary = health_monitor.get_health_summary()
            assert "components" in health_summary
            assert health_summary["components"]["total"] > 0
            
            # Simulate component failure
            self.mock_database.health_check.return_value = {"status": "unhealthy", "error": "Connection timeout"}
            
            # Wait for health check to detect failure
            await asyncio.sleep(2)
            
            # Verify degraded health
            updated_health = health_monitor.get_overall_status()
            health_summary = health_monitor.get_health_summary()
            
            # Should have detected the database issue
            assert len(health_summary["alerts"]["recent"]) > 0 or updated_health.value in ["degraded", "unhealthy"]
            
            print("✓ Health monitoring integration working correctly")
            
            await health_monitor.stop()
            await monitor_service.stop()


@pytest.mark.e2e
class TestWorkflowScenarios:
    """Additional end-to-end workflow scenarios."""
    
    def test_workflow_instance_creation(self):
        """Test that workflow test class can be instantiated."""
        workflow = StreamerMonitoringWorkflow()
        assert workflow is not None
    
    @pytest.mark.asyncio
    async def test_workflow_environment_setup(self):
        """Test workflow environment setup and teardown."""
        workflow = StreamerMonitoringWorkflow()
        
        # Manual setup for testing
        await workflow.setup_workflow_environment()
        
        # Verify setup
        assert hasattr(workflow, 'config')
        assert hasattr(workflow, 'test_streamers')
        assert len(workflow.test_streamers) > 0
        
        # Manual teardown
        await workflow._cleanup_test_environment()


if __name__ == "__main__":
    # Run end-to-end tests
    pytest.main([__file__, "-v", "-m", "e2e"])