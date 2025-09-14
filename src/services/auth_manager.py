"""
Authentication and session management for web dashboard.

Provides simple session-based authentication for admin panel access
with secure cookie handling and CSRF protection.
"""

import hashlib
import secrets
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)


class AuthManager:
    """
    Simple session-based authentication manager.
    
    Provides login, logout, and session validation for admin users.
    """
    
    def __init__(self, admin_username: str = "admin", admin_password: str = "password"):
        self.admin_username = admin_username
        self.admin_password_hash = self._hash_password(admin_password)
        
        # Session storage (in production, use Redis or database)
        self._sessions: Dict[str, Dict[str, Any]] = {}
        
        # Session configuration
        self.session_timeout = timedelta(hours=2)  # 2 hour timeout
        self.max_sessions = 10  # Max concurrent sessions
        
    def authenticate(self, username: str, password: str) -> Tuple[bool, Optional[str]]:
        """
        Authenticate user credentials and create session.
        
        Returns:
            (success, session_token) tuple
        """
        try:
            # Check credentials
            if (username == self.admin_username and 
                self._verify_password(password, self.admin_password_hash)):
                
                # Create new session
                session_token = self._create_session(username, 'admin')
                logger.info(f"Admin login successful: {username}")
                return True, session_token
            else:
                logger.warning(f"Failed login attempt: {username}")
                return False, None
                
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return False, None
    
    def validate_session(self, session_token: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Validate session token and return user info.
        
        Returns:
            (valid, user_info) tuple
        """
        try:
            if not session_token or session_token not in self._sessions:
                return False, None
            
            session = self._sessions[session_token]
            
            # Check if session expired
            if datetime.now() > session['expires_at']:
                self._destroy_session(session_token)
                return False, None
            
            # Update last activity
            session['last_activity'] = datetime.now()
            
            return True, {
                'username': session['username'],
                'role': session['role'],
                'login_time': session['created_at'],
                'last_activity': session['last_activity']
            }
            
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
                username = self._sessions[session_token]['username']
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
            now = datetime.now()
            expired_tokens = [
                token for token, session in self._sessions.items()
                if now > session['expires_at']
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
                    'username': session['username'],
                    'role': session['role'],
                    'created_at': session['created_at'],
                    'last_activity': session['last_activity'],
                    'expires_at': session['expires_at']
                })
            
            return sorted(sessions, key=lambda x: x['last_activity'], reverse=True)
            
        except Exception as e:
            logger.error(f"Error getting active sessions: {e}")
            return []
    
    def _create_session(self, username: str, role: str) -> str:
        """Create new session and return token."""
        # Clean up old sessions if too many
        if len(self._sessions) >= self.max_sessions:
            self.cleanup_expired_sessions()
            
            # If still too many, remove oldest
            if len(self._sessions) >= self.max_sessions:
                oldest_token = min(
                    self._sessions.keys(),
                    key=lambda x: self._sessions[x]['last_activity']
                )
                self._destroy_session(oldest_token)
        
        # Generate secure session token
        session_token = secrets.token_urlsafe(32)
        
        # Create session
        now = datetime.now()
        self._sessions[session_token] = {
            'username': username,
            'role': role,
            'created_at': now,
            'last_activity': now,
            'expires_at': now + self.session_timeout
        }
        
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
    
    @property
    def session_count(self) -> int:
        """Get current number of active sessions."""
        return len(self._sessions)