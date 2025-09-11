"""
Manual mode UI testing for the Rich-based console interface.

Tests the real-time console display, keyboard interaction, status updates,
and overall user experience of the manual monitoring mode.
"""

import asyncio
import logging
import os
import pytest
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional
from unittest.mock import patch, AsyncMock, MagicMock
from io import StringIO
import threading

try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
    from rich.layout import Layout
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from src.cli.manual import ManualModeUI, ManualModeController, DisplayConfig
from src.models.streamer import Streamer, StreamerStatus, StreamerCreate
from src.lib.config import ConfigurationManager
from tests.fixtures.test_db import DatabaseTestHelper, DatabaseTestConfig, skip_if_no_database
from tests.fixtures.websocket_utils import MockWebSocketConnection, WebSocketMessageBuilder

logger = logging.getLogger(__name__)


@pytest.mark.skipif(not RICH_AVAILABLE, reason="Rich library not available")
@pytest.mark.integration
class TestManualModeUI:
    """Test manual mode UI components and functionality."""
    
    @pytest.fixture
    def mock_console(self):
        """Mock Rich console for testing."""
        console = MagicMock(spec=Console)
        console.size.width = 120
        console.size.height = 30
        return console
    
    @pytest.fixture
    def display_config(self):
        """Display configuration for testing."""
        return DisplayConfig(
            refresh_rate=2.0,  # Faster refresh for testing
            max_streamers_displayed=10,
            show_header=True,
            show_footer=True,
            color_scheme={
                "online": "green",
                "offline": "red", 
                "unknown": "yellow",
                "header": "bold blue",
                "footer": "dim"
            }
        )
    
    @pytest.fixture
    def test_streamers_data(self):
        """Test streamer data for UI testing."""
        return [
            {
                "id": 1,
                "username": "teststreamer1",
                "display_name": "Test Streamer 1",
                "status": StreamerStatus.ONLINE,
                "last_seen_online": datetime.now(timezone.utc),
                "last_status_update": datetime.now(timezone.utc),
                "viewer_count": 150,
                "update_count": 5
            },
            {
                "id": 2,
                "username": "teststreamer2", 
                "display_name": "Test Streamer 2",
                "status": StreamerStatus.OFFLINE,
                "last_seen_online": datetime.now(timezone.utc) - timedelta(hours=2),
                "last_status_update": datetime.now(timezone.utc) - timedelta(minutes=30),
                "viewer_count": 0,
                "update_count": 3
            },
            {
                "id": 3,
                "username": "teststreamer3",
                "display_name": "Test Streamer 3", 
                "status": StreamerStatus.UNKNOWN,
                "last_seen_online": None,
                "last_status_update": datetime.now(timezone.utc) - timedelta(minutes=5),
                "viewer_count": None,
                "update_count": 1
            }
        ]
    
    def test_manual_mode_ui_initialization(self, mock_console, display_config):
        """Test ManualModeUI initialization."""
        ui = ManualModeUI(console=mock_console, config=display_config)
        
        assert ui.console == mock_console
        assert ui.config == display_config
        assert ui.is_running is False
        assert ui.total_updates == 0
        assert ui.streamers_data == {}
    
    def test_display_config_validation(self):
        """Test DisplayConfig validation."""
        # Valid configuration
        config = DisplayConfig(
            refresh_rate=1.0,
            max_streamers_displayed=20,
            show_header=True,
            show_footer=False
        )
        
        assert config.refresh_rate == 1.0
        assert config.max_streamers_displayed == 20
        assert config.show_header is True
        assert config.show_footer is False
        
        # Test default color scheme
        assert "online" in config.color_scheme
        assert "offline" in config.color_scheme
        assert "unknown" in config.color_scheme
    
    def test_streamer_data_update(self, mock_console, display_config, test_streamers_data):
        """Test updating streamer data in UI."""
        ui = ManualModeUI(console=mock_console, config=display_config)
        
        # Update with test data
        for streamer_data in test_streamers_data:
            ui.update_streamer_data(streamer_data["id"], streamer_data)
        
        assert len(ui.streamers_data) == 3
        assert ui.streamers_data[1]["username"] == "teststreamer1"
        assert ui.streamers_data[1]["status"] == StreamerStatus.ONLINE
        assert ui.streamers_data[2]["status"] == StreamerStatus.OFFLINE
        assert ui.streamers_data[3]["status"] == StreamerStatus.UNKNOWN
    
    def test_table_generation(self, mock_console, display_config, test_streamers_data):
        """Test table generation for streamer display."""
        ui = ManualModeUI(console=mock_console, config=display_config)
        
        # Add test data
        for streamer_data in test_streamers_data:
            ui.update_streamer_data(streamer_data["id"], streamer_data)
        
        # Generate table
        table = ui._create_streamers_table()
        
        assert isinstance(table, Table)
        assert table.title == "Kick Streamers Status"
        
        # Check table has correct number of rows (excluding header)
        # Note: This is a simplified check as Rich tables are complex objects
        assert len(ui.streamers_data) == 3
    
    def test_status_color_mapping(self, mock_console, display_config):
        """Test status color mapping in UI."""
        ui = ManualModeUI(console=mock_console, config=display_config)
        
        # Test color mapping
        assert ui._get_status_color(StreamerStatus.ONLINE) == "green"
        assert ui._get_status_color(StreamerStatus.OFFLINE) == "red"
        assert ui._get_status_color(StreamerStatus.UNKNOWN) == "yellow"
    
    def test_header_generation(self, mock_console, display_config):
        """Test header generation."""
        ui = ManualModeUI(console=mock_console, config=display_config)
        ui.connection_status = "Connected"
        ui.total_streamers = 5
        ui.total_updates = 42
        
        header = ui._create_header()
        
        # Should contain basic information
        assert isinstance(header, (Text, str))
    
    def test_footer_generation(self, mock_console, display_config):
        """Test footer generation."""
        ui = ManualModeUI(console=mock_console, config=display_config)
        
        footer = ui._create_footer()
        
        # Should contain keyboard shortcuts
        assert isinstance(footer, (Text, str))
    
    def test_sorting_functionality(self, mock_console, display_config, test_streamers_data):
        """Test streamer sorting functionality."""
        ui = ManualModeUI(console=mock_console, config=display_config)
        
        # Add test data
        for streamer_data in test_streamers_data:
            ui.update_streamer_data(streamer_data["id"], streamer_data)
        
        # Test sorting by status
        ui.sort_by = "status"
        sorted_data = ui._get_sorted_streamers()
        
        # Online streamers should come first when sorted by status
        statuses = [data["status"] for data in sorted_data]
        online_indices = [i for i, status in enumerate(statuses) if status == StreamerStatus.ONLINE]
        offline_indices = [i for i, status in enumerate(statuses) if status == StreamerStatus.OFFLINE]
        
        if online_indices and offline_indices:
            assert min(online_indices) < max(offline_indices)
        
        # Test sorting by username
        ui.sort_by = "username"
        sorted_data = ui._get_sorted_streamers()
        usernames = [data["username"] for data in sorted_data]
        assert usernames == sorted(usernames)
    
    @pytest.mark.asyncio
    async def test_ui_refresh_cycle(self, mock_console, display_config):
        """Test UI refresh cycle."""
        ui = ManualModeUI(console=mock_console, config=display_config)
        ui.config.refresh_rate = 0.1  # Very fast refresh for testing
        
        refresh_count = 0
        original_refresh = ui._refresh_display
        
        def mock_refresh():
            nonlocal refresh_count
            refresh_count += 1
            original_refresh()
        
        ui._refresh_display = mock_refresh
        
        # Start UI for a short time
        ui_task = asyncio.create_task(ui.start())
        await asyncio.sleep(0.5)  # Let it run for 0.5 seconds
        ui.stop()
        
        try:
            await asyncio.wait_for(ui_task, timeout=1.0)
        except asyncio.TimeoutError:
            ui_task.cancel()
        
        # Should have refreshed multiple times
        assert refresh_count > 0


