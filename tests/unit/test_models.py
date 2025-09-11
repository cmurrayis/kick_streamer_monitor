"""
Unit tests for data models.

Tests all model validation, state transitions, serialization, and business logic
for Streamer, StatusEvent, and Configuration entities.
"""

import pytest
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
from pydantic import ValidationError

from src.models.streamer import (
    Streamer, StreamerCreate, StreamerUpdate, StreamerStatusUpdate,
    StreamerStatus, StreamerStateTransition, StreamerBase
)
from src.models.status_event import (
    StatusEvent, StatusEventCreate, EventType, StatusEventUpdate
)
from src.models.configuration import (
    Configuration, ConfigurationCreate, ConfigurationUpdate,
    ConfigCategory, ConfigurationType
)


class TestStreamerStatus:
    """Test StreamerStatus enum."""
    
    def test_streamer_status_values(self):
        """Test StreamerStatus enum values."""
        assert StreamerStatus.ONLINE == "online"
        assert StreamerStatus.OFFLINE == "offline"
        assert StreamerStatus.UNKNOWN == "unknown"
    
    def test_streamer_status_enum_membership(self):
        """Test StreamerStatus enum membership."""
        assert "online" in StreamerStatus
        assert "offline" in StreamerStatus
        assert "unknown" in StreamerStatus
        assert "invalid" not in StreamerStatus


class TestStreamerStateTransition:
    """Test StreamerStateTransition logic."""
    
    def test_valid_transitions_from_unknown(self):
        """Test valid transitions from UNKNOWN status."""
        valid = StreamerStateTransition.VALID_TRANSITIONS[StreamerStatus.UNKNOWN]
        assert StreamerStatus.ONLINE in valid
        assert StreamerStatus.OFFLINE in valid
        assert len(valid) == 2
    
    def test_valid_transitions_from_online(self):
        """Test valid transitions from ONLINE status."""
        valid = StreamerStateTransition.VALID_TRANSITIONS[StreamerStatus.ONLINE]
        assert StreamerStatus.OFFLINE in valid
        assert StreamerStatus.ONLINE not in valid
        assert StreamerStatus.UNKNOWN not in valid
        assert len(valid) == 1
    
    def test_valid_transitions_from_offline(self):
        """Test valid transitions from OFFLINE status."""
        valid = StreamerStateTransition.VALID_TRANSITIONS[StreamerStatus.OFFLINE]
        assert StreamerStatus.ONLINE in valid
        assert StreamerStatus.OFFLINE not in valid
        assert StreamerStatus.UNKNOWN not in valid
        assert len(valid) == 1
    
    def test_is_valid_transition_unknown_to_online(self):
        """Test transition from UNKNOWN to ONLINE."""
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.UNKNOWN, StreamerStatus.ONLINE
        ) is True
    
    def test_is_valid_transition_unknown_to_offline(self):
        """Test transition from UNKNOWN to OFFLINE."""
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.UNKNOWN, StreamerStatus.OFFLINE
        ) is True
    
    def test_is_valid_transition_online_to_offline(self):
        """Test transition from ONLINE to OFFLINE."""
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.ONLINE, StreamerStatus.OFFLINE
        ) is True
    
    def test_is_valid_transition_offline_to_online(self):
        """Test transition from OFFLINE to ONLINE."""
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.OFFLINE, StreamerStatus.ONLINE
        ) is True
    
    def test_invalid_transitions(self):
        """Test invalid state transitions."""
        # ONLINE to ONLINE (no change)
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.ONLINE, StreamerStatus.ONLINE
        ) is False
        
        # OFFLINE to OFFLINE (no change)
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.OFFLINE, StreamerStatus.OFFLINE
        ) is False
        
        # ONLINE to UNKNOWN (invalid)
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.ONLINE, StreamerStatus.UNKNOWN
        ) is False
        
        # OFFLINE to UNKNOWN (invalid)
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.OFFLINE, StreamerStatus.UNKNOWN
        ) is False
    
    def test_get_valid_transitions(self):
        """Test getting valid transitions for each status."""
        unknown_transitions = StreamerStateTransition.get_valid_transitions(StreamerStatus.UNKNOWN)
        assert set(unknown_transitions) == {StreamerStatus.ONLINE, StreamerStatus.OFFLINE}
        
        online_transitions = StreamerStateTransition.get_valid_transitions(StreamerStatus.ONLINE)
        assert online_transitions == [StreamerStatus.OFFLINE]
        
        offline_transitions = StreamerStateTransition.get_valid_transitions(StreamerStatus.OFFLINE)
        assert offline_transitions == [StreamerStatus.ONLINE]


