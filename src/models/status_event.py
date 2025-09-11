"""
StatusEvent data model for tracking real-time status change notifications.

Represents events received from Kick.com WebSocket service indicating
streamer online/offline status changes.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, Union
from pydantic import BaseModel, Field, validator, root_validator
import json

from .streamer import StreamerStatus


class EventType(str, Enum):
    """Valid event types for status changes."""
    STREAM_START = "stream_start"
    STREAM_END = "stream_end"
    CONNECTION_TEST = "connection_test"


class StatusEventBase(BaseModel):
    """Base model for StatusEvent with common validation."""
    
    streamer_id: int = Field(..., description="Reference to Streamer entity")
    event_type: EventType = Field(..., description="Type of status change")
    previous_status: StreamerStatus = Field(..., description="Status before the change")
    new_status: StreamerStatus = Field(..., description="Status after the change")
    kick_event_id: Optional[str] = Field(None, description="Kick.com's internal event identifier")
    event_timestamp: datetime = Field(..., description="When the event occurred on Kick.com")
    received_timestamp: Optional[datetime] = Field(None, description="When our service received the event")
    processed_timestamp: Optional[datetime] = Field(None, description="When the database was updated")
    event_data: Optional[Dict[str, Any]] = Field(None, description="Raw event data from Kick.com")
    
    @validator('event_timestamp')
    def validate_event_timestamp(cls, v):
        """Validate event_timestamp is not in the future."""
        if v > datetime.now(timezone.utc):
            raise ValueError("Event timestamp cannot be in the future")
        return v
    
    @validator('received_timestamp')
    def validate_received_timestamp(cls, v):
        """Set received_timestamp to now if not provided."""
        return v or datetime.now(timezone.utc)
    
    @validator('kick_event_id')
    def validate_kick_event_id(cls, v):
        """Validate kick_event_id format if provided."""
        if v is not None and not isinstance(v, str):
            raise ValueError("Kick event ID must be a string")
        return v
    
    @root_validator
    def validate_timestamp_order(cls, values):
        """Validate timestamp ordering constraints."""
        event_ts = values.get('event_timestamp')
        received_ts = values.get('received_timestamp')
        processed_ts = values.get('processed_timestamp')
        
        if received_ts and event_ts and received_ts < event_ts:
            raise ValueError("Received timestamp cannot be before event timestamp")
        
        if processed_ts and received_ts and processed_ts < received_ts:
            raise ValueError("Processed timestamp cannot be before received timestamp")
        
        if processed_ts and event_ts and processed_ts < event_ts:
            raise ValueError("Processed timestamp cannot be before event timestamp")
        
        return values
    
    @root_validator
    def validate_status_event_consistency(cls, values):
        """Validate event type matches status change."""
        event_type = values.get('event_type')
        previous_status = values.get('previous_status')
        new_status = values.get('new_status')
        
        if event_type == EventType.STREAM_START:
            if new_status != StreamerStatus.ONLINE:
                raise ValueError("Stream start events must result in online status")
            if previous_status == StreamerStatus.ONLINE:
                raise ValueError("Stream start events cannot transition from online status")
        
        elif event_type == EventType.STREAM_END:
            if new_status != StreamerStatus.OFFLINE:
                raise ValueError("Stream end events must result in offline status")
            if previous_status == StreamerStatus.OFFLINE:
                raise ValueError("Stream end events cannot transition from offline status")
        
        elif event_type == EventType.CONNECTION_TEST:
            # Connection test events can have any status transition
            pass
        
        return values


class StatusEventCreate(StatusEventBase):
    """Model for creating a new status event record."""
    
    @validator('received_timestamp', pre=True, always=True)
    def set_received_timestamp(cls, v):
        """Set received timestamp to current time if not provided."""
        return v or datetime.now(timezone.utc)


class StatusEventUpdate(BaseModel):
    """Model for updating status event (limited updates allowed)."""
    
    processed_timestamp: Optional[datetime] = Field(None, description="When processing completed")
    event_data: Optional[Dict[str, Any]] = Field(None, description="Updated event data")
    
    @validator('processed_timestamp')
    def validate_processed_timestamp(cls, v):
        """Validate processed timestamp."""
        if v and v > datetime.now(timezone.utc):
            raise ValueError("Processed timestamp cannot be in the future")
        return v


class StatusEvent(StatusEventBase):
    """Complete status event model with database fields."""
    
    id: Optional[int] = Field(None, description="Database primary key")
    created_at: Optional[datetime] = Field(None, description="Record creation timestamp")
    
    class Config:
        """Pydantic configuration."""
        orm_mode = True
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }
    
    def mark_processed(self, timestamp: Optional[datetime] = None) -> 'StatusEvent':
        """
        Mark the event as processed.
        
        Args:
            timestamp: When processing completed (defaults to now)
            
        Returns:
            Updated event instance
        """
        processed_time = timestamp or datetime.now(timezone.utc)
        
        # Validate timestamp ordering
        if self.received_timestamp and processed_time < self.received_timestamp:
            raise ValueError("Processed timestamp cannot be before received timestamp")
        
        if processed_time < self.event_timestamp:
            raise ValueError("Processed timestamp cannot be before event timestamp")
        
        updated_data = self.dict()
        updated_data['processed_timestamp'] = processed_time
        
        return self.__class__(**updated_data)
    
    def add_event_data(self, data: Dict[str, Any]) -> 'StatusEvent':
        """
        Add or update event data.
        
        Args:
            data: Additional event data to store
            
        Returns:
            Updated event instance
        """
        updated_data = self.dict()
        current_data = updated_data.get('event_data') or {}
        current_data.update(data)
        updated_data['event_data'] = current_data
        
        return self.__class__(**updated_data)
    
    def get_processing_latency(self) -> Optional[float]:
        """
        Calculate processing latency in seconds.
        
        Returns:
            Seconds between received and processed timestamps, or None
        """
        if not self.processed_timestamp or not self.received_timestamp:
            return None
        
        delta = self.processed_timestamp - self.received_timestamp
        return delta.total_seconds()
    
    def get_event_latency(self) -> Optional[float]:
        """
        Calculate event delivery latency in seconds.
        
        Returns:
            Seconds between event occurrence and receipt, or None
        """
        if not self.received_timestamp:
            return None
        
        delta = self.received_timestamp - self.event_timestamp
        return delta.total_seconds()
    
    def get_total_latency(self) -> Optional[float]:
        """
        Calculate total end-to-end latency in seconds.
        
        Returns:
            Seconds between event occurrence and processing completion, or None
        """
        if not self.processed_timestamp:
            return None
        
        delta = self.processed_timestamp - self.event_timestamp
        return delta.total_seconds()
    
    def is_processed(self) -> bool:
        """Check if event has been processed."""
        return self.processed_timestamp is not None
    
    def is_duplicate_candidate(self, other: 'StatusEvent') -> bool:
        """
        Check if this event might be a duplicate of another.
        
        Args:
            other: Other event to compare against
            
        Returns:
            True if events appear to be duplicates
        """
        if self.streamer_id != other.streamer_id:
            return False
        
        if self.event_type != other.event_type:
            return False
        
        if self.kick_event_id and other.kick_event_id:
            return self.kick_event_id == other.kick_event_id
        
        # Check timestamp similarity (within 5 seconds)
        time_diff = abs((self.event_timestamp - other.event_timestamp).total_seconds())
        return time_diff <= 5.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        data = self.dict(exclude_unset=True)
        
        # Convert datetime objects to ISO format
        for field in ['event_timestamp', 'received_timestamp', 'processed_timestamp', 'created_at']:
            if field in data and data[field]:
                data[field] = data[field].isoformat()
        
        return data
    
    def to_json(self) -> str:
        """Convert to JSON string representation."""
        return json.dumps(self.to_dict(), indent=2)
    
    def __repr__(self) -> str:
        """String representation of status event."""
        return (
            f"<StatusEvent(id={self.id}, streamer_id={self.streamer_id}, "
            f"type={self.event_type}, {self.previous_status}->{self.new_status})>"
        )


class StatusEventQuery(BaseModel):
    """Model for querying status events with filters."""
    
    streamer_id: Optional[int] = None
    event_type: Optional[EventType] = None
    status_from: Optional[StreamerStatus] = None
    status_to: Optional[StreamerStatus] = None
    after_timestamp: Optional[datetime] = None
    before_timestamp: Optional[datetime] = None
    processed_only: Optional[bool] = None
    limit: Optional[int] = Field(100, ge=1, le=1000)
    offset: Optional[int] = Field(0, ge=0)
    
    @validator('limit')
    def validate_limit(cls, v):
        """Validate query limit."""
        if v is not None and (v < 1 or v > 1000):
            raise ValueError("Limit must be between 1 and 1000")
        return v
    
    @validator('offset')
    def validate_offset(cls, v):
        """Validate query offset."""
        if v is not None and v < 0:
            raise ValueError("Offset must be non-negative")
        return v
    
    @root_validator
    def validate_timestamp_range(cls, values):
        """Validate timestamp range if both provided."""
        after = values.get('after_timestamp')
        before = values.get('before_timestamp')
        
        if after and before and after >= before:
            raise ValueError("after_timestamp must be before before_timestamp")
        
        return values