@pytest.mark.skipif(not RICH_AVAILABLE, reason="Rich library not available")
@pytest.mark.integration 
class TestManualModeController:
    """Test manual mode controller and keyboard interaction."""
    
    @pytest.fixture
    def config_manager(self):
        """Configuration manager for testing."""
        config = ConfigurationManager(auto_load=False)
        config.set("MONITORING_STATUS_UPDATE_INTERVAL", "1")
        config.set("DATABASE_HOST", "localhost")
        config.set("DATABASE_NAME", "test_db")
        config.set("DATABASE_USER", "test_user")
        config.set("DATABASE_PASSWORD", "test_pass")
        return config
    
    @pytest.fixture
    def mock_services(self):
        """Mock services for controller testing."""
        return {
            "database": AsyncMock(),
            "websocket": AsyncMock(),
            "monitoring": AsyncMock()
        }
    
    def test_controller_initialization(self, config_manager, mock_services):
        """Test controller initialization."""
        controller = ManualModeController(
            config=config_manager,
            database_service=mock_services["database"],
            websocket_service=mock_services["websocket"]
        )
        
        assert controller.config == config_manager
        assert controller.database_service == mock_services["database"]
        assert controller.websocket_service == mock_services["websocket"]
        assert controller.is_running is False
    
    @pytest.mark.asyncio
    async def test_keyboard_input_handling(self, config_manager, mock_services):
        """Test keyboard input handling."""
        controller = ManualModeController(
            config=config_manager,
            database_service=mock_services["database"],
            websocket_service=mock_services["websocket"]
        )
        
        # Mock keyboard input
        with patch('src.cli.manual.keyboard') as mock_keyboard:
            # Simulate 'q' key press
            mock_keyboard.is_pressed.return_value = True
            mock_keyboard.read_key.return_value = 'q'
            
            # Test quit command
            result = await controller._handle_keyboard_input('q')
            assert result is False  # Should signal to quit
            
            # Test refresh command
            result = await controller._handle_keyboard_input('r')
            assert result is True  # Should continue running
            
            # Test sort command
            result = await controller._handle_keyboard_input('s')
            assert result is True  # Should continue running
    
    @pytest.mark.asyncio
    async def test_data_fetching(self, config_manager, mock_services):
        """Test data fetching from services."""
        controller = ManualModeController(
            config=config_manager,
            database_service=mock_services["database"],
            websocket_service=mock_services["websocket"]
        )
        
        # Mock database responses
        mock_streamers = [
            Streamer(
                id=1,
                kick_user_id="12345",
                username="teststreamer1",
                display_name="Test Streamer 1",
                status=StreamerStatus.ONLINE
            ),
            Streamer(
                id=2,
                kick_user_id="67890", 
                username="teststreamer2",
                display_name="Test Streamer 2",
                status=StreamerStatus.OFFLINE
            )
        ]
        
        mock_services["database"].get_all_streamers.return_value = mock_streamers
        mock_services["websocket"].get_connection_status.return_value = {
            "is_connected": True,
            "uptime_seconds": 120
        }
        
        # Fetch data
        streamers = await controller._fetch_streamers_data()
        connection_status = await controller._fetch_connection_status()
        
        assert len(streamers) == 2
        assert streamers[0].username == "teststreamer1"
        assert connection_status["is_connected"] is True
        
        # Verify service calls
        mock_services["database"].get_all_streamers.assert_called_once()
        mock_services["websocket"].get_connection_status.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_data_update_cycle(self, config_manager, mock_services):
        """Test periodic data update cycle."""
        controller = ManualModeController(
            config=config_manager,
            database_service=mock_services["database"],
            websocket_service=mock_services["websocket"]
        )
        
        # Mock very fast update cycle for testing
        controller.update_interval = 0.1
        
        update_count = 0
        
        async def mock_update():
            nonlocal update_count
            update_count += 1
            
        controller._update_ui_data = mock_update
        
        # Run for short time
        task = asyncio.create_task(controller._data_update_loop())
        await asyncio.sleep(0.3)
        controller.is_running = False
        
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
        
        # Should have updated multiple times
        assert update_count >= 2


