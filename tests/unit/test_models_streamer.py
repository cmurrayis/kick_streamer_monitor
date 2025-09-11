"""
Unit tests for streamer models and validation.

Tests cover all aspects of the streamer model including validation,
state transitions, and business logic.
"""

import pytest
from datetime import datetime, timezone, timedelta
from pydantic import ValidationError

from src.models.streamer import (
    Streamer,
    StreamerCreate,
    StreamerUpdate,
    StreamerStatusUpdate,
    StreamerStatus,
    StreamerStateTransition,
)


class TestStreamerStatus:
    """Test StreamerStatus enum."""
    
    def test_status_values(self):
        """Test that all status values are correct."""
        assert StreamerStatus.ONLINE == "online"
        assert StreamerStatus.OFFLINE == "offline"
        assert StreamerStatus.UNKNOWN == "unknown"


class TestStreamerStateTransition:
    """Test StreamerStateTransition logic."""
    
    def test_valid_transitions_from_unknown(self):
        """Test valid transitions from unknown status."""
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.UNKNOWN, StreamerStatus.ONLINE
        )
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.UNKNOWN, StreamerStatus.OFFLINE
        )
        assert not StreamerStateTransition.is_valid_transition(
            StreamerStatus.UNKNOWN, StreamerStatus.UNKNOWN
        )
    
    def test_valid_transitions_from_online(self):
        """Test valid transitions from online status."""
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.ONLINE, StreamerStatus.OFFLINE
        )
        assert not StreamerStateTransition.is_valid_transition(
            StreamerStatus.ONLINE, StreamerStatus.ONLINE
        )
        assert not StreamerStateTransition.is_valid_transition(
            StreamerStatus.ONLINE, StreamerStatus.UNKNOWN
        )
    
    def test_valid_transitions_from_offline(self):
        """Test valid transitions from offline status."""
        assert StreamerStateTransition.is_valid_transition(
            StreamerStatus.OFFLINE, StreamerStatus.ONLINE
        )
        assert not StreamerStateTransition.is_valid_transition(
            StreamerStatus.OFFLINE, StreamerStatus.OFFLINE
        )
        assert not StreamerStateTransition.is_valid_transition(
            StreamerStatus.OFFLINE, StreamerStatus.UNKNOWN
        )
    
    def test_get_valid_transitions(self):
        """Test getting valid transitions for each status."""
        unknown_transitions = StreamerStateTransition.get_valid_transitions(
            StreamerStatus.UNKNOWN
        )
        assert StreamerStatus.ONLINE in unknown_transitions
        assert StreamerStatus.OFFLINE in unknown_transitions
        
        online_transitions = StreamerStateTransition.get_valid_transitions(
            StreamerStatus.ONLINE
        )
        assert online_transitions == [StreamerStatus.OFFLINE]
        
        offline_transitions = StreamerStateTransition.get_valid_transitions(
            StreamerStatus.OFFLINE
        )
        assert offline_transitions == [StreamerStatus.ONLINE]