class TestStreamerBase:
    """Test StreamerBase model validation."""
    
    @pytest.fixture
    def valid_streamer_data(self):
        """Valid streamer data for testing."""
        return {
            "kick_user_id": "12345",
            "username": "teststreamer",
            "display_name": "Test Streamer",
            "status": StreamerStatus.UNKNOWN
        }
    
    def test_streamer_base_creation_valid(self, valid_streamer_data):
        """Test creating StreamerBase with valid data."""
        streamer = StreamerBase(**valid_streamer_data)
        assert streamer.kick_user_id == "12345"
        assert streamer.username == "teststreamer"
        assert streamer.display_name == "Test Streamer"
        assert streamer.status == StreamerStatus.UNKNOWN
        assert streamer.is_active is True
        assert streamer.last_seen_online is None
        assert streamer.last_status_update is None
    
    def test_streamer_base_required_fields(self):
        """Test StreamerBase required field validation."""
        # Missing kick_user_id
        with pytest.raises(ValidationError) as exc_info:
            StreamerBase(username="test")
        assert "kick_user_id" in str(exc_info.value)
        
        # Missing username
        with pytest.raises(ValidationError) as exc_info:
            StreamerBase(kick_user_id="12345")
        assert "username" in str(exc_info.value)
    
    def test_username_validation(self):
        """Test username validation rules."""
        # Empty username
        with pytest.raises(ValidationError) as exc_info:
            StreamerBase(kick_user_id="12345", username="")
        assert "Username cannot be empty" in str(exc_info.value)
        
        # Whitespace-only username
        with pytest.raises(ValidationError) as exc_info:
            StreamerBase(kick_user_id="12345", username="   ")
        assert "Username cannot be empty" in str(exc_info.value)
        
        # Username with leading/trailing whitespace gets trimmed
        streamer = StreamerBase(kick_user_id="12345", username="  testuser  ")
        assert streamer.username == "testuser"
    
    def test_kick_user_id_validation(self):
        """Test kick_user_id validation rules."""
        # Empty kick_user_id
        with pytest.raises(ValidationError) as exc_info:
            StreamerBase(kick_user_id="", username="test")
        assert "Kick user ID cannot be empty" in str(exc_info.value)
        
        # Whitespace-only kick_user_id
        with pytest.raises(ValidationError) as exc_info:
            StreamerBase(kick_user_id="   ", username="test")
        assert "Kick user ID cannot be empty" in str(exc_info.value)
        
        # kick_user_id with leading/trailing whitespace gets trimmed
        streamer = StreamerBase(kick_user_id="  12345  ", username="test")
        assert streamer.kick_user_id == "12345"
    
    def test_timestamp_validation_future_dates(self):
        """Test that future timestamps are rejected."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        
        # Future last_seen_online
        with pytest.raises(ValidationError) as exc_info:
            StreamerBase(
                kick_user_id="12345",
                username="test",
                last_seen_online=future_time
            )
        assert "cannot be in the future" in str(exc_info.value)
        
        # Future last_status_update
        with pytest.raises(ValidationError) as exc_info:
            StreamerBase(
                kick_user_id="12345",
                username="test",
                last_status_update=future_time
            )
        assert "cannot be in the future" in str(exc_info.value)
    
    def test_status_consistency_validation(self):
        """Test status and timestamp consistency validation."""
        # ONLINE status without last_seen_online should auto-set it
        streamer = StreamerBase(
            kick_user_id="12345",
            username="test",
            status=StreamerStatus.ONLINE
        )
        assert streamer.last_seen_online is not None
        assert streamer.last_seen_online <= datetime.now(timezone.utc)
    
    def test_default_values(self):
        """Test default values for optional fields."""
        streamer = StreamerBase(kick_user_id="12345", username="test")
        assert streamer.status == StreamerStatus.UNKNOWN
        assert streamer.is_active is True
        assert streamer.display_name is None
        assert streamer.last_seen_online is None
        assert streamer.last_status_update is None


class TestStreamerCreate:
    """Test StreamerCreate model."""
    
    def test_streamer_create_inherits_validation(self):
        """Test that StreamerCreate inherits all validation from StreamerBase."""
        # Should accept valid data
        streamer = StreamerCreate(
            kick_user_id="12345",
            username="teststreamer",
            display_name="Test Streamer"
        )
        assert streamer.username == "teststreamer"
        
        # Should reject invalid data
        with pytest.raises(ValidationError):
            StreamerCreate(kick_user_id="", username="test")


class TestStreamerUpdate:
    """Test StreamerUpdate model."""
    
    def test_streamer_update_all_optional(self):
        """Test that all fields in StreamerUpdate are optional."""
        # Should work with no fields
        update = StreamerUpdate()
        assert update.display_name is None
        assert update.status is None
        assert update.last_seen_online is None
        assert update.last_status_update is None
        assert update.is_active is None
    
    def test_streamer_update_partial_data(self):
        """Test StreamerUpdate with partial data."""
        update = StreamerUpdate(
            display_name="New Display Name",
            status=StreamerStatus.ONLINE
        )
        assert update.display_name == "New Display Name"
        assert update.status == StreamerStatus.ONLINE
        assert update.last_seen_online is None
    
    def test_streamer_update_timestamp_validation(self):
        """Test timestamp validation in updates."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        
        with pytest.raises(ValidationError):
            StreamerUpdate(last_seen_online=future_time)
        
        with pytest.raises(ValidationError):
            StreamerUpdate(last_status_update=future_time)