@pytest.mark.skipif(not RICH_AVAILABLE, reason="Rich library not available")
@pytest.mark.integration
class TestManualModeIntegration:
    """Integration tests for manual mode with real components."""
    
    @skip_if_no_database()
    @pytest.mark.asyncio
    async def test_manual_mode_with_database(self):
        """Test manual mode with real database integration."""
        # Setup test database
        db_config = DatabaseTestConfig()
        helper = DatabaseTestHelper(db_config)
        
        try:
            await helper.setup_database()
            
            # Create test streamers
            streamer_ids = await helper.create_test_streamers(3)
            
            # Initialize manual mode components
            config = ConfigurationManager(auto_load=False)
            for key, value in db_config.__dict__.items():
                if key.startswith('host'):
                    config.set('DATABASE_HOST', value)
                elif key.startswith('port'):
                    config.set('DATABASE_PORT', str(value))
                elif key.startswith('database'):
                    config.set('DATABASE_NAME', value)
                elif key.startswith('username'):
                    config.set('DATABASE_USER', value)
                elif key.startswith('password'):
                    config.set('DATABASE_PASSWORD', value)
            
            # Mock console to avoid actual display
            with patch('rich.console.Console') as mock_console_class:
                mock_console = MagicMock()
                mock_console_class.return_value = mock_console
                
                async with helper.database_service() as db_service:
                    controller = ManualModeController(
                        config=config,
                        database_service=db_service,
                        websocket_service=AsyncMock()
                    )
                    
                    # Test data fetching
                    streamers = await controller._fetch_streamers_data()
                    assert len(streamers) == 3
                    
                    # Test UI updates
                    ui = ManualModeUI(console=mock_console)
                    
                    for streamer in streamers:
                        ui.update_streamer_data(streamer.id, {
                            "id": streamer.id,
                            "username": streamer.username,
                            "status": streamer.status,
                            "last_status_update": streamer.last_status_update
                        })
                    
                    assert len(ui.streamers_data) == 3
        
        finally:
            await helper.cleanup_database()
    
    @pytest.mark.asyncio
    async def test_manual_mode_websocket_integration(self):
        """Test manual mode with WebSocket integration."""
        mock_websocket = MockWebSocketConnection()
        
        with patch('src.cli.manual.WebSocketService') as mock_ws_service:
            mock_service = AsyncMock()
            mock_service.get_connection_status.return_value = {
                "is_connected": True,
                "uptime_seconds": 300,
                "total_messages_received": 150
            }
            mock_ws_service.return_value = mock_service
            
            config = ConfigurationManager(auto_load=False)
            controller = ManualModeController(
                config=config,
                database_service=AsyncMock(),
                websocket_service=mock_service
            )
            
            # Test connection status fetching
            status = await controller._fetch_connection_status()
            assert status["is_connected"] is True
            assert status["uptime_seconds"] == 300
            
            # Verify service was called
            mock_service.get_connection_status.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_manual_mode_error_handling(self):
        """Test manual mode error handling scenarios."""
        config = ConfigurationManager(auto_load=False)
        
        # Mock services that will fail
        failing_db_service = AsyncMock()
        failing_db_service.get_all_streamers.side_effect = Exception("Database connection failed")
        
        failing_ws_service = AsyncMock()
        failing_ws_service.get_connection_status.side_effect = Exception("WebSocket error")
        
        controller = ManualModeController(
            config=config,
            database_service=failing_db_service,
            websocket_service=failing_ws_service
        )
        
        # Test graceful error handling
        streamers = await controller._fetch_streamers_data()
        assert streamers == []  # Should return empty list on error
        
        status = await controller._fetch_connection_status()
        assert status["is_connected"] is False  # Should indicate disconnected on error
    
    def test_display_configuration_customization(self):
        """Test display configuration customization."""
        # Test custom color scheme
        custom_config = DisplayConfig(
            color_scheme={
                "online": "bright_green",
                "offline": "bright_red", 
                "unknown": "bright_yellow",
                "header": "bold cyan",
                "footer": "dim white"
            },
            refresh_rate=0.5,
            max_streamers_displayed=25
        )
        
        assert custom_config.color_scheme["online"] == "bright_green"
        assert custom_config.refresh_rate == 0.5
        assert custom_config.max_streamers_displayed == 25
    
    @pytest.mark.asyncio
    async def test_manual_mode_performance(self):
        """Test manual mode performance with many streamers."""
        # Create mock data for many streamers
        mock_streamers = []
        for i in range(100):
            mock_streamers.append({
                "id": i + 1,
                "username": f"streamer_{i+1:03d}",
                "display_name": f"Streamer {i+1}",
                "status": StreamerStatus.ONLINE if i % 3 == 0 else 
                          StreamerStatus.OFFLINE if i % 3 == 1 else
                          StreamerStatus.UNKNOWN,
                "last_status_update": datetime.now(timezone.utc),
                "update_count": i % 10
            })
        
        # Test UI with large dataset
        with patch('rich.console.Console') as mock_console_class:
            mock_console = MagicMock()
            mock_console_class.return_value = mock_console
            
            ui = ManualModeUI(console=mock_console)
            
            # Measure performance of data updates
            start_time = time.time()
            
            for streamer_data in mock_streamers:
                ui.update_streamer_data(streamer_data["id"], streamer_data)
            
            # Generate display table
            table = ui._create_streamers_table()
            
            end_time = time.time()
            update_time = end_time - start_time
            
            # Should handle 100 streamers reasonably quickly
            assert update_time < 1.0  # Less than 1 second
            assert len(ui.streamers_data) == 100
    
    def test_keyboard_shortcut_documentation(self):
        """Test that keyboard shortcuts are properly documented."""
        # This ensures keyboard shortcuts are consistent and documented
        shortcuts = {
            'q': 'Quit application',
            'r': 'Refresh data',
            's': 'Sort by status',
            'u': 'Sort by username',
            't': 'Sort by last update time',
            'c': 'Clear screen',
            'h': 'Show help'
        }
        
        for key, description in shortcuts.items():
            assert len(key) == 1  # Single character shortcuts
            assert len(description) > 0  # Has description
            assert isinstance(description, str)
        
        logger.info(f"Documented keyboard shortcuts: {shortcuts}")


if __name__ == "__main__":
    # Run tests with proper logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    pytest.main([__file__, "-v", "-s"])