"""
User account models for the web dashboard system.

Defines user accounts, roles, and streamer assignments for multi-tenant monitoring.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class UserRole(str, Enum):
    """User role enumeration."""
    ADMIN = "admin"      # Full system access
    USER = "user"        # Limited to assigned streamers
    VIEWER = "viewer"    # Read-only access to assigned streamers


class UserStatus(str, Enum):
    """User account status."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"


class User(BaseModel):
    """User account model."""
    id: Optional[int] = None
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=5, max_length=255)
    display_name: Optional[str] = Field(None, max_length=100)
    role: UserRole = UserRole.USER
    status: UserStatus = UserStatus.ACTIVE
    password_hash: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    """User creation model."""
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: Optional[str] = Field(None, max_length=100)
    role: UserRole = UserRole.USER


class UserUpdate(BaseModel):
    """User update model."""
    email: Optional[str] = Field(None, min_length=5, max_length=255)
    display_name: Optional[str] = Field(None, max_length=100)
    role: Optional[UserRole] = None
    status: Optional[UserStatus] = None


class UserStreamerAssignment(BaseModel):
    """User-to-streamer assignment model."""
    id: Optional[int] = None
    user_id: int
    streamer_id: int
    assigned_at: Optional[datetime] = None
    assigned_by: Optional[int] = None  # Admin user ID who made assignment
    
    class Config:
        from_attributes = True


class UserStreamerAssignmentCreate(BaseModel):
    """Create user-streamer assignment."""
    user_id: int
    streamer_id: int


class UserSession(BaseModel):
    """User session information."""
    user_id: int
    username: str
    role: UserRole
    display_name: Optional[str]
    assigned_streamers: List[int] = []  # List of streamer IDs
    login_time: datetime
    last_activity: datetime