class TestStreamerCreate:
    """Test StreamerCreate model validation."""
    
    def test_valid_creation(self):
        """Test creating a valid streamer."""
        streamer = StreamerCreate(
            kick_user_id="12345",
            username="testuser",
            display_name="Test User"
        )
        assert streamer.kick_user_id == "12345"
        assert streamer.username == "testuser"
        assert streamer.display_name == "Test User"
        assert streamer.status == StreamerStatus.UNKNOWN
        assert streamer.is_active is True
    
    def test_required_fields(self):
        """Test that required fields are validated."""
        with pytest.raises(ValidationError) as exc_info:
            StreamerCreate()
        
        error_fields = {error['loc'][0] for error in exc_info.value.errors}
        assert 'kick_user_id' in error_fields
        assert 'username' in error_fields
    
    def test_username_validation(self):
        """Test username validation rules."""
        # Empty username
        with pytest.raises(ValidationError) as exc_info:
            StreamerCreate(kick_user_id="12345", username="")
        assert "Username cannot be empty" in str(exc_info.value)
        
        # Whitespace username
        with pytest.raises(ValidationError) as exc_info:
            StreamerCreate(kick_user_id="12345", username="   ")
        assert "Username cannot be empty" in str(exc_info.value)
        
        # Valid username with whitespace should be trimmed
        streamer = StreamerCreate(kick_user_id="12345", username="  testuser  ")
        assert streamer.username == "testuser"
    
    def test_kick_user_id_validation(self):
        """Test kick_user_id validation rules."""
        # Empty kick_user_id
        with pytest.raises(ValidationError) as exc_info:
            StreamerCreate(kick_user_id="", username="testuser")
        assert "Kick user ID cannot be empty" in str(exc_info.value)
        
        # Whitespace kick_user_id
        with pytest.raises(ValidationError) as exc_info:
            StreamerCreate(kick_user_id="   ", username="testuser")
        assert "Kick user ID cannot be empty" in str(exc_info.value)
        
        # Valid kick_user_id with whitespace should be trimmed
        streamer = StreamerCreate(kick_user_id="  12345  ", username="testuser")
        assert streamer.kick_user_id == "12345"
    
    def test_timestamp_validation(self):
        """Test timestamp validation."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        
        # Future last_seen_online
        with pytest.raises(ValidationError) as exc_info:
            StreamerCreate(
                kick_user_id="12345",
                username="testuser",
                last_seen_online=future_time
            )
        assert "cannot be in the future" in str(exc_info.value)
        
        # Future last_status_update
        with pytest.raises(ValidationError) as exc_info:
            StreamerCreate(
                kick_user_id="12345",
                username="testuser",
                last_status_update=future_time
            )
        assert "cannot be in the future" in str(exc_info.value)
    
    def test_status_consistency_validation(self):
        """Test status and timestamp consistency."""
        # Online status should set last_seen_online if not provided
        streamer = StreamerCreate(
            kick_user_id="12345",
            username="testuser",
            status=StreamerStatus.ONLINE
        )
        assert streamer.last_seen_online is not None
        assert isinstance(streamer.last_seen_online, datetime)


class TestStreamerUpdate:
    """Test StreamerUpdate model validation."""
    
    def test_partial_update(self):
        """Test partial update with optional fields."""
        update = StreamerUpdate(display_name="New Display Name")
        assert update.display_name == "New Display Name"
        assert update.status is None
        assert update.is_active is None
    
    def test_timestamp_validation_in_update(self):
        """Test timestamp validation in updates."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        
        with pytest.raises(ValidationError) as exc_info:
            StreamerUpdate(last_seen_online=future_time)
        assert "cannot be in the future" in str(exc_info.value)