class TestStreamerStatusUpdate:
    """Test StreamerStatusUpdate model."""
    
    def test_status_update_creation(self):
        """Test creating status update."""
        update = StreamerStatusUpdate(
            new_status=StreamerStatus.ONLINE,
            previous_status=StreamerStatus.OFFLINE
        )
        assert update.new_status == StreamerStatus.ONLINE
        assert update.previous_status == StreamerStatus.OFFLINE
        assert update.timestamp is not None
    
    def test_status_update_auto_timestamp(self):
        """Test automatic timestamp assignment."""
        before_creation = datetime.now(timezone.utc)
        update = StreamerStatusUpdate(new_status=StreamerStatus.ONLINE)
        after_creation = datetime.now(timezone.utc)
        
        assert before_creation <= update.timestamp <= after_creation
    
    def test_status_update_transition_validation(self):
        """Test status transition validation."""
        # Valid transition
        update = StreamerStatusUpdate(
            new_status=StreamerStatus.ONLINE,
            previous_status=StreamerStatus.OFFLINE
        )
        assert update.new_status == StreamerStatus.ONLINE
        
        # Invalid transition
        with pytest.raises(ValidationError) as exc_info:
            StreamerStatusUpdate(
                new_status=StreamerStatus.UNKNOWN,
                previous_status=StreamerStatus.ONLINE
            )
        assert "Invalid status transition" in str(exc_info.value)
    
    def test_status_update_no_previous_status(self):
        """Test status update without previous status (initial state)."""
        update = StreamerStatusUpdate(new_status=StreamerStatus.ONLINE)
        assert update.new_status == StreamerStatus.ONLINE
        assert update.previous_status is None
    
    def test_status_update_future_timestamp_rejected(self):
        """Test that future timestamps are rejected."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        
        with pytest.raises(ValidationError) as exc_info:
            StreamerStatusUpdate(
                new_status=StreamerStatus.ONLINE,
                timestamp=future_time
            )
        assert "cannot be in the future" in str(exc_info.value)


class TestStreamer:
    """Test full Streamer model."""
    
    @pytest.fixture
    def valid_streamer_data(self):
        """Valid streamer data for testing."""
        return {
            "kick_user_id": "12345",
            "username": "teststreamer",
            "display_name": "Test Streamer",
            "status": StreamerStatus.OFFLINE
        }
    
    def test_streamer_creation(self, valid_streamer_data):
        """Test creating complete Streamer model."""
        streamer = Streamer(**valid_streamer_data)
        assert streamer.kick_user_id == "12345"
        assert streamer.username == "teststreamer"
        assert streamer.status == StreamerStatus.OFFLINE
        assert streamer.id is None  # Database ID not set yet
        assert streamer.created_at is None
        assert streamer.updated_at is None
    
    def test_streamer_with_database_fields(self, valid_streamer_data):
        """Test Streamer with database-specific fields."""
        now = datetime.now(timezone.utc)
        streamer_data = {
            **valid_streamer_data,
            "id": 123,
            "created_at": now,
            "updated_at": now
        }
        
        streamer = Streamer(**streamer_data)
        assert streamer.id == 123
        assert streamer.created_at == now
        assert streamer.updated_at == now
    
    def test_update_status_valid_transition(self, valid_streamer_data):
        """Test updating status with valid transition."""
        streamer = Streamer(**valid_streamer_data)
        assert streamer.status == StreamerStatus.OFFLINE
        
        # Update to ONLINE (valid from OFFLINE)
        updated_streamer = streamer.update_status(StreamerStatus.ONLINE)
        
        assert updated_streamer.status == StreamerStatus.ONLINE
        assert updated_streamer.last_seen_online is not None
        assert updated_streamer.last_status_update is not None
        assert updated_streamer.updated_at is not None
        
        # Original streamer unchanged
        assert streamer.status == StreamerStatus.OFFLINE
    
    def test_update_status_invalid_transition(self, valid_streamer_data):
        """Test updating status with invalid transition."""
        streamer_data = {**valid_streamer_data, "status": StreamerStatus.ONLINE}
        streamer = Streamer(**streamer_data)
        
        # Try to transition ONLINE -> UNKNOWN (invalid)
        with pytest.raises(ValueError) as exc_info:
            streamer.update_status(StreamerStatus.UNKNOWN)
        
        assert "Invalid status transition" in str(exc_info.value)
        assert "online" in str(exc_info.value)
        assert "unknown" in str(exc_info.value)
    
    def test_update_status_with_custom_timestamp(self, valid_streamer_data):
        """Test updating status with custom timestamp."""
        streamer = Streamer(**valid_streamer_data)
        custom_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        
        updated_streamer = streamer.update_status(
            StreamerStatus.ONLINE, 
            timestamp=custom_time
        )
        
        assert updated_streamer.last_status_update == custom_time
        assert updated_streamer.last_seen_online == custom_time
    
    def test_is_status_transition_valid(self, valid_streamer_data):
        """Test status transition validation method."""
        streamer = Streamer(**valid_streamer_data)  # OFFLINE
        
        assert streamer.is_status_transition_valid(StreamerStatus.ONLINE) is True
        assert streamer.is_status_transition_valid(StreamerStatus.OFFLINE) is False
        assert streamer.is_status_transition_valid(StreamerStatus.UNKNOWN) is False
    
    def test_get_valid_status_transitions(self, valid_streamer_data):
        """Test getting valid status transitions."""
        # OFFLINE streamer
        streamer = Streamer(**valid_streamer_data)
        transitions = streamer.get_valid_status_transitions()
        assert transitions == [StreamerStatus.ONLINE]
        
        # ONLINE streamer
        streamer_data = {**valid_streamer_data, "status": StreamerStatus.ONLINE}
        streamer = Streamer(**streamer_data)
        transitions = streamer.get_valid_status_transitions()
        assert transitions == [StreamerStatus.OFFLINE]
    
    def test_to_dict(self, valid_streamer_data):
        """Test converting streamer to dictionary."""
        streamer = Streamer(**valid_streamer_data)
        data_dict = streamer.to_dict()
        
        assert isinstance(data_dict, dict)
        assert data_dict["kick_user_id"] == "12345"
        assert data_dict["username"] == "teststreamer"
        assert data_dict["status"] == StreamerStatus.OFFLINE
    
    def test_streamer_repr(self, valid_streamer_data):
        """Test string representation of streamer."""
        streamer = Streamer(**valid_streamer_data)
        repr_str = repr(streamer)
        
        assert "Streamer" in repr_str
        assert "teststreamer" in repr_str
        assert "offline" in repr_str.lower()
    
    def test_json_encoding(self, valid_streamer_data):
        """Test JSON encoding configuration."""
        now = datetime.now(timezone.utc)
        streamer_data = {
            **valid_streamer_data,
            "created_at": now,
            "updated_at": now
        }
        
        streamer = Streamer(**streamer_data)
        json_data = streamer.json()
        
        assert isinstance(json_data, str)
        assert now.isoformat() in json_data


class TestStatusEvent:
    """Test StatusEvent model."""
    
    @pytest.fixture
    def valid_event_data(self):
        """Valid status event data for testing."""
        return {
            "streamer_id": 123,
            "event_type": EventType.STREAM_START,
            "timestamp": datetime.now(timezone.utc),
            "details": {"test": "data"}
        }
    
    def test_status_event_creation(self, valid_event_data):
        """Test creating StatusEvent."""
        event = StatusEvent(**valid_event_data)
        assert event.streamer_id == 123
        assert event.event_type == EventType.STREAM_START
        assert event.details == {"test": "data"}
    
    def test_status_event_required_fields(self):
        """Test StatusEvent required field validation."""
        # Missing streamer_id
        with pytest.raises(ValidationError):
            StatusEvent(event_type=EventType.STREAM_START)
        
        # Missing event_type
        with pytest.raises(ValidationError):
            StatusEvent(streamer_id=123)


class TestStatusEventCreate:
    """Test StatusEventCreate model."""
    
    def test_status_event_create(self):
        """Test creating StatusEventCreate."""
        event = StatusEventCreate(
            streamer_id=123,
            event_type=EventType.STREAM_END,
            details={"duration": 3600}
        )
        assert event.streamer_id == 123
        assert event.event_type == EventType.STREAM_END
        assert event.details == {"duration": 3600}


class TestConfiguration:
    """Test Configuration model."""
    
    @pytest.fixture
    def valid_config_data(self):
        """Valid configuration data for testing."""
        return {
            "key": "test_setting",
            "value": "test_value",
            "category": ConfigCategory.SYSTEM,
            "type": ConfigurationType.STRING,
            "description": "Test configuration setting"
        }
    
    def test_configuration_creation(self, valid_config_data):
        """Test creating Configuration."""
        config = Configuration(**valid_config_data)
        assert config.key == "test_setting"
        assert config.value == "test_value"
        assert config.category == ConfigCategory.SYSTEM
        assert config.type == ConfigurationType.STRING
        assert config.description == "Test configuration setting"
    
    def test_configuration_required_fields(self):
        """Test Configuration required field validation."""
        # Missing key
        with pytest.raises(ValidationError):
            Configuration(
                value="test",
                category=ConfigCategory.SYSTEM,
                type=ConfigurationType.STRING
            )
        
        # Missing value
        with pytest.raises(ValidationError):
            Configuration(
                key="test_key",
                category=ConfigCategory.SYSTEM,
                type=ConfigurationType.STRING
            )


class TestConfigurationCreate:
    """Test ConfigurationCreate model."""
    
    def test_configuration_create(self):
        """Test creating ConfigurationCreate."""
        config = ConfigurationCreate(
            key="new_setting",
            value="new_value",
            category=ConfigCategory.AUTHENTICATION,
            type=ConfigurationType.STRING
        )
        assert config.key == "new_setting"
        assert config.category == ConfigCategory.AUTHENTICATION


class TestConfigurationUpdate:
    """Test ConfigurationUpdate model."""
    
    def test_configuration_update_all_optional(self):
        """Test that all fields in ConfigurationUpdate are optional."""
        update = ConfigurationUpdate()
        assert update.value is None
        assert update.description is None
        assert update.is_encrypted is None
    
    def test_configuration_update_partial(self):
        """Test ConfigurationUpdate with partial data."""
        update = ConfigurationUpdate(
            value="updated_value",
            description="Updated description"
        )
        assert update.value == "updated_value"
        assert update.description == "Updated description"
        assert update.is_encrypted is None