"""
End-to-end testing package for Kick Streamer Monitor.

This package contains complete workflow tests that exercise the entire system
from user interface to data persistence, validating real-world scenarios.
"""

from .test_streamer_monitoring_workflow import StreamerMonitoringWorkflow
from .test_oauth_flow import OAuthFlowTest  
from .test_websocket_streaming import WebSocketStreamingTest
from .test_cli_operations import CLIOperationsTest
from .test_system_integration import SystemIntegrationTest

__all__ = [
    "StreamerMonitoringWorkflow",
    "OAuthFlowTest",
    "WebSocketStreamingTest", 
    "CLIOperationsTest",
    "SystemIntegrationTest",
]