class TestStreamerStatusUpdate:
    """Test StreamerStatusUpdate model validation."""
    
    def test_valid_status_update(self):
        """Test valid status update."""
        update = StreamerStatusUpdate(
            new_status=StreamerStatus.ONLINE,
            previous_status=StreamerStatus.OFFLINE
        )
        assert update.new_status == StreamerStatus.ONLINE
        assert update.previous_status == StreamerStatus.OFFLINE
        assert isinstance(update.timestamp, datetime)
    
    def test_invalid_status_transition(self):
        """Test invalid status transition validation."""
        with pytest.raises(ValidationError) as exc_info:
            StreamerStatusUpdate(
                new_status=StreamerStatus.UNKNOWN,
                previous_status=StreamerStatus.ONLINE
            )
        assert "Invalid status transition" in str(exc_info.value)
    
    def test_timestamp_defaults_to_now(self):
        """Test that timestamp defaults to current time."""
        before = datetime.now(timezone.utc)
        update = StreamerStatusUpdate(new_status=StreamerStatus.ONLINE)
        after = datetime.now(timezone.utc)
        
        assert before <= update.timestamp <= after
    
    def test_future_timestamp_validation(self):
        """Test that future timestamps are rejected."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        
        with pytest.raises(ValidationError) as exc_info:
            StreamerStatusUpdate(
                new_status=StreamerStatus.ONLINE,
                timestamp=future_time
            )
        assert "cannot be in the future" in str(exc_info.value)


class TestStreamer:
    """Test full Streamer model functionality."""
    
    def test_complete_streamer_creation(self):
        """Test creating a complete streamer instance."""
        now = datetime.now(timezone.utc)
        streamer = Streamer(
            id=1,
            kick_user_id="12345",
            username="testuser",
            display_name="Test User",
            status=StreamerStatus.ONLINE,
            last_seen_online=now,
            created_at=now,
            updated_at=now
        )
        
        assert streamer.id == 1
        assert streamer.kick_user_id == "12345"
        assert streamer.username == "testuser"
        assert streamer.status == StreamerStatus.ONLINE
    
    def test_update_status_valid_transition(self):
        """Test valid status update."""
        streamer = Streamer(
            kick_user_id="12345",
            username="testuser",
            status=StreamerStatus.OFFLINE
        )
        
        updated = streamer.update_status(StreamerStatus.ONLINE)
        
        assert updated.status == StreamerStatus.ONLINE
        assert updated.last_seen_online is not None
        assert updated.last_status_update is not None
        assert updated.updated_at is not None
        assert updated.kick_user_id == "12345"  # Other fields preserved
    
    def test_update_status_invalid_transition(self):
        """Test invalid status update."""
        streamer = Streamer(
            kick_user_id="12345",
            username="testuser",
            status=StreamerStatus.ONLINE
        )
        
        with pytest.raises(ValueError) as exc_info:
            streamer.update_status(StreamerStatus.UNKNOWN)
        assert "Invalid status transition" in str(exc_info.value)
    
    def test_update_status_with_timestamp(self):
        """Test status update with custom timestamp."""
        custom_time = datetime.now(timezone.utc) - timedelta(hours=1)
        streamer = Streamer(
            kick_user_id="12345",
            username="testuser",
            status=StreamerStatus.OFFLINE
        )
        
        updated = streamer.update_status(StreamerStatus.ONLINE, custom_time)
        
        assert updated.last_status_update == custom_time
        assert updated.updated_at == custom_time
        assert updated.last_seen_online == custom_time
    
    def test_is_status_transition_valid(self):
        """Test status transition validation method."""
        streamer = Streamer(
            kick_user_id="12345",
            username="testuser",
            status=StreamerStatus.OFFLINE
        )
        
        assert streamer.is_status_transition_valid(StreamerStatus.ONLINE)
        assert not streamer.is_status_transition_valid(StreamerStatus.UNKNOWN)
    
    def test_get_valid_status_transitions(self):
        """Test getting valid transitions for current status."""
        streamer = Streamer(
            kick_user_id="12345",
            username="testuser",
            status=StreamerStatus.OFFLINE
        )
        
        valid_transitions = streamer.get_valid_status_transitions()
        assert valid_transitions == [StreamerStatus.ONLINE]
    
    def test_to_dict(self):
        """Test dictionary conversion."""
        streamer = Streamer(
            kick_user_id="12345",
            username="testuser",
            display_name="Test User"
        )
        
        data = streamer.to_dict()
        assert data['kick_user_id'] == "12345"
        assert data['username'] == "testuser"
        assert data['display_name'] == "Test User"
    
    def test_string_representation(self):
        """Test string representation."""
        streamer = Streamer(
            id=1,
            kick_user_id="12345",
            username="testuser",
            status=StreamerStatus.ONLINE
        )
        
        repr_str = repr(streamer)
        assert "Streamer" in repr_str
        assert "id=1" in repr_str
        assert "username='testuser'" in repr_str
        assert "status=StreamerStatus.ONLINE" in repr_str
    
    def test_json_encoding(self):
        """Test JSON encoding with datetime fields."""
        now = datetime.now(timezone.utc)
        streamer = Streamer(
            kick_user_id="12345",
            username="testuser",
            created_at=now
        )
        
        # Test that it can be converted to JSON
        json_data = streamer.json()
        assert json_data is not None
        assert now.isoformat() in json_data


@pytest.fixture
def sample_streamer():
    """Fixture providing a sample streamer for testing."""
    return Streamer(
        id=1,
        kick_user_id="12345",
        username="teststreamer",
        display_name="Test Streamer",
        status=StreamerStatus.OFFLINE,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )


class TestStreamerFixture:
    """Test using the streamer fixture."""
    
    def test_fixture_usage(self, sample_streamer):
        """Test that fixture provides valid streamer."""
        assert sample_streamer.username == "teststreamer"
        assert sample_streamer.status == StreamerStatus.OFFLINE
        assert sample_streamer.is_active is True
    
    def test_fixture_immutability(self, sample_streamer):
        """Test that fixture modifications don't affect other tests."""
        original_username = sample_streamer.username
        updated = sample_streamer.update_status(StreamerStatus.ONLINE)
        
        # Original should be unchanged
        assert sample_streamer.username == original_username
        assert sample_streamer.status == StreamerStatus.OFFLINE
        
        # Updated should be different
        assert updated.status == StreamerStatus.ONLINE