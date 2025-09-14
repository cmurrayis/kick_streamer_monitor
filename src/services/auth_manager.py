"""
Authentication and session management for web dashboard.

Provides session-based authentication for admin and user accounts
with secure cookie handling and role-based access control.
"""

import hashlib
import secrets
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple, List

from models.user import User, UserRole, UserStatus, UserSession

logger = logging.getLogger(__name__)


class AuthManager:
    """
    Multi-user session-based authentication manager.
    
    Provides login, logout, and session validation for admin and regular users.
    """
    
    def __init__(self, database_service=None):
        self.database_service = database_service
        
        # Session storage (in production, use Redis or database)
        self._sessions: Dict[str, UserSession] = {}
        
        # Session configuration
        self.session_timeout = timedelta(hours=2)  # 2 hour timeout
        self.max_sessions = 50  # Max concurrent sessions
        
    async def authenticate(self, username: str, password: str) -> Tuple[bool, Optional[str]]:
        """
        Authenticate user credentials and create session.
        
        Returns:
            (success, session_token) tuple
        """
        try:
            # Get user from database
            if not self.database_service:
                logger.error("Database service not available for authentication")
                return False, None
            
            user = await self.database_service.get_user_by_username(username)
            if not user:
                logger.warning(f"Failed login attempt - user not found: {username}")
                return False, None
            
            # Check user status
            if user.status != UserStatus.ACTIVE:
                logger.warning(f"Login attempt for inactive user: {username}")
                return False, None
            
            # Verify password
            if not self._verify_password(password, user.password_hash):
                logger.warning(f"Failed login attempt - wrong password: {username}")
                return False, None
            
            # Get user's assigned streamers
            assigned_streamers = []
            if user.role == UserRole.USER or user.role == UserRole.VIEWER:
                assignments = await self.database_service.get_user_streamer_assignments(user.id)
                assigned_streamers = [a.streamer_id for a in assignments]
            
            # Create session
            session_token = self._create_session(user, assigned_streamers)
            
            # Update last login
            await self.database_service.update_user_last_login(user.id)
            
            logger.info(f"User login successful: {username} ({user.role})")
            return True, session_token
                
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False, None
    
    def validate_session(self, session_token: str) -> Tuple[bool, Optional[UserSession]]:
        """
        Validate session token and return user session.
        
        Returns:
            (valid, user_session) tuple
        """
        try:
            if not session_token or session_token not in self._sessions:
                return False, None
            
            session = self._sessions[session_token]
            
            # Check if session expired
            expires_at = session.login_time + self.session_timeout
            if datetime.now(timezone.utc) > expires_at:
                self._destroy_session(session_token)
                return False, None
            
            # Update last activity
            session.last_activity = datetime.now(timezone.utc)
            
            return True, session
            
        except Exception as e:
            logger.error(f"Session validation error: {e}")
            return False, None
    
    def logout(self, session_token: str) -> bool:
        """
        Logout user and destroy session.
        
        Returns:
            Success status
        """
        try:
            if session_token in self._sessions:
                username = self._sessions[session_token].username
                self._destroy_session(session_token)
                logger.info(f"User logged out: {username}")
                return True
            return False
            
        except Exception as e:
            logger.error(f"Logout error: {e}")
            return False
    
    def cleanup_expired_sessions(self) -> None:
        """Clean up expired sessions."""
        try:
            now = datetime.now(timezone.utc)
            expired_tokens = [
                token for token, session in self._sessions.items()
                if now > (session.login_time + self.session_timeout)
            ]
            
            for token in expired_tokens:
                self._destroy_session(token)
            
            if expired_tokens:
                logger.info(f"Cleaned up {len(expired_tokens)} expired sessions")
                
        except Exception as e:
            logger.error(f"Session cleanup error: {e}")
    
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get list of active sessions (for admin view)."""
        try:
            sessions = []
            for token, session in self._sessions.items():
                sessions.append({
                    'token': token[:8] + '...',  # Partial token for security
                    'username': session.username,
                    'role': session.role.value,
                    'login_time': session.login_time,
                    'last_activity': session.last_activity,
                    'assigned_streamers_count': len(session.assigned_streamers)
                })
            
            return sorted(sessions, key=lambda x: x['last_activity'], reverse=True)
            
        except Exception as e:
            logger.error(f"Error getting active sessions: {e}")
            return []
    
    def _create_session(self, user: User, assigned_streamers: List[int]) -> str:
        """Create new session and return token."""
        # Clean up old sessions if too many
        if len(self._sessions) >= self.max_sessions:
            self.cleanup_expired_sessions()
            
            # If still too many, remove oldest
            if len(self._sessions) >= self.max_sessions:
                oldest_token = min(
                    self._sessions.keys(),
                    key=lambda x: self._sessions[x].last_activity
                )
                self._destroy_session(oldest_token)
        
        # Generate secure session token
        session_token = secrets.token_urlsafe(32)
        
        # Create session
        now = datetime.now(timezone.utc)
        self._sessions[session_token] = UserSession(
            user_id=user.id,
            username=user.username,
            role=user.role,
            display_name=user.display_name,
            assigned_streamers=assigned_streamers,
            login_time=now,
            last_activity=now
        )
        
        return session_token
    
    def _destroy_session(self, session_token: str) -> None:
        """Remove session from storage."""
        if session_token in self._sessions:
            del self._sessions[session_token]
    
    def _hash_password(self, password: str) -> str:
        """Hash password with salt."""
        salt = "kick_monitor_salt"  # In production, use random salt per user
        return hashlib.sha256((password + salt).encode()).hexdigest()
    
    def _verify_password(self, password: str, password_hash: str) -> bool:
        """Verify password against hash."""
        return self._hash_password(password) == password_hash
    
    async def register_user(self, username: str, email: str, password: str, 
                           display_name: Optional[str] = None) -> Tuple[bool, str]:
        """
        Register a new user account.
        
        Returns:
            (success, message) tuple
        """
        try:
            if not self.database_service:
                return False, "Database service not available"
            
            # Check if username exists
            existing_user = await self.database_service.get_user_by_username(username)
            if existing_user:
                return False, "Username already exists"
            
            # Check if email exists
            existing_email = await self.database_service.get_user_by_email(email)
            if existing_email:
                return False, "Email already registered"
            
            # Create user
            from models.user import UserCreate
            user_create = UserCreate(
                username=username,
                email=email,
                password=password,
                display_name=display_name
            )
            
            user = await self.database_service.create_user(user_create)
            if user:
                logger.info(f"New user registered: {username}")
                return True, "Account created successfully"
            else:
                return False, "Failed to create account"
                
        except Exception as e:
            logger.error(f"User registration error: {e}")
            return False, "Registration failed"
    
    @property
    def session_count(self) -> int:
        """Get current number of active sessions."""
        return len(self._sessions)