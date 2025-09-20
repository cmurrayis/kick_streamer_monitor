"""
Streamer data model with validation and state transitions.

Represents a Kick.com content creator being monitored for online/offline status changes.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, validator, model_validator


class StreamerStatus(str, Enum):
    """Valid streamer online/offline status values."""
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class StreamerStateTransition:
    """Manages valid state transitions for streamer status."""
    
    VALID_TRANSITIONS = {
        StreamerStatus.UNKNOWN: [StreamerStatus.ONLINE, StreamerStatus.OFFLINE],
        StreamerStatus.ONLINE: [StreamerStatus.OFFLINE],
        StreamerStatus.OFFLINE: [StreamerStatus.ONLINE],
    }
    
    @classmethod
    def is_valid_transition(cls, from_status: StreamerStatus, to_status: StreamerStatus) -> bool:
        """Check if status transition is valid."""
        return to_status in cls.VALID_TRANSITIONS.get(from_status, [])
    
    @classmethod
    def get_valid_transitions(cls, from_status: StreamerStatus) -> list[StreamerStatus]:
        """Get list of valid transitions from current status."""
        return cls.VALID_TRANSITIONS.get(from_status, [])


class StreamerBase(BaseModel):
    """Base model for Streamer with common validation."""
    
    kick_user_id: str = Field(..., description="Kick.com's internal user ID")
    username: str = Field(..., description="Kick.com username", min_length=1)
    display_name: Optional[str] = Field(None, description="Human-readable display name")
    status: StreamerStatus = Field(StreamerStatus.UNKNOWN, description="Current online/offline status")
    profile_picture_url: Optional[str] = Field(None, description="URL to streamer's profile picture")
    bio: Optional[str] = Field(None, description="Streamer's bio/description")
    follower_count: Optional[int] = Field(0, description="Number of followers")
    is_live: Optional[bool] = Field(False, description="Current live status")
    is_verified: Optional[bool] = Field(False, description="Whether the streamer is verified")
    last_seen_online: Optional[datetime] = Field(None, description="Timestamp of last online detection")
    last_status_update: Optional[datetime] = Field(None, description="Timestamp of most recent status change")
    is_active: bool = Field(True, description="Whether monitoring is enabled")
    current_viewers: Optional[int] = Field(None, description="Current live viewer count")
    peak_viewers: Optional[int] = Field(None, description="Peak viewer count during current/last stream")
    avg_viewers: Optional[int] = Field(None, description="Average viewer count across all streams")
    livestream_id: Optional[int] = Field(None, description="Current livestream ID from Kick API")
    channel_id: Optional[int] = Field(None, description="Channel ID from Kick API")
    
    @validator('username')
    def validate_username(cls, v):
        """Validate username is non-empty and trimmed."""
        if not v or not v.strip():
            raise ValueError("Username cannot be empty")
        return v.strip()
    
    @validator('kick_user_id')
    def validate_kick_user_id(cls, v):
        """Validate kick_user_id is non-empty and trimmed."""
        if not v or not v.strip():
            raise ValueError("Kick user ID cannot be empty")
        return v.strip()
    
    @validator('last_seen_online')
    def validate_last_seen_online(cls, v):
        """Validate last_seen_online is not in the future."""
        if v and v > datetime.now(timezone.utc):
            raise ValueError("Last seen online cannot be in the future")
        return v
    
    @validator('last_status_update')
    def validate_last_status_update(cls, v):
        """Validate last_status_update is not in the future."""
        if v and v > datetime.now(timezone.utc):
            raise ValueError("Last status update cannot be in the future")
        return v

    @validator('current_viewers', 'peak_viewers', 'avg_viewers')
    def validate_viewer_counts(cls, v):
        """Validate viewer counts are non-negative if provided."""
        if v is not None and v < 0:
            raise ValueError("Viewer counts cannot be negative")
        return v

    @validator('livestream_id')
    def validate_livestream_id(cls, v):
        """Validate livestream_id is positive if provided."""
        if v is not None and v <= 0:
            raise ValueError("Livestream ID must be positive")
        return v

    @validator('channel_id')
    def validate_channel_id(cls, v):
        """Validate channel_id is positive if provided."""
        if v is not None and v <= 0:
            raise ValueError("Channel ID must be positive")
        return v

    @model_validator(mode='before')
    @classmethod
    def validate_status_consistency(cls, values):
        """Validate status and timestamp consistency."""
        if isinstance(values, dict):
            status = values.get('status')
            last_seen_online = values.get('last_seen_online')
            
            if status == StreamerStatus.ONLINE and last_seen_online is None:
                values['last_seen_online'] = datetime.now(timezone.utc)
        
        return values


class StreamerCreate(StreamerBase):
    """Model for creating a new streamer record."""
    pass


class StreamerUpdate(BaseModel):
    """Model for updating an existing streamer record."""
    
    display_name: Optional[str] = None
    status: Optional[StreamerStatus] = None
    profile_picture_url: Optional[str] = None
    bio: Optional[str] = None
    follower_count: Optional[int] = None
    is_live: Optional[bool] = None
    is_verified: Optional[bool] = None
    last_seen_online: Optional[datetime] = None
    last_status_update: Optional[datetime] = None
    is_active: Optional[bool] = None
    
    @validator('last_seen_online')
    def validate_last_seen_online(cls, v):
        """Validate last_seen_online is not in the future."""
        if v and v > datetime.now(timezone.utc):
            raise ValueError("Last seen online cannot be in the future")
        return v
    
    @validator('last_status_update')
    def validate_last_status_update(cls, v):
        """Validate last_status_update is not in the future."""
        if v and v > datetime.now(timezone.utc):
            raise ValueError("Last status update cannot be in the future")
        return v


class StreamerStatusUpdate(BaseModel):
    """Model for status-only updates with transition validation."""
    
    new_status: StreamerStatus = Field(..., description="New status to transition to")
    previous_status: Optional[StreamerStatus] = Field(None, description="Previous status for validation")
    timestamp: Optional[datetime] = Field(None, description="When the status change occurred")
    
    @validator('timestamp')
    def validate_timestamp(cls, v):
        """Validate timestamp is not in the future."""
        if v and v > datetime.now(timezone.utc):
            raise ValueError("Status change timestamp cannot be in the future")
        return v or datetime.now(timezone.utc)
    
    @model_validator(mode='before')
    @classmethod
    def validate_status_transition(cls, values):
        """Validate the status transition is allowed."""
        if isinstance(values, dict):
            previous_status = values.get('previous_status')
            new_status = values.get('new_status')
            
            if previous_status and not StreamerStateTransition.is_valid_transition(previous_status, new_status):
                valid_transitions = StreamerStateTransition.get_valid_transitions(previous_status)
                raise ValueError(
                    f"Invalid status transition from {previous_status} to {new_status}. "
                    f"Valid transitions: {[t.value for t in valid_transitions]}"
                )
        
        return values


class Streamer(StreamerBase):
    """Complete streamer model with database fields."""
    
    id: Optional[int] = Field(None, description="Database primary key")
    created_at: Optional[datetime] = Field(None, description="Record creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Record modification timestamp")
    
    class Config:
        """Pydantic configuration."""
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }
    
    def update_status(self, new_status: StreamerStatus, timestamp: Optional[datetime] = None) -> 'Streamer':
        """
        Update streamer status with validation.
        
        Args:
            new_status: New status to set
            timestamp: When the change occurred (defaults to now)
            
        Returns:
            Updated streamer instance
            
        Raises:
            ValueError: If status transition is invalid
        """
        if not StreamerStateTransition.is_valid_transition(self.status, new_status):
            valid_transitions = StreamerStateTransition.get_valid_transitions(self.status)
            raise ValueError(
                f"Invalid status transition from {self.status} to {new_status}. "
                f"Valid transitions: {[t.value for t in valid_transitions]}"
            )
        
        update_time = timestamp or datetime.now(timezone.utc)
        
        # Create updated instance
        updated_data = self.dict()
        updated_data['status'] = new_status
        updated_data['last_status_update'] = update_time
        updated_data['updated_at'] = update_time
        
        if new_status == StreamerStatus.ONLINE:
            updated_data['last_seen_online'] = update_time
        
        return self.__class__(**updated_data)
    
    def is_status_transition_valid(self, new_status: StreamerStatus) -> bool:
        """Check if transitioning to new status would be valid."""
        return StreamerStateTransition.is_valid_transition(self.status, new_status)
    
    def get_valid_status_transitions(self) -> list[StreamerStatus]:
        """Get list of valid status transitions from current status."""
        return StreamerStateTransition.get_valid_transitions(self.status)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return self.dict(exclude_unset=True)
    
    def __repr__(self) -> str:
        """String representation of streamer."""
        return f"<Streamer(id={self.id}, username='{self.username}', status={self.status})>"