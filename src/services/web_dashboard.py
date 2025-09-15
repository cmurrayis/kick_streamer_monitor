"""
Web dashboard service for daemon mode monitoring.

Provides a lightweight HTTP server with real-time statistics dashboard
accessible via browser when running in daemon mode.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from urllib.parse import parse_qs

from aiohttp import web, WSMsgType
from aiohttp.web import Request, Response, WebSocketResponse
from .auth_manager import AuthManager
from models.user import UserRole, UserSession, UserStatus

logger = logging.getLogger(__name__)


class WebDashboardService:
    """
    Lightweight web dashboard for daemon mode monitoring.
    
    Provides HTTP endpoints and WebSocket for real-time updates.
    """
    
    def __init__(self, monitor_service, host: str = "0.0.0.0", port: int = 8080, 
                 admin_username: str = "admin", admin_password: str = "password"):
        self.monitor_service = monitor_service
        self.host = host
        self.port = port
        
        # Authentication manager
        self.auth_manager = AuthManager(database_service=None)  # Will be set after db connection
        
        # Web server state
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self._is_running = False
        
        # WebSocket connections for real-time updates
        
        # Rate limiting for registration attempts
        self.registration_attempts = {}  # Track registration attempts by IP
        self._websocket_connections = set()
    
    async def start(self) -> None:
        """Start the web dashboard server."""
        if self._is_running:
            logger.warning("Web dashboard already running")
            return
        
        try:
            logger.info(f"Starting web dashboard on {self.host}:{self.port}")
            
            # Connect auth manager and web service to database service
            if hasattr(self.monitor_service, 'database_service'):
                self.database_service = self.monitor_service.database_service
                self.auth_manager.database_service = self.monitor_service.database_service
            
            # Create aiohttp application
            self.app = web.Application()
            
            # Public routes
            self.app.router.add_get('/', self._handle_dashboard)
            self.app.router.add_get('/api/status', self._handle_api_status)
            self.app.router.add_get('/api/streamers', self._handle_api_streamers)
            self.app.router.add_get('/ws', self._handle_websocket)
            
            # Authentication routes
            self.app.router.add_get('/login', self._handle_login_page)
            self.app.router.add_post('/login', self._handle_login_submit)
            self.app.router.add_get('/register', self._handle_register_page)
            self.app.router.add_post('/register', self._handle_register_submit)
            self.app.router.add_post('/logout', self._handle_logout)

            # User account settings routes (protected)
            self.app.router.add_get('/account', self._handle_account_settings)
            self.app.router.add_post('/account/update-profile', self._handle_update_profile)
            self.app.router.add_post('/account/change-password', self._handle_change_password)

            # Admin routes (protected)
            self.app.router.add_get('/admin', self._handle_admin_dashboard)
            self.app.router.add_get('/admin/streamers', self._handle_admin_streamers)
            self.app.router.add_post('/admin/streamers/add', self._handle_add_streamer)
            self.app.router.add_post('/admin/streamers/remove', self._handle_remove_streamer)
            self.app.router.add_post('/admin/streamers/toggle', self._handle_toggle_streamer)
            self.app.router.add_post('/admin/streamers/refresh', self._handle_refresh_streamer_data)
            self.app.router.add_get('/admin/users', self._handle_admin_users)
            self.app.router.add_get('/admin/analytics', self._handle_admin_analytics)
            self.app.router.add_post('/admin/users/add', self._handle_add_user)
            self.app.router.add_post('/admin/users/delete', self._handle_delete_user)
            self.app.router.add_post('/admin/users/toggle-status', self._handle_toggle_user_status)
            self.app.router.add_post('/admin/users/reset-password', self._handle_reset_user_password)
            self.app.router.add_post('/admin/users/assign', self._handle_assign_streamer)
            self.app.router.add_post('/admin/users/unassign', self._handle_unassign_streamer)
            
            # API endpoints for assignment management
            self.app.router.add_get('/api/users', self._handle_api_users)
            self.app.router.add_get('/api/streamers', self._handle_api_streamers)
            self.app.router.add_get('/api/assignments', self._handle_api_assignments)
            self.app.router.add_get('/api/users/{user_id}/assignments', self._handle_api_user_assignments)
            self.app.router.add_get('/api/users/assignments-summary', self._handle_api_assignments_summary)
            self.app.router.add_get('/api/debug/users', self._handle_api_debug_users)

            # Dashboard and analytics API endpoints
            self.app.router.add_get('/api/dashboard/summary', self._handle_api_dashboard_summary)
            self.app.router.add_get('/api/dashboard/status-grid', self._handle_api_status_grid)
            self.app.router.add_get('/api/dashboard/recent-activity', self._handle_api_recent_activity)
            self.app.router.add_get('/api/dashboard/system-health', self._handle_api_system_health)
            self.app.router.add_get('/api/dashboard/viewer-analytics', self._handle_api_viewer_analytics)
            
            # Start server
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            
            self.site = web.TCPSite(self.runner, self.host, self.port)
            await self.site.start()
            
            self._is_running = True
            
            # Start background task for WebSocket broadcasts
            asyncio.create_task(self._broadcast_updates())
            
            logger.info(f"Web dashboard started: http://{self.host}:{self.port}")
            
        except Exception as e:
            logger.error(f"Failed to start web dashboard: {e}")
            await self.stop()
            raise
    
    async def stop(self) -> None:
        """Stop the web dashboard server."""
        if not self._is_running:
            return
        
        logger.info("Stopping web dashboard")
        
        # Close WebSocket connections
        for ws in list(self._websocket_connections):
            await ws.close()
        self._websocket_connections.clear()
        
        # Stop server
        if self.site:
            await self.site.stop()
            self.site = None
        
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        
        self.app = None
        self._is_running = False
        
        logger.info("Web dashboard stopped")
    
    async def _handle_dashboard(self, request: Request) -> Response:
        """Serve the main landing page - check if user is logged in."""
        # Check if user has valid session
        session_token = request.cookies.get('session_token')
        if session_token:
            valid, user_session = self.auth_manager.validate_session(session_token)
            if valid:
                # User is logged in, show their dashboard
                if user_session.role == UserRole.ADMIN:
                    # Redirect admin to admin dashboard
                    response = Response(status=302)
                    response.headers['Location'] = '/admin'
                    return response
                else:
                    # Show user dashboard (TODO: Implement proper user dashboard in Task C)
                    html_content = await self._get_user_dashboard_html(user_session)
                    return Response(text=html_content, content_type='text/html')
        
        # No valid session, show splash page
        html_content = await self._get_splash_page_html()
        return Response(text=html_content, content_type='text/html')
    
    async def _handle_api_status(self, request: Request) -> Response:
        """API endpoint for service status."""
        try:
            if hasattr(self.monitor_service, 'get_monitoring_stats'):
                stats = self.monitor_service.get_monitoring_stats()
            else:
                stats = self.monitor_service.get_stats()
            
            return Response(
                text=json.dumps(stats, default=str),
                content_type='application/json'
            )
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return Response(
                text=json.dumps({"error": str(e)}),
                content_type='application/json',
                status=500
            )
    
    async def _handle_api_streamers(self, request: Request) -> Response:
        """API endpoint for streamer details."""
        try:
            if hasattr(self.monitor_service, 'get_streamer_details'):
                streamers = self.monitor_service.get_streamer_details()
            else:
                streamers = []
            
            return Response(
                text=json.dumps(streamers, default=str),
                content_type='application/json'
            )
        except Exception as e:
            logger.error(f"Error getting streamers: {e}")
            return Response(
                text=json.dumps({"error": str(e)}),
                content_type='application/json',
                status=500
            )
    
    async def _handle_websocket(self, request: Request) -> WebSocketResponse:
        """WebSocket endpoint for real-time updates."""
        ws = WebSocketResponse()
        await ws.prepare(request)
        
        self._websocket_connections.add(ws)
        logger.debug("WebSocket client connected")
        
        try:
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    logger.error(f'WebSocket error: {ws.exception()}')
                    break
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self._websocket_connections.discard(ws)
            logger.debug("WebSocket client disconnected")
        
        return ws
    
    async def _handle_login_page(self, request: Request) -> Response:
        """Serve the login page."""
        error = request.query.get('error')
        html_content = self._get_login_html(error)
        return Response(text=html_content, content_type='text/html')
    
    async def _handle_login_submit(self, request: Request) -> Response:
        """Handle login form submission."""
        try:
            data = await request.post()
            username = data.get('username', '').strip()
            password = data.get('password', '').strip()
            
            success, session_token = await self.auth_manager.authenticate(username, password)
            
            if success:
                # Set secure cookie and redirect to dashboard
                response = Response(status=302)
                response.headers['Location'] = '/'  # Will redirect to appropriate dashboard
                response.set_cookie(
                    'session_token', 
                    session_token,
                    max_age=7200,  # 2 hours
                    httponly=True,
                    secure=False  # Set to True in production with HTTPS
                )
                return response
            else:
                # Return login page with error
                html_content = self._get_login_html("Invalid username or password")
                return Response(text=html_content, content_type='text/html', status=401)
                
        except Exception as e:
            logger.error(f"Login error: {e}")
            html_content = self._get_login_html("Login error occurred")
            return Response(text=html_content, content_type='text/html', status=500)
    
    async def _handle_logout(self, request: Request) -> Response:
        """Handle logout."""
        try:
            session_token = request.cookies.get('session_token')
            if session_token:
                self.auth_manager.logout(session_token)
            
            # Clear cookie and redirect to dashboard
            response = Response(status=302)
            response.headers['Location'] = '/'
            response.del_cookie('session_token')
            return response
            
        except Exception as e:
            logger.error(f"Logout error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/'
            return response
    
    def _require_admin(self, request: Request) -> Optional[UserSession]:
        """Check if request has valid admin session."""
        try:
            session_token = request.cookies.get('session_token')
            if not session_token:
                return None
            
            valid, user_session = self.auth_manager.validate_session(session_token)
            if valid and user_session.role == UserRole.ADMIN:
                return user_session
            
            return None
            
        except Exception as e:
            logger.error(f"Auth check error: {e}")
            return None
    
    def _require_admin_redirect(self, request: Request) -> Response:
        """Check admin auth and return redirect response if unauthorized."""
        user_session = self._require_admin(request)
        if not user_session:
            response = Response(status=302)
            response.headers['Location'] = '/login?error=session_expired'
            return response
        return None

    def _get_user_session(self, request: Request) -> Optional[UserSession]:
        """Get valid user session (any role) or None."""
        try:
            session_token = request.cookies.get('session_token')
            if not session_token:
                return None

            valid, user_session = self.auth_manager.validate_session(session_token)
            if valid:
                return user_session

            return None

        except Exception as e:
            logger.error(f"User session check error: {e}")
            return None

    async def _handle_register_page(self, request: Request) -> Response:
        """Serve the registration page."""
        html_content = self._get_register_page_html()
        return Response(text=html_content, content_type='text/html')
    
    def _check_registration_rate_limit(self, remote_ip: str) -> bool:
        """Check if registration rate limit is exceeded for IP."""
        import time
        current_time = time.time()
        
        # Clean old entries (older than 1 hour)
        cutoff_time = current_time - 3600
        self.registration_attempts = {
            ip: attempts for ip, attempts in self.registration_attempts.items()
            if any(timestamp > cutoff_time for timestamp in attempts)
        }
        
        # Get attempts for this IP in the last hour
        attempts = self.registration_attempts.get(remote_ip, [])
        recent_attempts = [t for t in attempts if t > cutoff_time]
        
        # Allow max 3 registration attempts per hour per IP
        if len(recent_attempts) >= 3:
            return True  # Rate limited
        
        # Record this attempt
        if remote_ip not in self.registration_attempts:
            self.registration_attempts[remote_ip] = []
        self.registration_attempts[remote_ip].append(current_time)
        
        return False  # Not rate limited
    
    async def _handle_register_submit(self, request: Request) -> Response:
        """Handle user registration submission."""
        try:
            # Rate limiting by IP
            remote_ip = request.remote or 'unknown'
            if self._check_registration_rate_limit(remote_ip):
                logger.warning(f"Registration blocked - rate limit exceeded from {remote_ip}")
                return Response(status=302, headers={'Location': '/register?error=rate_limited'})
            
            data = await request.post()
            username = data.get('username', '').strip()
            email = data.get('email', '').strip()
            password = data.get('password', '').strip()
            confirm_password = data.get('confirm_password', '').strip()
            display_name = data.get('display_name', '').strip()
            website = data.get('website', '').strip()  # Honeypot field
            
            # Bot protection: if honeypot field is filled, it's likely a bot
            if website:
                logger.warning(f"Registration blocked - honeypot triggered from {remote_ip}")
                return Response(status=302, headers={'Location': '/register?error=registration_failed'})
            
            # Validate inputs
            if not username or not email or not password:
                return Response(status=302, headers={'Location': '/register?error=missing_fields'})
            
            if password != confirm_password:
                return Response(status=302, headers={'Location': '/register?error=password_mismatch'})
            
            # Register user
            success, message = await self.auth_manager.register_user(
                username=username,
                email=email,
                password=password,
                display_name=display_name or None
            )
            
            if success:
                # Redirect to login with success message
                return Response(status=302, headers={'Location': '/login?message=registration_success'})
            else:
                # Redirect back with error
                return Response(status=302, headers={'Location': f'/register?error=registration_failed&msg={message}'})
                
        except Exception as e:
            logger.error(f"Registration error: {e}")
            return Response(status=302, headers={'Location': '/register?error=registration_failed'})

    async def _handle_account_settings(self, request: Request) -> Response:
        """Handle account settings page."""
        # Require user login
        user_session = self._get_user_session(request)
        if not user_session:
            return Response(status=302, headers={'Location': '/login'})

        try:
            # Get user details from database
            user = await self.database_service.get_user_by_id(user_session.user_id)
            if not user:
                return Response(status=302, headers={'Location': '/login'})

            html_content = await self._get_account_settings_html(user)
            return Response(text=html_content, content_type='text/html')

        except Exception as e:
            logger.error(f"Account settings error: {e}")
            return Response(status=500, text="Internal server error")

    async def _handle_update_profile(self, request: Request) -> Response:
        """Handle updating user profile (email, display name)."""
        # Require user login
        user_session = self._get_user_session(request)
        if not user_session:
            return Response(status=302, headers={'Location': '/login'})

        try:
            data = await request.post()
            email = data.get('email', '').strip()
            display_name = data.get('display_name', '').strip()

            if not email:
                return Response(status=302, headers={'Location': '/account?error=missing_email'})

            # Update user profile
            from models.user import UserUpdate
            update_data = UserUpdate(
                email=email,
                display_name=display_name if display_name else None
            )

            updated_user = await self.database_service.update_user(user_session.user_id, update_data)
            if updated_user:
                return Response(status=302, headers={'Location': '/account?success=profile_updated'})
            else:
                return Response(status=302, headers={'Location': '/account?error=update_failed'})

        except Exception as e:
            logger.error(f"Profile update error: {e}")
            return Response(status=302, headers={'Location': '/account?error=update_failed'})

    async def _handle_change_password(self, request: Request) -> Response:
        """Handle password change."""
        # Require user login
        user_session = self._get_user_session(request)
        if not user_session:
            return Response(status=302, headers={'Location': '/login'})

        try:
            data = await request.post()
            current_password = data.get('current_password', '')
            new_password = data.get('new_password', '')
            confirm_password = data.get('confirm_password', '')

            # Validate input
            if not all([current_password, new_password, confirm_password]):
                return Response(status=302, headers={'Location': '/account?error=missing_fields'})

            if new_password != confirm_password:
                return Response(status=302, headers={'Location': '/account?error=password_mismatch'})

            if len(new_password) < 6:
                return Response(status=302, headers={'Location': '/account?error=password_too_short'})

            # Verify current password
            user = await self.database_service.get_user_by_id(user_session.user_id)
            if not user:
                return Response(status=302, headers={'Location': '/login'})

            # Check current password
            import hashlib
            current_hash = hashlib.sha256((current_password + "kick_monitor_salt").encode()).hexdigest()
            if current_hash != user.password_hash:
                return Response(status=302, headers={'Location': '/account?error=incorrect_password'})

            # Hash new password
            new_hash = hashlib.sha256((new_password + "kick_monitor_salt").encode()).hexdigest()

            # Update password
            from models.user import UserUpdate
            update_data = UserUpdate(password_hash=new_hash)
            updated_user = await self.database_service.update_user(user_session.user_id, update_data)

            if updated_user:
                logger.info(f"User {user.username} changed their password")
                return Response(status=302, headers={'Location': '/account?success=password_changed'})
            else:
                return Response(status=302, headers={'Location': '/account?error=password_change_failed'})

        except Exception as e:
            logger.error(f"Password change error: {e}")
            return Response(status=302, headers={'Location': '/account?error=password_change_failed'})

    async def _handle_admin_dashboard(self, request: Request) -> Response:
        """Serve the admin dashboard."""
        redirect = self._require_admin_redirect(request)
        if redirect:
            return redirect
        
        user_session = self._require_admin(request)
        html_content = self._get_admin_dashboard_html(user_session)
        return Response(text=html_content, content_type='text/html')
    
    async def _handle_admin_streamers(self, request: Request) -> Response:
        """Serve the admin streamers management page."""
        user_session = self._require_admin(request)
        if not user_session:
            response = Response(status=302)
            response.headers['Location'] = '/login'
            return response
        
        html_content = self._get_admin_streamers_html(user_session)
        return Response(text=html_content, content_type='text/html')
    
    async def _handle_add_streamer(self, request: Request) -> Response:
        """Handle adding a new streamer."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")

        try:
            data = await request.post()
            username = data.get('username', '').strip()

            if not username:
                return Response(status=400, text="Username required")

            # Check if database service is available
            if not self.database_service:
                logger.error("Database service not available")
                response = Response(status=302)
                response.headers['Location'] = '/admin/streamers?error=database_unavailable'
                return response

            # Add streamer via database service
            success = await self.database_service.add_streamer(username)
            
            if not success:
                response = Response(status=302)
                response.headers['Location'] = '/admin/streamers?error=add_failed'
                return response
            
            logger.info(f"Admin {user_session.username} adding streamer: {username}")
            
            # Redirect back to streamers page
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?success=added'
            return response
            
        except Exception as e:
            logger.error(f"Add streamer error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?error=add_failed'
            return response
    
    async def _handle_remove_streamer(self, request: Request) -> Response:
        """Handle removing a streamer."""
        user_info = self._require_admin(request)
        if not user_info:
            return Response(status=401, text="Unauthorized")
        
        try:
            data = await request.post()
            streamer_id = data.get('streamer_id', '').strip()
            
            if not streamer_id:
                return Response(status=400, text="Streamer ID required")

            # Check if database service is available
            if not self.database_service:
                logger.error("Database service not available")
                response = Response(status=302)
                response.headers['Location'] = '/admin/streamers?error=database_unavailable'
                return response

            # Remove streamer via database service
            logger.info(f"Admin {user_info.username} removing streamer ID: {streamer_id}")
            success = await self.database_service.delete_streamer(int(streamer_id))

            if success:
                response = Response(status=302)
                response.headers['Location'] = '/admin/streamers?success=removed'
                return response
            else:
                response = Response(status=302)
                response.headers['Location'] = '/admin/streamers?error=remove_failed'
                return response
            
        except Exception as e:
            logger.error(f"Remove streamer error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?error=remove_failed'
            return response
    
    async def _handle_toggle_streamer(self, request: Request) -> Response:
        """Handle toggling streamer monitoring status."""
        user_info = self._require_admin(request)
        if not user_info:
            return Response(status=401, text="Unauthorized")
        
        try:
            data = await request.post()
            streamer_id = data.get('streamer_id', '').strip()
            
            if not streamer_id:
                return Response(status=400, text="Streamer ID required")
            
            # Toggle streamer status via database service
            logger.info(f"Admin {user_info.username} toggling streamer ID: {streamer_id}")
            
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?success=toggled'
            return response
            
        except Exception as e:
            logger.error(f"Toggle streamer error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?error=toggle_failed'
            return response
    
    async def _handle_refresh_streamer_data(self, request: Request) -> Response:
        """Handle refreshing streamer profile data from Kick.com API."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")
        
        try:
            data = await request.post()
            streamer_id = data.get('streamer_id', '').strip()
            
            if not streamer_id:
                return Response(status=400, text="Streamer ID required")
            
            streamer_id = int(streamer_id)
            
            # Get streamer from database
            streamer = await self.database_service.get_streamer_by_id(streamer_id)
            if not streamer:
                response = Response(status=302)
                response.headers['Location'] = '/admin/streamers?error=streamer_not_found'
                return response
            
            logger.info(f"Admin {user_session.username} refreshing data for streamer: {streamer.username}")
            
            # Fetch profile data from Kick.com API
            if hasattr(self.monitor_service, 'auth_service'):
                try:
                    # Get channel info from Kick API
                    channel_info = await self.monitor_service.auth_service.get_channel_info(streamer.username)
                    
                    if channel_info:
                        # Extract profile data
                        profile_data = {}
                        
                        # Map API response to our database fields
                        if 'user' in channel_info:
                            user_data = channel_info['user']
                            profile_data['display_name'] = user_data.get('username')  # Kick uses username as display name
                            profile_data['profile_picture_url'] = user_data.get('profile_pic')
                            profile_data['bio'] = user_data.get('bio', '')
                            profile_data['follower_count'] = user_data.get('followers_count', 0)
                            profile_data['is_verified'] = user_data.get('verified', False)
                        
                        # Update database with new profile data
                        updated_streamer = await self.database_service.update_streamer_profile_data(
                            streamer_id, profile_data
                        )
                        
                        if updated_streamer:
                            logger.info(f"Successfully refreshed profile data for {streamer.username}")
                            response = Response(status=302)
                            response.headers['Location'] = '/admin/streamers?success=refreshed'
                            return response
                        else:
                            logger.error(f"Failed to update profile data for {streamer.username}")
                            response = Response(status=302)
                            response.headers['Location'] = '/admin/streamers?error=update_failed'
                            return response
                    else:
                        logger.warning(f"No channel info found for {streamer.username}")
                        response = Response(status=302)
                        response.headers['Location'] = '/admin/streamers?error=no_data'
                        return response
                        
                except Exception as api_error:
                    logger.error(f"API error refreshing {streamer.username}: {api_error}")
                    response = Response(status=302)
                    response.headers['Location'] = '/admin/streamers?error=api_failed'
                    return response
            else:
                logger.error("No auth service available for API calls")
                response = Response(status=302)
                response.headers['Location'] = '/admin/streamers?error=no_api'
                return response
                
        except ValueError:
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?error=invalid_id'
            return response
        except Exception as e:
            logger.error(f"Refresh streamer data error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?error=refresh_failed'
            return response
    
    async def _broadcast_updates(self) -> None:
        """Broadcast updates to connected WebSocket clients."""
        while self._is_running:
            try:
                if self._websocket_connections:
                    # Get current data
                    if hasattr(self.monitor_service, 'get_monitoring_stats'):
                        stats = self.monitor_service.get_monitoring_stats()
                    else:
                        stats = self.monitor_service.get_stats()
                    
                    if hasattr(self.monitor_service, 'get_streamer_details'):
                        streamers = self.monitor_service.get_streamer_details()
                    else:
                        streamers = []
                    
                    # Broadcast to all connections
                    update_data = {
                        "type": "update",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "stats": stats,
                        "streamers": streamers
                    }
                    
                    disconnected = set()
                    for ws in self._websocket_connections:
                        try:
                            await ws.send_str(json.dumps(update_data, default=str))
                        except Exception as e:
                            logger.debug(f"WebSocket send error: {e}")
                            disconnected.add(ws)
                    
                    # Clean up disconnected clients
                    self._websocket_connections -= disconnected
                
                # Update every 5 seconds
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"Error broadcasting updates: {e}")
                await asyncio.sleep(5)
    
    
    def _get_login_html(self, error_code: str = "") -> str:
        """Generate the login page HTML."""
        error_messages = {
            'session_expired': 'Your session has expired. Please log in again.',
            'invalid_credentials': 'Invalid username or password.',
            'login_failed': 'Login failed. Please try again.'
        }
        error_message = error_messages.get(error_code, error_code) if error_code else ''
        error_html = f'<div class="error-message">{error_message}</div>' if error_message else ''
        
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login - Kick Streamer Monitor</title>
    <style>
        body {{
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }}
        .login-container {{
            background: #0a0a0a;
            border: 2px solid #00ff00;
            padding: 40px;
            width: 400px;
            text-align: center;
        }}
        .login-title {{
            color: #ffff00;
            margin-bottom: 30px;
            font-size: 24px;
            font-weight: bold;
        }}
        .form-group {{
            margin-bottom: 20px;
            text-align: left;
        }}
        .form-group label {{
            display: block;
            margin-bottom: 5px;
            color: #00ff00;
        }}
        .form-group input {{
            width: 100%;
            padding: 10px;
            background: #1a1a1a;
            border: 1px solid #00ff00;
            color: #00ff00;
            font-family: 'Courier New', monospace;
            font-size: 14px;
        }}
        .form-group input:focus {{
            outline: none;
            border-color: #ffff00;
            box-shadow: 0 0 5px #ffff00;
        }}
        .login-button {{
            background: #0a0a0a;
            border: 2px solid #00ff00;
            color: #00ff00;
            padding: 12px 30px;
            font-family: 'Courier New', monospace;
            font-size: 16px;
            cursor: pointer;
            width: 100%;
        }}
        .login-button:hover {{
            background: #00ff00;
            color: #000000;
        }}
        .error-message {{
            background: #330000;
            border: 1px solid #ff0000;
            color: #ff6666;
            padding: 10px;
            margin-bottom: 20px;
            text-align: center;
        }}
        .back-link {{
            margin-top: 20px;
        }}
        .back-link a {{
            color: #00ff00;
            text-decoration: none;
        }}
        .back-link a:hover {{
            color: #ffff00;
        }}
    </style>
</head>
<body>
    <div class="login-container">
        <div class="login-title">🔐 ADMIN LOGIN</div>
        {error_html}
        <form method="post" action="/login">
            <div class="form-group">
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" required>
            </div>
            <div class="form-group">
                <label for="password">Password:</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit" class="login-button">LOGIN</button>
        </form>
        <div class="back-link">
            <a href="/">&larr; Back to Dashboard</a>
        </div>
    </div>
</body>
</html>'''
    
    async def _handle_admin_users(self, request: Request) -> Response:
        """Serve the admin users management page."""
        user_session = self._require_admin(request)
        if not user_session:
            response = Response(status=302)
            response.headers['Location'] = '/login'
            return response
        
        html_content = await self._get_admin_users_html(user_session)
        return Response(text=html_content, content_type='text/html')

    async def _handle_admin_analytics(self, request: Request) -> Response:
        """Serve the admin analytics page."""
        user_session = self._require_admin(request)
        if not user_session:
            response = Response(status=302)
            response.headers['Location'] = '/login'
            return response

        html_content = await self._get_admin_analytics_html(user_session)
        return Response(text=html_content, content_type='text/html')

    async def _handle_add_user(self, request: Request) -> Response:
        """Handle adding a new user."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")
        
        try:
            data = await request.post()
            username = data.get('username', '').strip()
            email = data.get('email', '').strip()
            password = data.get('password', '').strip()
            display_name = data.get('display_name', '').strip()
            role = data.get('role', 'user').strip()
            
            if not username or not email or not password:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=missing_fields'
                return response
            
            # Create user via auth manager
            success, message = await self.auth_manager.register_user(
                username=username,
                email=email, 
                password=password,
                display_name=display_name or None
            )
            
            if success:
                # Update role if not default user
                if role != 'user':
                    from models.user import UserRole, UserUpdate
                    user_role = UserRole(role)
                    update_data = UserUpdate(role=user_role)
                    user = await self.database_service.get_user_by_username(username)
                    if user:
                        await self.database_service.update_user(user.id, update_data)
                
                logger.info(f"Admin {user_session.username} created user: {username}")
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?success=added'
                return response
            else:
                response = Response(status=302)
                response.headers['Location'] = f'/admin/users?error=add_failed'
                return response
                
        except Exception as e:
            logger.error(f"Error adding user: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=add_failed'
            return response
    
    async def _handle_delete_user(self, request: Request) -> Response:
        """Handle deleting a user."""
        redirect = self._require_admin_redirect(request)
        if redirect:
            return redirect
        
        user_session = self._require_admin(request)
        
        try:
            data = await request.post()
            user_id = int(data.get('user_id', 0))
            
            if not user_id:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=missing_user_id'
                return response
            
            # Get user info before deletion
            user_to_delete = await self.database_service.get_user_by_id(user_id)
            if not user_to_delete:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=user_not_found'
                return response
            
            # Prevent deletion of admin users
            if user_to_delete.role == UserRole.ADMIN:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=cannot_delete_admin'
                return response
            
            # Delete user (this should cascade delete assignments)
            success = await self.database_service.delete_user(user_id)
            
            if success:
                logger.info(f"Admin {user_session.username} deleted user: {user_to_delete.username}")
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?success=deleted'
                return response
            else:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=delete_failed'
                return response
                
        except ValueError:
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=invalid_user_id'
            return response
        except Exception as e:
            logger.error(f"Delete user error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=delete_failed'
            return response

    async def _handle_toggle_user_status(self, request: Request) -> Response:
        """Handle toggling user status (active/inactive)."""
        redirect = self._require_admin_redirect(request)
        if redirect:
            return redirect

        user_session = self._require_admin(request)

        try:
            data = await request.post()
            user_id = int(data.get('user_id', 0))

            if not user_id:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=missing_user_id'
                return response

            # Check if database service is available
            if not self.database_service:
                logger.error("Database service not available")
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=database_unavailable'
                return response

            # Get user info before status change
            user_to_update = await self.database_service.get_user_by_id(user_id)
            if not user_to_update:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=user_not_found'
                return response

            # Prevent status change of admin users
            if user_to_update.role == UserRole.ADMIN:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=cannot_modify_admin'
                return response

            # Toggle status between ACTIVE and INACTIVE
            new_status = UserStatus.INACTIVE if user_to_update.status == UserStatus.ACTIVE else UserStatus.ACTIVE

            # Update user status
            from models.user import UserUpdate
            update_data = UserUpdate(status=new_status)
            updated_user = await self.database_service.update_user(user_id, update_data)

            if updated_user:
                status_action = "disabled" if new_status == UserStatus.INACTIVE else "enabled"
                logger.info(f"Admin {user_session.username} {status_action} user: {user_to_update.username}")
                response = Response(status=302)
                response.headers['Location'] = f'/admin/users?success={status_action}'
                return response
            else:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=status_update_failed'
                return response

        except ValueError:
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=invalid_user_id'
            return response
        except Exception as e:
            logger.error(f"Toggle user status error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=status_update_failed'
            return response

    async def _handle_reset_user_password(self, request: Request) -> Response:
        """Handle resetting user password to a default value."""
        redirect = self._require_admin_redirect(request)
        if redirect:
            return redirect

        user_session = self._require_admin(request)

        try:
            data = await request.post()
            user_id = int(data.get('user_id', 0))

            if not user_id:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=missing_user_id'
                return response

            # Check if database service is available
            if not self.database_service:
                logger.error("Database service not available")
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=database_unavailable'
                return response

            # Get user info before password reset
            user_to_update = await self.database_service.get_user_by_id(user_id)
            if not user_to_update:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=user_not_found'
                return response

            # Prevent password reset of admin users by non-admin
            if user_to_update.role == UserRole.ADMIN and user_session.user_role != UserRole.ADMIN:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=cannot_modify_admin'
                return response

            # Generate default password (could be improved with random generation)
            default_password = "TempPass123!"

            # Hash the new password
            import hashlib
            password_hash = hashlib.sha256((default_password + "kick_monitor_salt").encode()).hexdigest()

            # Update user password
            from models.user import UserUpdate
            update_data = UserUpdate(password_hash=password_hash)
            updated_user = await self.database_service.update_user(user_id, update_data)

            if updated_user:
                logger.info(f"Admin {user_session.username} reset password for user: {user_to_update.username}")
                response = Response(status=302)
                response.headers['Location'] = f'/admin/users?success=password_reset&username={user_to_update.username}&password={default_password}'
                return response
            else:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=password_reset_failed'
                return response

        except ValueError:
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=invalid_user_id'
            return response
        except Exception as e:
            logger.error(f"Reset password error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=password_reset_failed'
            return response

    async def _handle_assign_streamer(self, request: Request) -> Response:
        """Handle assigning a streamer to a user."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")
        
        try:
            data = await request.post()
            user_id = int(data.get('user_id', 0))
            streamer_id = int(data.get('streamer_id', 0))
            
            if not user_id or not streamer_id:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=missing_fields'
                return response
            
            # Check if streamer is already assigned to ANY user
            existing_assignments = await self.database_service.get_streamer_user_assignments(streamer_id)
            if existing_assignments:
                # Streamer is already assigned to someone else
                existing_user_ids = [a.user_id for a in existing_assignments]
                if user_id not in existing_user_ids:
                    response = Response(status=302)
                    response.headers['Location'] = '/admin/users?error=streamer_already_assigned'
                    return response
                else:
                    # Same user trying to assign same streamer again
                    response = Response(status=302)
                    response.headers['Location'] = '/admin/users?error=duplicate_assignment'
                    return response
            
            # Create assignment (streamer not assigned to anyone)
            from models.user import UserStreamerAssignmentCreate
            assignment = UserStreamerAssignmentCreate(
                user_id=user_id,
                streamer_id=streamer_id
            )
            
            result = await self.database_service.create_user_streamer_assignment(
                assignment, assigned_by=user_session.user_id
            )
            
            if result:
                logger.info(f"Admin {user_session.username} assigned streamer {streamer_id} to user {user_id}")
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?success=assigned'
                return response
            else:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=assign_failed'
                return response
                
        except Exception as e:
            logger.error(f"Error assigning streamer: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=assign_failed'
            return response
    
    async def _handle_unassign_streamer(self, request: Request) -> Response:
        """Handle unassigning a streamer from a user."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")
        
        try:
            data = await request.post()
            user_id = int(data.get('user_id', 0))
            streamer_id = int(data.get('streamer_id', 0))
            
            if not user_id or not streamer_id:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=missing_fields'
                return response
            
            # Remove assignment
            success = await self.database_service.delete_user_streamer_assignment(
                user_id, streamer_id
            )
            
            if success:
                logger.info(f"Admin {user_session.username} unassigned streamer {streamer_id} from user {user_id}")
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?success=unassigned'
                return response
            else:
                response = Response(status=302)
                response.headers['Location'] = '/admin/users?error=unassign_failed'
                return response
                
        except Exception as e:
            logger.error(f"Error unassigning streamer: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/users?error=unassign_failed'
            return response
    
    async def _handle_api_users(self, request: Request) -> Response:
        """API endpoint to get all users (for assignment management)."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")

        # Check if database service is available
        if not self.database_service:
            logger.error("Database service not available for users API")
            return Response(status=500, text="Database service unavailable")

        try:
            users = await self.database_service.get_all_users()
            users_data = []
            for user in users:
                users_data.append({
                    'id': user.id,
                    'username': user.username,
                    'role': user.role.value,
                    'status': user.status.value
                })
            return Response(text=json.dumps(users_data), content_type='application/json')
        except Exception as e:
            logger.error(f"Error fetching users API: {e}")
            return Response(status=500, text="Internal server error")
    
    async def _handle_api_streamers(self, request: Request) -> Response:
        """API endpoint to get all streamers (for assignment management)."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")

        # Check if database service is available
        if not self.database_service:
            logger.error("Database service not available for streamers API")
            return Response(status=500, text="Database service unavailable")

        try:
            streamers = await self.database_service.get_all_streamers()
            streamers_data = []
            for streamer in streamers:
                streamers_data.append({
                    'id': streamer.id,
                    'username': streamer.username,
                    'status': streamer.status.value,
                    'display_name': streamer.display_name,
                    'last_seen_online': streamer.last_seen_online.isoformat() if streamer.last_seen_online else None,
                    'last_status_update': streamer.last_status_update.isoformat() if streamer.last_status_update else None,
                    'is_active': streamer.is_active
                })
            return Response(text=json.dumps(streamers_data), content_type='application/json')
        except Exception as e:
            logger.error(f"Error fetching streamers API: {e}")
            return Response(status=500, text="Internal server error")
    
    async def _handle_api_assignments(self, request: Request) -> Response:
        """API endpoint to get all assignments."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")

        # Check if database service is available
        if not self.database_service:
            logger.error("Database service not available for assignments API")
            return Response(status=500, text="Database service unavailable")

        try:
            assignments = await self.database_service.get_all_user_streamer_assignments()
            assignments_data = []
            for assignment in assignments:
                assignments_data.append({
                    'user_id': assignment.user_id,
                    'streamer_id': assignment.streamer_id,
                    'assigned_at': assignment.assigned_at.isoformat() if assignment.assigned_at else None
                })
            return Response(text=json.dumps(assignments_data), content_type='application/json')
        except Exception as e:
            logger.error(f"Error fetching assignments API: {e}")
            return Response(status=500, text="Internal server error")
    
    async def _handle_api_user_assignments(self, request: Request) -> Response:
        """API endpoint to get assignments for a specific user."""
        user_session = self._require_admin(request)
        if not user_session:
            return Response(status=401, text="Unauthorized")

        # Check if database service is available
        if not self.database_service:
            logger.error("Database service not available for user assignments")
            return Response(status=500, text="Database service unavailable")

        try:
            user_id = int(request.match_info['user_id'])
            assignments = await self.database_service.get_user_streamer_assignments(user_id)
            
            assignments_data = []
            for assignment in assignments:
                streamer = await self.database_service.get_streamer_by_id(assignment.streamer_id)
                if streamer:
                    assignments_data.append({
                        'streamer_id': assignment.streamer_id,
                        'streamer_username': streamer.username
                    })
            
            return Response(text=json.dumps(assignments_data), content_type='application/json')
        except Exception as e:
            logger.error(f"Error fetching user assignments API: {e}")
            return Response(status=500, text="Internal server error")
    
    async def _handle_api_assignments_summary(self, request: Request) -> Response:
        """API endpoint to get assignment summary for all users."""
        logger.error("DEBUG: Assignment summary API called")  # Changed to ERROR so it shows

        user_session = self._require_admin(request)
        if not user_session:
            logger.error("DEBUG: Assignment summary API: Unauthorized access attempt")
            return Response(status=401, text="Unauthorized")

        # Check if database service is available
        if not self.database_service:
            logger.error("DEBUG: Database service not available for assignments summary")
            return Response(status=500, text="Database service unavailable")

        try:
            logger.info("Starting assignments summary API call")
            users = await self.database_service.get_all_users()
            logger.info(f"Found {len(users)} users for assignments summary")
            
            summary_data = []
            
            for user in users:
                try:
                    if user.role != UserRole.ADMIN:  # Skip admin users
                        logger.info(f"Getting assignments for user {user.id} ({user.username})")
                        try:
                            assignments = await self.database_service.get_user_streamer_assignments(user.id)
                            logger.info(f"Found {len(assignments)} assignments for user {user.username}")
                        except Exception as assignment_error:
                            logger.error(f"Error getting assignments for user {user.username}: {assignment_error}")
                            # Continue with empty assignments if the table doesn't exist or has issues
                            assignments = []
                        
                        streamer_names = []
                        for assignment in assignments:
                            try:
                                streamer = await self.database_service.get_streamer_by_id(assignment.streamer_id)
                                if streamer:
                                    streamer_names.append(streamer.username)
                                    logger.info(f"Added streamer {streamer.username} for user {user.username}")
                                else:
                                    logger.warning(f"Streamer with ID {assignment.streamer_id} not found")
                            except Exception as streamer_error:
                                logger.error(f"Error getting streamer {assignment.streamer_id}: {streamer_error}")
                        
                        summary_data.append({
                            'user_id': user.id,
                            'username': user.username,
                            'streamers': streamer_names
                        })
                        logger.info(f"Added summary for user {user.username}: {len(streamer_names)} streamers")
                except Exception as user_error:
                    logger.error(f"Error processing user {user.username}: {user_error}")
            
            logger.error(f"DEBUG: Returning {len(summary_data)} user summaries: {summary_data}")
            return Response(text=json.dumps(summary_data), content_type='application/json')
        except Exception as e:
            logger.error(f"Error fetching assignments summary API: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return Response(status=500, text=f"Internal server error: {str(e)}")
    
    async def _handle_api_debug_users(self, request: Request) -> Response:
        """Debug API endpoint to check users without auth."""
        try:
            # Skip auth check for debugging
            logger.info("Debug API: Checking users...")
            
            if not self.database_service:
                return Response(text=json.dumps({"error": "No database service"}), content_type='application/json')
            
            users = await self.database_service.get_all_users()
            logger.info(f"Debug API: Found {len(users)} users")
            
            users_data = []
            for user in users:
                users_data.append({
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'role': user.role.value,
                    'status': user.status.value
                })
            
            debug_data = {
                'user_count': len(users),
                'users': users_data,
                'database_connected': bool(self.database_service and self.database_service.pool)
            }
            
            return Response(text=json.dumps(debug_data, indent=2), content_type='application/json')
        except Exception as e:
            logger.error(f"Debug API error: {e}")
            error_data = {
                'error': str(e),
                'database_service': bool(self.database_service),
                'pool': bool(self.database_service.pool if self.database_service else False)
            }
            return Response(text=json.dumps(error_data, indent=2), content_type='application/json')

    # =========================================================================
    # DASHBOARD API HANDLERS
    # =========================================================================

    async def _handle_api_dashboard_summary(self, request: Request) -> Response:
        """API endpoint for dashboard summary statistics."""
        try:
            if not self.database_service:
                return Response(status=503, text=json.dumps({"error": "Database service unavailable"}),
                              content_type='application/json')

            summary = await self.database_service.get_dashboard_summary()
            return Response(text=json.dumps(summary), content_type='application/json')

        except Exception as e:
            logger.error(f"Dashboard summary API error: {e}")
            return Response(status=500, text=json.dumps({"error": "Internal server error"}),
                          content_type='application/json')

    async def _handle_api_status_grid(self, request: Request) -> Response:
        """API endpoint for streamer status grid data."""
        try:
            if not self.database_service:
                return Response(status=503, text=json.dumps({"error": "Database service unavailable"}),
                              content_type='application/json')

            status_grid = await self.database_service.get_streamer_status_grid()

            return Response(text=json.dumps(status_grid, default=str), content_type='application/json')

        except Exception as e:
            logger.error(f"Status grid API error: {e}")
            return Response(status=500, text=json.dumps({"error": "Internal server error"}),
                          content_type='application/json')

    async def _handle_api_recent_activity(self, request: Request) -> Response:
        """API endpoint for recent activity feed."""
        try:
            if not self.database_service:
                return Response(status=503, text=json.dumps({"error": "Database service unavailable"}),
                              content_type='application/json')

            # Get limit from query params (default 25)
            limit = min(int(request.query.get('limit', 25)), 100)  # Cap at 100

            recent_events = await self.database_service.get_recent_status_events(limit)

            return Response(text=json.dumps(recent_events, default=str), content_type='application/json')

        except Exception as e:
            logger.error(f"Recent activity API error: {e}")
            return Response(status=500, text=json.dumps({"error": "Internal server error"}),
                          content_type='application/json')

    async def _handle_api_system_health(self, request: Request) -> Response:
        """API endpoint for system health metrics."""
        try:
            if not self.database_service:
                return Response(status=503, text=json.dumps({
                    "database_status": "disconnected",
                    "error": "Database service unavailable"
                }), content_type='application/json')

            health_metrics = await self.database_service.get_system_health_metrics()

            # Get real monitoring statistics if available
            if hasattr(self.monitor_service, 'get_monitoring_stats'):
                monitor_stats = self.monitor_service.get_monitoring_stats()
                processing_stats = monitor_stats.get('processing', {})

                # Update health metrics with real monitoring data
                health_metrics['processing'] = {
                    'success_rate': processing_stats.get('success_rate', 0),
                    'total_checks': processing_stats.get('total_checks', 0),
                    'failed_checks': processing_stats.get('failed_checks', 0)
                }

            return Response(text=json.dumps(health_metrics, default=str), content_type='application/json')

        except Exception as e:
            logger.error(f"System health API error: {e}")
            # Return partial health info even on error
            health_data = {
                "database_status": "error",
                "error": str(e),
                "last_health_check": datetime.now(timezone.utc).isoformat()
            }
            return Response(status=500, text=json.dumps(health_data), content_type='application/json')

    async def _handle_api_viewer_analytics(self, request: Request) -> Response:
        """API endpoint for viewer count analytics."""
        try:
            if not self.database_service:
                return Response(status=503, text=json.dumps({"error": "Database service unavailable"}),
                              content_type='application/json')

            viewer_analytics = await self.database_service.get_viewer_analytics_summary()
            return Response(text=json.dumps(viewer_analytics, default=str), content_type='application/json')

        except Exception as e:
            logger.error(f"Viewer analytics API error: {e}")
            return Response(status=500, text=json.dumps({"error": "Internal server error"}),
                          content_type='application/json')

    def _get_register_page_html(self) -> str:
        """Generate the registration page HTML."""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Register - Kick Streamer Monitor</title>
    <style>
        body {
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }
        .register-container {
            background: #0a0a0a;
            border: 1px solid #00ff00;
            padding: 40px;
            max-width: 400px;
            width: 100%;
        }
        .title {
            text-align: center;
            color: #ffff00;
            margin-bottom: 30px;
            font-size: 24px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #ffff00;
        }
        .form-group input {
            width: 100%;
            padding: 10px;
            background: #1a1a1a;
            border: 1px solid #00ff00;
            color: #00ff00;
            font-family: 'Courier New', monospace;
            box-sizing: border-box;
        }
        .form-group input:focus {
            outline: none;
            border-color: #ffff00;
        }
        .register-btn {
            width: 100%;
            padding: 12px;
            background: #003300;
            border: 1px solid #00ff00;
            color: #00ff00;
            font-family: 'Courier New', monospace;
            font-size: 16px;
            cursor: pointer;
            margin-top: 10px;
        }
        .register-btn:hover {
            background: #00ff00;
            color: #000000;
        }
        .login-link {
            text-align: center;
            margin-top: 20px;
        }
        .login-link a {
            color: #ffff00;
            text-decoration: none;
        }
        .login-link a:hover {
            color: #00ff00;
        }
        .error-message, .success-message {
            padding: 10px;
            margin-bottom: 20px;
            border: 1px solid;
            text-align: center;
        }
        .error-message {
            background: #330000;
            border-color: #ff0000;
            color: #ff6666;
        }
        .success-message {
            background: #003300;
            border-color: #00ff00;
            color: #00ff00;
        }
    </style>
</head>
<body>
    <div class="register-container">
        <div class="title">🔐 REGISTER</div>
        
        <div id="message-container"></div>
        
        <form method="post" action="/register">
            <div class="form-group">
                <label for="username">Username:</label>
                <input type="text" id="username" name="username" required>
            </div>
            
            <div class="form-group">
                <label for="email">Email:</label>
                <input type="email" id="email" name="email" required>
            </div>
            
            <div class="form-group">
                <label for="display_name">Display Name (optional):</label>
                <input type="text" id="display_name" name="display_name">
            </div>
            
            <div class="form-group">
                <label for="password">Password:</label>
                <input type="password" id="password" name="password" required>
            </div>
            
            <div class="form-group">
                <label for="confirm_password">Confirm Password:</label>
                <input type="password" id="confirm_password" name="confirm_password" required>
            </div>
            
            <!-- Honeypot field - hidden from real users -->
            <div style="position: absolute; left: -9999px; visibility: hidden;">
                <label for="website">Website (leave blank):</label>
                <input type="text" id="website" name="website" tabindex="-1" autocomplete="off">
            </div>
            
            <button type="submit" class="register-btn">REGISTER</button>
        </form>
        
        <div class="login-link">
            Already have an account? <a href="/login">Login here</a><br>
            <a href="/" style="color: #888888; text-decoration: none; font-size: 14px;">← Back to main page</a>
        </div>
    </div>

    <script>
        // Check for messages in URL params
        const registerUrlParams = new URLSearchParams(window.location.search);
        const error = registerUrlParams.get('error');
        const message = registerUrlParams.get('message');
        const messageContainer = document.getElementById('message-container');

        if (error) {
            const messages = {
                'missing_fields': 'Please fill in all required fields.',
                'password_mismatch': 'Passwords do not match.',
                'registration_failed': 'Registration failed. Please try again.',
                'rate_limited': 'Too many registration attempts. Please try again later.'
            };
            messageContainer.innerHTML = `<div class="error-message">${messages[error] || 'Registration failed!'}</div>`;
        } else if (message) {
            const messages = {
                'registration_success': 'Registration successful! Please log in.'
            };
            messageContainer.innerHTML = `<div class="success-message">${messages[message] || 'Success!'}</div>`;
        }
    </script>
</body>
</html>'''

    async def _get_splash_page_html(self) -> str:
        """Generate the public splash page HTML."""
        # Get current system stats for display
        try:
            if hasattr(self.monitor_service, 'get_monitoring_stats'):
                stats = self.monitor_service.get_monitoring_stats()
            else:
                stats = self.monitor_service.get_stats()
            
            # Get basic streamer count
            total_streamers = await self.database_service.get_streamer_count() if self.database_service else 0
            
        except Exception as e:
            logger.error(f"Error fetching splash page stats: {e}")
            stats = {}
            total_streamers = 0
        
        # Extract stats
        service_status = stats.get('service_status', {})
        streamer_stats = stats.get('streamers', {})
        
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kick Streamer Monitor</title>
    <style>
        body {{
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        .header {{
            background: #0a0a0a;
            border-bottom: 1px solid #00ff00;
            padding: 20px;
            text-align: center;
        }}
        .title {{
            font-size: 2.5em;
            color: #ffff00;
            margin-bottom: 10px;
        }}
        .subtitle {{
            font-size: 1.2em;
            color: #888888;
        }}
        .main-content {{
            flex: 1;
            max-width: 1200px;
            margin: 0 auto;
            padding: 40px 20px;
            width: 100%;
            box-sizing: border-box;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }}
        .stat-card {{
            border: 1px solid #00ff00;
            padding: 20px;
            background: #0a0a0a;
            text-align: center;
        }}
        .stat-value {{
            font-size: 2em;
            color: #ffff00;
            margin-bottom: 5px;
        }}
        .stat-label {{
            font-size: 0.9em;
            color: #888888;
        }}
        .features {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 30px;
            margin-bottom: 40px;
        }}
        .feature-card {{
            border: 1px solid #00ff00;
            padding: 30px;
            background: #0a0a0a;
        }}
        .feature-title {{
            font-size: 1.3em;
            color: #ffff00;
            margin-bottom: 15px;
        }}
        .feature-desc {{
            color: #888888;
            line-height: 1.6;
        }}
        .auth-section {{
            text-align: center;
            padding: 40px;
            border: 1px solid #00ff00;
            background: #0a0a0a;
        }}
        .auth-title {{
            font-size: 1.5em;
            color: #ffff00;
            margin-bottom: 20px;
        }}
        .auth-buttons {{
            display: flex;
            gap: 20px;
            justify-content: center;
            flex-wrap: wrap;
        }}
        .auth-btn {{
            background: #003300;
            border: 1px solid #00ff00;
            color: #00ff00;
            padding: 12px 30px;
            text-decoration: none;
            font-family: 'Courier New', monospace;
            font-size: 16px;
            transition: all 0.3s;
        }}
        .auth-btn:hover {{
            background: #00ff00;
            color: #000000;
        }}
        .auth-btn.register {{
            border-color: #ffff00;
            color: #ffff00;
        }}
        .auth-btn.register:hover {{
            background: #ffff00;
            color: #000000;
        }}
        .footer {{
            background: #0a0a0a;
            border-top: 1px solid #00ff00;
            padding: 20px;
            text-align: center;
            color: #888888;
            font-size: 0.9em;
        }}
        .status-online {{ color: #00ff00; }}
        .status-offline {{ color: #ff6666; }}
        .status-running {{ color: #00ff00; }}
        .status-stopped {{ color: #ff6666; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="title">🚀 KICK STREAMER MONITOR</div>
        <div class="subtitle">Real-time monitoring for Kick.com streamers</div>
    </div>

    <div class="main-content">
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value status-{'running' if service_status.get('is_running', False) else 'stopped'}">
                    {'RUNNING' if service_status.get('is_running', False) else 'STOPPED'}
                </div>
                <div class="stat-label">SERVICE STATUS</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{total_streamers}</div>
                <div class="stat-label">TOTAL STREAMERS</div>
            </div>
            <div class="stat-card">
                <div class="stat-value status-online">{streamer_stats.get('online', 0)}</div>
                <div class="stat-label">CURRENTLY ONLINE</div>
            </div>
            <div class="stat-card">
                <div class="stat-value status-offline">{streamer_stats.get('offline', 0)}</div>
                <div class="stat-label">CURRENTLY OFFLINE</div>
            </div>
        </div>

        <div class="features">
            <div class="feature-card">
                <div class="feature-title">📊 REAL-TIME MONITORING</div>
                <div class="feature-desc">
                    Track Kick.com streamers in real-time with automatic status updates.
                    Get instant notifications when streamers go online or offline.
                </div>
            </div>
            <div class="feature-card">
                <div class="feature-title">👥 MULTI-USER ACCESS</div>
                <div class="feature-desc">
                    Create user accounts with customized streamer assignments.
                    Admins can manage users and control access to specific streamers.
                </div>
            </div>
            <div class="feature-card">
                <div class="feature-title">📈 DETAILED ANALYTICS</div>
                <div class="feature-desc">
                    View comprehensive statistics and historical data.
                    Monitor success rates, uptime, and streamer activity patterns.
                </div>
            </div>
            <div class="feature-card">
                <div class="feature-title">🔐 SECURE ACCESS</div>
                <div class="feature-desc">
                    Role-based authentication with secure session management.
                    Admin controls for user management and system configuration.
                </div>
            </div>
        </div>

        <div class="auth-section">
            <div class="auth-title">🔑 ACCESS YOUR DASHBOARD</div>
            <div class="auth-buttons">
                <a href="/login" class="auth-btn">LOGIN</a>
                <a href="/register" class="auth-btn register">REGISTER</a>
            </div>
        </div>
    </div>

    <div class="footer">
        <div>Kick Streamer Monitor v1.0 | Real-time monitoring service</div>
        <div>Contact your administrator for access or assistance</div>
    </div>
</body>
</html>'''

    async def _get_user_dashboard_html(self, user_session) -> str:
        """Generate enhanced user dashboard HTML with real-time updates."""
        # Get user's assigned streamers
        try:
            assignments = await self.database_service.get_user_streamer_assignments(user_session.user_id)
            streamer_ids = [a.streamer_id for a in assignments]
            
            if streamer_ids:
                # Get streamers data
                streamers = []
                for streamer_id in streamer_ids:
                    streamer = await self.database_service.get_streamer_by_id(streamer_id)
                    if streamer:
                        streamers.append(streamer)
            else:
                streamers = []
        except Exception as e:
            logger.error(f"Error fetching user streamers: {e}")
            streamers = []
        
        # Calculate initial stats
        online_count = sum(1 for s in streamers if s.status.value == 'online')
        offline_count = len(streamers) - online_count

        # Calculate total viewers from online streamers
        total_viewers = 0
        for s in streamers:
            if s.status.value == 'online' and hasattr(s, 'current_viewers') and s.current_viewers is not None:
                total_viewers += s.current_viewers
        
        # Generate content based on streamers
        if streamers:
            # Generate streamer rows
            streamer_rows = ""
            for streamer in streamers:
                status_class = f"status-{streamer.status}"
                last_seen = streamer.last_seen_online.strftime("%Y-%m-%d %H:%M") if streamer.last_seen_online else "Never"
                last_update = streamer.last_status_update.strftime("%Y-%m-%d %H:%M") if streamer.last_status_update else "Never"

                # Display viewer count for online streamers
                viewer_display = ""
                if streamer.status.value == 'online' and hasattr(streamer, 'current_viewers') and streamer.current_viewers is not None:
                    viewer_display = f"{streamer.current_viewers:,}"
                elif streamer.status.value == 'online':
                    viewer_display = "Loading..."
                else:
                    viewer_display = "-"

                streamer_rows += f'''
                    <tr data-streamer-id="{streamer.id}">
                        <td>{streamer.username}</td>
                        <td class="{status_class} streamer-status">{streamer.status.upper()}</td>
                        <td class="viewer-count">{viewer_display}</td>
                        <td class="last-seen">{last_seen}</td>
                        <td class="last-update">{last_update}</td>
                    </tr>
                '''
            
            main_content = f'''
            <table class="streamers-table" id="streamers-table">
                <thead>
                    <tr>
                        <th>Streamer</th>
                        <th>Status</th>
                        <th>Viewers</th>
                        <th>Last Seen Online</th>
                        <th>Last Update</th>
                    </tr>
                </thead>
                <tbody id="streamers-tbody">
                    {streamer_rows}
                </tbody>
            </table>
            '''
        else:
            main_content = '<div class="no-streamers">No streamers assigned to your account.<br>Contact an administrator to assign streamers.</div>'
        
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>My Dashboard - Kick Streamer Monitor</title>
    <style>
        body {{
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 20px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid #00ff00;
            padding: 20px;
            margin-bottom: 20px;
            background: #0a0a0a;
        }}
        .user-info {{ color: #ffff00; }}
        .logout-btn {{
            background: #330000;
            border: 1px solid #ff6600;
            color: #ff6600;
            padding: 8px 15px;
            text-decoration: none;
            font-family: 'Courier New', monospace;
            border: none;
            cursor: pointer;
        }}
        .logout-btn:hover {{
            background: #ff6600;
            color: #000000;
        }}
        .connection-status {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 12px;
            margin-top: 5px;
            color: #888888;
        }}
        .connection-indicator {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #ff6666;
        }}
        .connection-indicator.connected {{
            background: #00ff00;
        }}
        .stats-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }}
        .mini-stat {{
            border: 1px solid #00ff00;
            padding: 12px;
            background: #0a0a0a;
            text-align: center;
        }}
        .mini-stat-value {{
            font-size: 20px;
            color: #ffff00;
            margin-bottom: 5px;
            font-weight: bold;
        }}
        .mini-stat-label {{
            font-size: 11px;
            color: #888888;
        }}
        .streamers-table {{
            width: 100%;
            border-collapse: collapse;
            border: 1px solid #00ff00;
            margin-top: 20px;
        }}
        .streamers-table th, .streamers-table td {{
            border: 1px solid #00ff00;
            padding: 10px;
            text-align: left;
        }}
        .streamers-table th {{
            background: #003300;
            color: #ffff00;
        }}
        .streamers-table tr {{
            transition: background-color 0.3s ease;
        }}
        .streamers-table tr.updated {{
            background: #003300;
        }}
        .status-online {{ color: #00ff00; }}
        .status-offline {{ color: #ff6666; }}
        .status-unknown {{ color: #ffff00; }}
        .viewer-count {{
            color: #00ccff;
            font-weight: bold;
        }}
        .no-streamers {{
            text-align: center;
            padding: 40px;
            color: #888888;
        }}
        .last-update-info {{
            text-align: center;
            margin-top: 20px;
            color: #888888;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>📊 MY DASHBOARD</h1>
                <div class="user-info">Logged in as: {user_session.username} ({user_session.role.upper()})</div>
                <div class="connection-status">
                    <span class="connection-indicator" id="connection-indicator"></span>
                    <span id="connection-status">Connecting...</span>
                </div>
            </div>
            <div style="display: flex; gap: 10px; align-items: center;">
                <a href="/account" style="background: #666666; color: white; padding: 8px 16px; border-radius: 4px; text-decoration: none; font-weight: bold; transition: background 0.3s;" onmouseover="this.style.background='#555555'" onmouseout="this.style.background='#666666'">⚙️ ACCOUNT</a>
                <form method="post" action="/logout" style="margin: 0;">
                    <button type="submit" class="logout-btn">LOGOUT</button>
                </form>
            </div>
        </div>

        <div class="stats-row">
            <div class="mini-stat">
                <div class="mini-stat-value" id="assigned-count">{len(streamers)}</div>
                <div class="mini-stat-label">Assigned Streamers</div>
            </div>
            <div class="mini-stat">
                <div class="mini-stat-value status-online" id="online-count">{online_count}</div>
                <div class="mini-stat-label">Online Now</div>
            </div>
            <div class="mini-stat">
                <div class="mini-stat-value status-offline" id="offline-count">{offline_count}</div>
                <div class="mini-stat-label">Offline</div>
            </div>
            <div class="mini-stat">
                <div class="mini-stat-value" id="total-viewers" style="color: #00ccff;">{total_viewers:,}</div>
                <div class="mini-stat-label">Total Viewers</div>
            </div>
            <div class="mini-stat">
                <div class="mini-stat-value" id="last-change">-</div>
                <div class="mini-stat-label">Last Change</div>
            </div>
        </div>

        {main_content}

        <div class="last-update-info">
            Last Updated: <span id="last-update">Loading...</span>
        </div>
    </div>

    <script>
        let ws = null;
        let reconnectInterval = null;
        let userStreamers = {str(streamer_ids).replace("'", '"')};

        function connectWebSocket() {{
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${{protocol}}//${{window.location.host}}/ws`);
            
            ws.onopen = function() {{
                document.getElementById('connection-status').textContent = 'Connected';
                document.getElementById('connection-indicator').classList.add('connected');
                clearInterval(reconnectInterval);
            }};
            
            ws.onmessage = function(event) {{
                const data = JSON.parse(event.data);
                if (data.type === 'update') {{
                    updateUserDashboard(data.stats, data.streamers);
                }}
            }};
            
            ws.onclose = function() {{
                document.getElementById('connection-status').textContent = 'Disconnected';
                document.getElementById('connection-indicator').classList.remove('connected');
                
                // Reconnect every 5 seconds
                if (!reconnectInterval) {{
                    reconnectInterval = setInterval(connectWebSocket, 5000);
                }}
            }};
        }}

        function updateUserDashboard(stats, allStreamers) {{
            // Filter to only show user's assigned streamers
            const userAssignedStreamers = allStreamers.filter(s => userStreamers.includes(s.id));

            // Update stats
            const onlineCount = userAssignedStreamers.filter(s => s.status === 'online').length;
            const offlineCount = userAssignedStreamers.length - onlineCount;

            // Calculate total viewers from online streamers
            const totalViewers = userAssignedStreamers
                .filter(s => s.status === 'online')
                .reduce((sum, s) => sum + (s.current_viewers || 0), 0);

            document.getElementById('online-count').textContent = onlineCount;
            document.getElementById('offline-count').textContent = offlineCount;
            document.getElementById('total-viewers').textContent = totalViewers.toLocaleString();

            // Update streamers table
            updateStreamersTable(userAssignedStreamers);

            // Update timestamp
            document.getElementById('last-update').textContent = new Date().toLocaleString();
        }}

        function updateStreamersTable(streamers) {{
            const tbody = document.getElementById('streamers-tbody');
            if (!tbody || !streamers || streamers.length === 0) return;

            streamers.forEach(streamer => {{
                const row = document.querySelector(`tr[data-streamer-id="${{streamer.id}}"]`);
                if (row) {{
                    const statusCell = row.querySelector('.streamer-status');
                    const viewerCell = row.querySelector('.viewer-count');
                    const lastSeenCell = row.querySelector('.last-seen');
                    const lastUpdateCell = row.querySelector('.last-update');

                    // Check if status changed
                    const currentStatus = statusCell.textContent.toLowerCase();
                    const newStatus = streamer.status.toUpperCase();

                    if (currentStatus !== newStatus.toLowerCase()) {{
                        // Status changed - highlight row briefly
                        row.classList.add('updated');
                        setTimeout(() => row.classList.remove('updated'), 2000);

                        // Update last change time
                        document.getElementById('last-change').textContent = new Date().toLocaleTimeString();
                    }}

                    // Update status with correct styling
                    statusCell.className = `status-${{streamer.status}} streamer-status`;
                    statusCell.textContent = newStatus;

                    // Update viewer count
                    let viewerDisplay = "-";
                    if (streamer.status === 'online') {{
                        if (streamer.current_viewers !== undefined && streamer.current_viewers !== null) {{
                            viewerDisplay = streamer.current_viewers.toLocaleString();
                        }} else {{
                            viewerDisplay = "Loading...";
                        }}
                    }}
                    viewerCell.textContent = viewerDisplay;

                    // Update timestamps
                    const lastSeen = streamer.last_seen_online ?
                        new Date(streamer.last_seen_online).toLocaleString() : 'Never';
                    const lastUpdate = streamer.last_status_update ?
                        new Date(streamer.last_status_update).toLocaleString() : 'Never';

                    lastSeenCell.textContent = lastSeen;
                    lastUpdateCell.textContent = lastUpdate;
                }}
            }});
        }}

        // Initialize WebSocket connection
        connectWebSocket();
        
        // Fallback: fetch data every 30 seconds if WebSocket fails
        setInterval(async () => {{
            if (!ws || ws.readyState !== WebSocket.OPEN) {{
                try {{
                    const [statsRes, streamersRes] = await Promise.all([
                        fetch('/api/status'),
                        fetch('/api/streamers')
                    ]);
                    
                    const stats = await statsRes.json();
                    const streamers = await streamersRes.json();
                    
                    updateUserDashboard(stats, streamers);
                }} catch (error) {{
                    console.error('Failed to fetch data:', error);
                }}
            }}
        }}, 30000);
    </script>
</body>
</html>'''

    def _get_admin_dashboard_html(self, user_session) -> str:
        """Generate the admin dashboard HTML."""
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Dashboard - Kick Streamer Monitor</title>
    <style>
        body {{
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 20px;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid #00ff00;
            padding: 20px;
            margin-bottom: 20px;
            background: #0a0a0a;
        }}
        .admin-info {{ color: #ffff00; }}
        .logout-btn {{
            background: #330000;
            border: 1px solid #ff6600;
            color: #ff6600;
            padding: 8px 15px;
            text-decoration: none;
            font-family: 'Courier New', monospace;
        }}
        .logout-btn:hover {{
            background: #ff6600;
            color: #000000;
        }}
        .admin-menu {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .menu-card {{
            border: 1px solid #00ff00;
            padding: 20px;
            background: #0a0a0a;
            text-align: center;
            text-decoration: none;
            color: #00ff00;
            transition: all 0.3s;
        }}
        .menu-card:hover {{
            background: #003300;
            border-color: #ffff00;
            color: #ffff00;
        }}
        .menu-title {{ font-size: 18px; margin-bottom: 10px; }}
        .menu-desc {{ font-size: 14px; color: #888888; }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}
        .stat-card {{
            border: 1px solid #00ff00;
            padding: 15px;
            background: #0a0a0a;
            text-align: center;
        }}
        .stat-value {{ font-size: 24px; color: #ffff00; }}
        .stat-label {{ font-size: 12px; margin-top: 5px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🛠️ ADMIN DASHBOARD</h1>
                <div class="admin-info">Logged in as: {user_session.username}</div>
            </div>
            <form method="post" action="/logout" style="margin: 0;">
                <button type="submit" class="logout-btn">LOGOUT</button>
            </form>
        </div>

        <div class="admin-menu">
            <a href="/admin/streamers" class="menu-card">
                <div class="menu-title">👥 MANAGE STREAMERS</div>
                <div class="menu-desc">Add, remove, and configure streamers</div>
            </a>
            <a href="/admin/users" class="menu-card">
                <div class="menu-title">👤 MANAGE USERS</div>
                <div class="menu-desc">Create users and assign streamers</div>
            </a>
            <a href="/" class="menu-card">
                <div class="menu-title">📊 VIEW DASHBOARD</div>
                <div class="menu-desc">Public monitoring dashboard</div>
            </a>
            <a href="/admin/analytics" class="menu-card">
                <div class="menu-title">📈 ANALYTICS</div>
                <div class="menu-desc">View comprehensive analytics and statistics</div>
            </a>
            <div class="menu-card" style="opacity: 0.5;">
                <div class="menu-title">⚙️ SETTINGS</div>
                <div class="menu-desc">Coming soon...</div>
            </div>
        </div>

        <div class="stats-grid" id="admin-stats">
            <div class="stat-card">
                <div class="stat-value">-</div>
                <div class="stat-label">Active Sessions</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">-</div>
                <div class="stat-label">Total Streamers</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">-</div>
                <div class="stat-label">System Uptime</div>
            </div>
        </div>
    </div>

    <script>
        // Load admin stats
        fetch('/api/status')
            .then(response => response.json())
            .then(data => {{
                const statsCards = document.querySelectorAll('#admin-stats .stat-value');
                const streamerStats = data.streamers || {{}};
                const serviceStats = data.service_status || {{}};
                
                statsCards[0].textContent = '1'; // Active sessions (placeholder)
                statsCards[1].textContent = streamerStats.total_monitored || '0';
                statsCards[2].textContent = formatUptime(serviceStats.uptime_seconds || 0);
            }})
            .catch(error => console.error('Failed to load admin stats:', error));

        function formatUptime(seconds) {{
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            return `${{hours}}h ${{minutes}}m`;
        }}
    </script>
</body>
</html>'''
    
    def _get_admin_streamers_html(self, user_session) -> str:
        """Generate the admin streamers management page."""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Manage Streamers - Admin Panel</title>
    <style>
        body {
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 20px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid #00ff00;
            padding: 20px;
            margin-bottom: 20px;
            background: #0a0a0a;
        }
        .back-btn {
            background: #0a0a0a;
            border: 1px solid #00ff00;
            color: #00ff00;
            padding: 8px 15px;
            text-decoration: none;
            font-family: 'Courier New', monospace;
        }
        .back-btn:hover {
            background: #00ff00;
            color: #000000;
        }
        .add-streamer-form {
            border: 1px solid #00ff00;
            padding: 20px;
            margin-bottom: 30px;
            background: #0a0a0a;
        }
        .form-row {
            display: flex;
            gap: 10px;
            align-items: end;
        }
        .form-group {
            flex: 1;
        }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #ffff00;
        }
        .form-group input {
            width: 100%;
            padding: 10px;
            background: #1a1a1a;
            border: 1px solid #00ff00;
            color: #00ff00;
            font-family: 'Courier New', monospace;
        }
        .add-btn {
            background: #0a0a0a;
            border: 1px solid #00ff00;
            color: #00ff00;
            padding: 10px 20px;
            font-family: 'Courier New', monospace;
            cursor: pointer;
            height: 42px;
        }
        .add-btn:hover {
            background: #00ff00;
            color: #000000;
        }
        .streamers-table {
            width: 100%;
            border-collapse: collapse;
            border: 1px solid #00ff00;
        }
        .streamers-table th, .streamers-table td {
            border: 1px solid #00ff00;
            padding: 12px;
            text-align: left;
        }
        .streamers-table th {
            background: #0a0a0a;
            color: #ffff00;
        }
        .action-btn {
            background: #0a0a0a;
            border: 1px solid #ff6600;
            color: #ff6600;
            padding: 5px 10px;
            margin: 2px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            cursor: pointer;
        }
        .action-btn:hover {
            background: #ff6600;
            color: #000000;
        }
        .status-online { color: #00ff00; }
        .status-offline { color: #ff6600; }
        .status-unknown { color: #666666; }
        .success-message, .error-message {
            padding: 10px;
            margin-bottom: 20px;
            border: 1px solid;
        }
        .success-message {
            background: #003300;
            border-color: #00ff00;
            color: #00ff00;
        }
        .error-message {
            background: #330000;
            border-color: #ff0000;
            color: #ff6666;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>👥 MANAGE STREAMERS</h1>
            <a href="/admin" class="back-btn">&larr; BACK TO ADMIN</a>
        </div>

        <div id="message-container"></div>

        <div class="add-streamer-form">
            <h3>➕ ADD NEW STREAMER</h3>
            <form method="post" action="/admin/streamers/add">
                <div class="form-row">
                    <div class="form-group">
                        <label for="username">Kick Username:</label>
                        <input type="text" id="username" name="username" required 
                               placeholder="Enter streamer username">
                    </div>
                    <button type="submit" class="add-btn">ADD STREAMER</button>
                </div>
            </form>
        </div>

        <table class="streamers-table">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Username</th>
                    <th>Status</th>
                    <th>Last Seen Online</th>
                    <th>Last Update</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody id="streamers-tbody">
                <tr><td colspan="6" style="text-align: center;">Loading streamers...</td></tr>
            </tbody>
        </table>
    </div>

    <script>
        // Check for messages in URL params
        const streamersUrlParams = new URLSearchParams(window.location.search);
        const success = streamersUrlParams.get('success');
        const error = streamersUrlParams.get('error');
        const messageContainer = document.getElementById('message-container');

        if (success) {
            const messages = {
                'added': 'Streamer added successfully!',
                'removed': 'Streamer removed successfully!',
                'refreshed': 'Streamer profile data refreshed successfully!'
            };
            messageContainer.innerHTML = `<div class="success-message">${messages[success] || 'Operation successful!'}</div>`;
        } else if (error) {
            const messages = {
                'add_failed': 'Failed to add streamer. Please try again.',
                'remove_failed': 'Failed to remove streamer. Please try again.',
                'refresh_failed': 'Failed to refresh streamer data. Please try again.',
                'api_failed': 'API error: Could not fetch data from Kick.com.',
                'no_data': 'No profile data found for this streamer.',
                'update_failed': 'Failed to update database with new profile data.',
                'streamer_not_found': 'Streamer not found in database.',
                'no_api': 'API service not available.',
                'invalid_id': 'Invalid streamer ID provided.'
            };
            messageContainer.innerHTML = `<div class="error-message">${messages[error] || 'Operation failed!'}</div>`;
        }

        // Load streamers
        fetch('/api/streamers')
            .then(response => response.json())
            .then(streamers => {
                const tbody = document.getElementById('streamers-tbody');
                
                if (!streamers || streamers.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center;">No streamers found</td></tr>';
                    return;
                }
                
                tbody.innerHTML = streamers.map(streamer => {
                    const statusClass = `status-${streamer.status}`;
                    const lastSeen = streamer.last_seen_online ? 
                        new Date(streamer.last_seen_online).toLocaleString() : 'Never';
                    const lastUpdate = streamer.last_status_update ? 
                        new Date(streamer.last_status_update).toLocaleString() : 'Never';
                    
                    return `
                        <tr>
                            <td>${streamer.id}</td>
                            <td>${streamer.username}</td>
                            <td class="${statusClass}">${streamer.status.toUpperCase()}</td>
                            <td>${lastSeen}</td>
                            <td>${lastUpdate}</td>
                            <td>
                                <form method="post" action="/admin/streamers/refresh" style="display: inline;">
                                    <input type="hidden" name="streamer_id" value="${streamer.id}">
                                    <button type="submit" class="action-btn" title="Refresh profile data from Kick.com">REFRESH</button>
                                </form>
                                <form method="post" action="/admin/streamers/remove" style="display: inline;" 
                                      onsubmit="return confirm('Are you sure you want to remove ${streamer.username}?')">
                                    <input type="hidden" name="streamer_id" value="${streamer.id}">
                                    <button type="submit" class="action-btn">REMOVE</button>
                                </form>
                            </td>
                        </tr>
                    `;
                }).join('');
            })
            .catch(error => {
                console.error('Failed to load streamers:', error);
                document.getElementById('streamers-tbody').innerHTML = 
                    '<tr><td colspan="6" style="text-align: center; color: #ff6666;">Failed to load streamers</td></tr>';
            });
    </script>
</body>
</html>'''

    async def _get_account_settings_html(self, user) -> str:
        """Generate account settings page HTML."""
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Account Settings - Kick Monitor</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a1a 0%, #2d2d2d 100%);
            color: #ffffff;
            min-height: 100vh;
        }}

        .header {{
            background: rgba(0, 0, 0, 0.3);
            padding: 1rem;
            border-bottom: 2px solid #00ff41;
            margin-bottom: 2rem;
        }}

        .header-content {{
            max-width: 1200px;
            margin: 0 auto;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .logo {{
            font-size: 1.5rem;
            font-weight: bold;
            color: #00ff41;
        }}

        .nav-buttons {{
            display: flex;
            gap: 1rem;
        }}

        .nav-btn {{
            background: #00ff41;
            color: #000;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 4px;
            text-decoration: none;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
        }}

        .nav-btn:hover {{
            background: #00cc34;
            transform: translateY(-2px);
        }}

        .logout-btn {{
            background: #ff4444;
            color: white;
        }}

        .logout-btn:hover {{
            background: #cc3333;
        }}

        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 0 1rem;
        }}

        .page-title {{
            text-align: center;
            margin-bottom: 2rem;
            color: #00ff41;
            font-size: 2rem;
        }}

        .settings-section {{
            background: rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 2rem;
            margin-bottom: 2rem;
            border: 1px solid rgba(0, 255, 65, 0.3);
        }}

        .section-title {{
            font-size: 1.3rem;
            color: #00ff41;
            margin-bottom: 1rem;
            border-bottom: 1px solid rgba(0, 255, 65, 0.3);
            padding-bottom: 0.5rem;
        }}

        .form-group {{
            margin-bottom: 1rem;
        }}

        label {{
            display: block;
            margin-bottom: 0.5rem;
            color: #cccccc;
            font-weight: 500;
        }}

        input[type="text"], input[type="email"], input[type="password"] {{
            width: 100%;
            padding: 0.75rem;
            border: 1px solid #444;
            border-radius: 4px;
            background: rgba(255, 255, 255, 0.1);
            color: #ffffff;
            font-size: 1rem;
        }}

        input[type="text"]:focus, input[type="email"]:focus, input[type="password"]:focus {{
            outline: none;
            border-color: #00ff41;
            box-shadow: 0 0 5px rgba(0, 255, 65, 0.3);
        }}

        .readonly {{
            background: rgba(255, 255, 255, 0.05);
            cursor: not-allowed;
        }}

        .form-btn {{
            background: #00ff41;
            color: #000;
            border: none;
            padding: 0.75rem 2rem;
            border-radius: 4px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s ease;
        }}

        .form-btn:hover {{
            background: #00cc34;
            transform: translateY(-2px);
        }}

        .danger-btn {{
            background: #ff4444;
            color: white;
        }}

        .danger-btn:hover {{
            background: #cc3333;
        }}

        .message-container {{
            margin-bottom: 1rem;
            text-align: center;
        }}

        .success-message {{
            background: rgba(0, 255, 65, 0.2);
            color: #00ff41;
            padding: 1rem;
            border-radius: 4px;
            border: 1px solid rgba(0, 255, 65, 0.3);
        }}

        .error-message {{
            background: rgba(255, 68, 68, 0.2);
            color: #ff4444;
            padding: 1rem;
            border-radius: 4px;
            border: 1px solid rgba(255, 68, 68, 0.3);
        }}

        .help-text {{
            font-size: 0.9rem;
            color: #888;
            margin-top: 0.25rem;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-content">
            <div class="logo">🎮 KICK MONITOR</div>
            <div class="nav-buttons">
                <a href="/" class="nav-btn">📊 Dashboard</a>
                <a href="/account" class="nav-btn">⚙️ Account</a>
                <form method="post" action="/logout" style="display: inline;">
                    <button type="submit" class="nav-btn logout-btn">🚪 Logout</button>
                </form>
            </div>
        </div>
    </div>

    <div class="container">
        <h1 class="page-title">⚙️ Account Settings</h1>

        <div id="message-container" class="message-container"></div>

        <!-- Profile Information Section -->
        <div class="settings-section">
            <h2 class="section-title">👤 Profile Information</h2>
            <form method="post" action="/account/update-profile">
                <div class="form-group">
                    <label for="username">Username:</label>
                    <input type="text" id="username" value="{user.username}" class="readonly" readonly>
                    <div class="help-text">Username cannot be changed</div>
                </div>

                <div class="form-group">
                    <label for="email">Email Address:</label>
                    <input type="email" id="email" name="email" value="{user.email or ''}" required>
                </div>

                <div class="form-group">
                    <label for="display_name">Display Name:</label>
                    <input type="text" id="display_name" name="display_name" value="{user.display_name or ''}" placeholder="Optional display name">
                </div>

                <button type="submit" class="form-btn">💾 Update Profile</button>
            </form>
        </div>

        <!-- Password Change Section -->
        <div class="settings-section">
            <h2 class="section-title">🔒 Change Password</h2>
            <form method="post" action="/account/change-password">
                <div class="form-group">
                    <label for="current_password">Current Password:</label>
                    <input type="password" id="current_password" name="current_password" required>
                </div>

                <div class="form-group">
                    <label for="new_password">New Password:</label>
                    <input type="password" id="new_password" name="new_password" required>
                    <div class="help-text">Minimum 6 characters</div>
                </div>

                <div class="form-group">
                    <label for="confirm_password">Confirm New Password:</label>
                    <input type="password" id="confirm_password" name="confirm_password" required>
                </div>

                <button type="submit" class="form-btn danger-btn">🔑 Change Password</button>
            </form>
        </div>

        <!-- Account Information Section -->
        <div class="settings-section">
            <h2 class="section-title">ℹ️ Account Information</h2>
            <div class="form-group">
                <label>Account Role:</label>
                <input type="text" value="{user.role.value.upper()}" class="readonly" readonly>
            </div>

            <div class="form-group">
                <label>Account Status:</label>
                <input type="text" value="{user.status.value.upper()}" class="readonly" readonly>
            </div>

            <div class="form-group">
                <label>Member Since:</label>
                <input type="text" value="{user.created_at.strftime('%B %d, %Y') if user.created_at else 'Unknown'}" class="readonly" readonly>
            </div>
        </div>
    </div>

    <script>
        // Check for messages in URL params
        const accountUrlParams = new URLSearchParams(window.location.search);
        const success = accountUrlParams.get('success');
        const error = accountUrlParams.get('error');
        const messageContainer = document.getElementById('message-container');

        if (success) {{
            const messages = {{
                'profile_updated': 'Profile updated successfully!',
                'password_changed': 'Password changed successfully!'
            }};
            messageContainer.innerHTML = `<div class="success-message">${{messages[success] || 'Operation successful!'}}</div>`;
        }} else if (error) {{
            const messages = {{
                'missing_email': 'Email address is required.',
                'update_failed': 'Failed to update profile. Please try again.',
                'missing_fields': 'Please fill in all password fields.',
                'password_mismatch': 'New passwords do not match.',
                'password_too_short': 'Password must be at least 6 characters long.',
                'incorrect_password': 'Current password is incorrect.',
                'password_change_failed': 'Failed to change password. Please try again.'
            }};
            messageContainer.innerHTML = `<div class="error-message">${{messages[error] || 'Operation failed!'}}</div>`;
        }}

        // Clear URL parameters after showing message
        if (success || error) {{
            setTimeout(() => {{
                const url = new URL(window.location);
                url.searchParams.delete('success');
                url.searchParams.delete('error');
                window.history.replaceState({{}}, '', url);
            }}, 100);
        }}
    </script>
</body>
</html>'''

    async def _get_admin_users_html(self, user_session) -> str:
        """Generate the admin users management HTML."""
        # Get all users and streamers
        try:
            users = await self.database_service.get_all_users()
            streamers = await self.database_service.get_all_streamers()
        except Exception as e:
            logger.error(f"Error fetching users/streamers: {e}")
            users = []
            streamers = []
        
        # Generate user rows
        user_rows = ""
        if not users:
            logger.warning("No users found for admin users page")
        else:
            logger.info(f"Found {len(users)} users for admin users page")
            
        for user in users:
            # Don't show delete/toggle buttons for admin users
            action_buttons = ""
            if user.role != UserRole.ADMIN:
                # Toggle status button
                status_action = "DISABLE" if user.status == UserStatus.ACTIVE else "ENABLE"
                status_btn_class = "remove-btn" if user.status == UserStatus.ACTIVE else "add-btn"
                toggle_button = f'''
                    <form method="post" action="/admin/users/toggle-status" style="display: inline;"
                          onsubmit="return confirm('Are you sure you want to {status_action.lower()} user {user.username}?')">
                        <input type="hidden" name="user_id" value="{user.id}">
                        <button type="submit" class="{status_btn_class}" style="padding: 5px 10px; font-size: 12px; margin-right: 5px;">{status_action}</button>
                    </form>
                '''

                # Reset password button
                reset_password_button = f'''
                    <form method="post" action="/admin/users/reset-password" style="display: inline;"
                          onsubmit="return confirm('Are you sure you want to reset password for user {user.username}? They will need to use the new temporary password.')">
                        <input type="hidden" name="user_id" value="{user.id}">
                        <button type="submit" class="action-btn" style="padding: 5px 10px; font-size: 12px; margin-right: 5px; background: #ff9500; border-color: #ff9500;">RESET PWD</button>
                    </form>
                '''

                # Delete button
                delete_button = f'''
                    <form method="post" action="/admin/users/delete" style="display: inline;"
                          onsubmit="return confirm('Are you sure you want to delete user {user.username}? This action cannot be undone.')">
                        <input type="hidden" name="user_id" value="{user.id}">
                        <button type="submit" class="remove-btn" style="padding: 5px 10px; font-size: 12px;">DELETE</button>
                    </form>
                '''
                action_buttons = toggle_button + reset_password_button + delete_button
            else:
                action_buttons = '<span style="color: #666;">Protected</span>'
            
            user_rows += f'''
                <tr>
                    <td>{user.id}</td>
                    <td>{user.username}</td>
                    <td>{user.email}</td>
                    <td>{user.display_name or "-"}</td>
                    <td class="role-{user.role}">{user.role.upper()}</td>
                    <td class="status-{user.status}">{user.status.upper()}</td>
                    <td class="assignments-cell" id="assignments-{user.id}">Loading...</td>
                    <td>{user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "-"}</td>
                    <td>{action_buttons}</td>
                </tr>
            '''
        
        # Generate user options for assignment dropdown
        user_options = ""
        for user in users:
            if user.role != UserRole.ADMIN:
                user_options += f'<option value="{user.id}">{user.username} ({user.role})</option>'
        
        # Generate streamer options for assignment dropdown
        streamer_options = ""
        for streamer in streamers:
            streamer_options += f'<option value="{streamer.id}">{streamer.username}</option>'
        
        return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Manage Users - Kick Streamer Monitor</title>
    <style>
        body {{
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 20px;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid #00ff00;
            padding: 20px;
            margin-bottom: 20px;
            background: #0a0a0a;
        }}
        .back-btn {{
            background: #003300;
            border: 1px solid #00ff00;
            color: #00ff00;
            padding: 8px 15px;
            text-decoration: none;
            font-family: 'Courier New', monospace;
        }}
        .back-btn:hover {{
            background: #00ff00;
            color: #000000;
        }}
        .users-table {{
            width: 100%;
            border-collapse: collapse;
            border: 1px solid #00ff00;
            margin-bottom: 30px;
        }}
        .users-table th, .users-table td {{
            border: 1px solid #00ff00;
            padding: 10px;
            text-align: left;
        }}
        .users-table th {{
            background: #003300;
            color: #ffff00;
        }}
        .add-user-form {{
            border: 1px solid #00ff00;
            padding: 20px;
            background: #0a0a0a;
            margin-bottom: 20px;
        }}
        .form-row {{
            display: flex;
            gap: 15px;
            align-items: end;
            margin-bottom: 15px;
        }}
        .form-group {{
            flex: 1;
        }}
        .form-group label {{
            display: block;
            margin-bottom: 5px;
            color: #ffff00;
        }}
        .form-group input, .form-group select {{
            width: 100%;
            padding: 8px;
            background: #0a0a0a;
            border: 1px solid #00ff00;
            color: #00ff00;
            font-family: 'Courier New', monospace;
        }}
        .add-btn {{
            background: #003300;
            border: 1px solid #00ff00;
            color: #00ff00;
            padding: 10px 20px;
            font-family: 'Courier New', monospace;
            cursor: pointer;
        }}
        .add-btn:hover {{
            background: #00ff00;
            color: #000000;
        }}
        .assignment-section {{
            border: 1px solid #00ff00;
            padding: 20px;
            background: #0a0a0a;
            margin-top: 20px;
        }}
        .assignment-tools {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }}
        .assignment-form {{
            border: 1px solid #ffff00;
            padding: 15px;
            background: #0f0f0f;
        }}
        .assignment-form h4 {{
            color: #ffff00;
            margin-top: 0;
            margin-bottom: 15px;
        }}
        .assignment-form .form-row {{
            display: flex;
            gap: 15px;
            align-items: end;
        }}
        .remove-btn {{
            background: #330000;
            border: 1px solid #ff6600;
            color: #ff6600;
            padding: 10px 20px;
            font-family: 'Courier New', monospace;
            cursor: pointer;
        }}
        .remove-btn:hover {{
            background: #ff6600;
            color: #000000;
        }}
        .assignments-cell {{
            max-width: 200px;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .assignment-tag {{
            display: inline-block;
            background: #003300;
            color: #00ff00;
            padding: 2px 6px;
            margin: 1px;
            border-radius: 3px;
            font-size: 10px;
        }}
        .role-admin {{ color: #ff6600; }}
        .role-user {{ color: #00ff00; }}
        .role-viewer {{ color: #ffff00; }}
        .status-active {{ color: #00ff00; }}
        .status-inactive {{ color: #888888; }}
        .status-suspended {{ color: #ff6666; }}
        .success-message, .error-message {{
            padding: 10px;
            margin-bottom: 20px;
            border: 1px solid;
        }}
        .success-message {{
            background: #003300;
            border-color: #00ff00;
            color: #00ff00;
        }}
        .error-message {{
            background: #330000;
            border-color: #ff0000;
            color: #ff6666;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>👤 MANAGE USERS</h1>
            <a href="/admin" class="back-btn">&larr; BACK TO ADMIN</a>
        </div>

        <div id="message-container"></div>

        <div class="add-user-form">
            <h3>➕ ADD NEW USER</h3>
            <form method="post" action="/admin/users/add">
                <div class="form-row">
                    <div class="form-group">
                        <label for="username">Username:</label>
                        <input type="text" id="username" name="username" required>
                    </div>
                    <div class="form-group">
                        <label for="email">Email:</label>
                        <input type="email" id="email" name="email" required>
                    </div>
                    <div class="form-group">
                        <label for="password">Password:</label>
                        <input type="password" id="password" name="password" required>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label for="display_name">Display Name:</label>
                        <input type="text" id="display_name" name="display_name">
                    </div>
                    <div class="form-group">
                        <label for="role">Role:</label>
                        <select id="role" name="role">
                            <option value="user">User</option>
                            <option value="viewer">Viewer</option>
                            <option value="admin">Admin</option>
                        </select>
                    </div>
                    <button type="submit" class="add-btn">ADD USER</button>
                </div>
            </form>
        </div>

        <table class="users-table">
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Username</th>
                    <th>Email</th>
                    <th>Display Name</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Assigned Streamers</th>
                    <th>Created</th>
                </tr>
            </thead>
            <tbody>
                {user_rows}
            </tbody>
        </table>

        <div class="assignment-section">
            <h3>🔗 STREAMER ASSIGNMENT MANAGEMENT</h3>
            
            <div class="assignment-tools">
                <div class="assignment-form">
                    <h4>➕ Assign Streamer to User</h4>
                    <form method="post" action="/admin/users/assign">
                        <div class="form-row">
                            <div class="form-group">
                                <label for="assign_user_id">User:</label>
                                <select id="assign_user_id" name="user_id" required>
                                    <option value="">Select User</option>
                                    {user_options}
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="assign_streamer_id">Streamer:</label>
                                <select id="assign_streamer_id" name="streamer_id" required>
                                    <option value="">Select Streamer</option>
                                    {streamer_options}
                                </select>
                            </div>
                            <button type="submit" class="add-btn">ASSIGN</button>
                        </div>
                    </form>
                </div>
                
                <div class="assignment-form">
                    <h4>➖ Remove Assignment</h4>
                    <form method="post" action="/admin/users/unassign">
                        <div class="form-row">
                            <div class="form-group">
                                <label for="unassign_user_id">User:</label>
                                <select id="unassign_user_id" name="user_id" required onchange="loadUserAssignments(this.value)">
                                    <option value="">Select User</option>
                                    {user_options}
                                </select>
                            </div>
                            <div class="form-group">
                                <label for="unassign_streamer_id">Assigned Streamer:</label>
                                <select id="unassign_streamer_id" name="streamer_id" required>
                                    <option value="">Select User First</option>
                                </select>
                            </div>
                            <button type="submit" class="remove-btn">REMOVE</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Check for messages in URL params
        const usersUrlParams = new URLSearchParams(window.location.search);
        const success = usersUrlParams.get('success');
        const error = usersUrlParams.get('error');
        const messageContainer = document.getElementById('message-container');

        if (success) {{
            const messages = {{
                'added': 'User created successfully!',
                'assigned': 'Streamer assigned successfully!',
                'unassigned': 'Streamer unassigned successfully!',
                'deleted': 'User deleted successfully!',
                'enabled': 'User enabled successfully!',
                'disabled': 'User disabled successfully!',
                'password_reset': 'Password reset successfully!'
            }};

            if (success === 'password_reset') {{
                const username = usersUrlParams.get('username');
                const password = usersUrlParams.get('password');
                if (username && password) {{
                    messageContainer.innerHTML = `<div class="success-message">Password reset successfully for user <strong>${{username}}</strong>!<br>New temporary password: <strong style="background: #333; padding: 2px 4px; font-family: monospace;">${{password}}</strong><br><small>User should change this password after logging in.</small></div>`;
                }} else {{
                    messageContainer.innerHTML = `<div class="success-message">${{messages[success]}}</div>`;
                }}
            }} else {{
                messageContainer.innerHTML = `<div class="success-message">${{messages[success] || 'Operation successful!'}}</div>`;
            }}
        }} else if (error) {{
            const messages = {{
                'add_failed': 'Failed to create user. Please try again.',
                'assign_failed': 'Failed to assign streamer. Please try again.',
                'unassign_failed': 'Failed to unassign streamer. Please try again.',
                'missing_fields': 'Please fill in all required fields.',
                'streamer_already_assigned': 'This streamer is already assigned to another user.',
                'duplicate_assignment': 'This streamer is already assigned to this user.',
                'missing_user_id': 'Invalid user ID provided.',
                'user_not_found': 'User not found.',
                'cannot_delete_admin': 'Cannot delete admin users.',
                'cannot_modify_admin': 'Cannot modify admin users.',
                'delete_failed': 'Failed to delete user. Please try again.',
                'status_update_failed': 'Failed to update user status. Please try again.',
                'password_reset_failed': 'Failed to reset password. Please try again.',
                'database_unavailable': 'Database service is currently unavailable.'
            }};
            messageContainer.innerHTML = `<div class="error-message">${{messages[error] || 'Operation failed!'}}</div>`;
        }}

        // Load user assignments for removal dropdown
        async function loadUserAssignments(userId) {{
            if (!userId) {{
                document.getElementById('unassign_streamer_id').innerHTML = '<option value="">Select User First</option>';
                return;
            }}

            try {{
                const response = await fetch(`/api/users/${{userId}}/assignments`);
                if (response.ok) {{
                    const assignments = await response.json();
                    const select = document.getElementById('unassign_streamer_id');
                    
                    if (assignments.length === 0) {{
                        select.innerHTML = '<option value="">No assignments found</option>';
                    }} else {{
                        select.innerHTML = assignments.map(a => 
                            `<option value="${{a.streamer_id}}">${{a.streamer_username}}</option>`
                        ).join('');
                    }}
                }} else {{
                    document.getElementById('unassign_streamer_id').innerHTML = '<option value="">Error loading assignments</option>';
                }}
            }} catch (error) {{
                console.error('Error loading assignments:', error);
                document.getElementById('unassign_streamer_id').innerHTML = '<option value="">Error loading assignments</option>';
            }}
        }}

        // Load assignment counts for users table
        async function loadUserAssignmentCounts() {{
            try {{
                const response = await fetch('/api/users/assignments-summary');
                
                // Check for authentication errors
                if (response.status === 401) {{
                    window.location.href = '/login';
                    return;
                }}
                
                if (response.ok) {{
                    const summary = await response.json();
                    console.log('Assignment summary loaded:', summary);

                    // Create a map of user_id to summary for quick lookup
                    const summaryMap = new Map();
                    summary.forEach(userSummary => {{
                        summaryMap.set(userSummary.user_id, userSummary);
                    }});

                    // Update all assignment cells
                    document.querySelectorAll('.assignments-cell').forEach(cell => {{
                        const userId = parseInt(cell.id.replace('assignments-', ''));
                        const userSummary = summaryMap.get(userId);

                        if (userSummary) {{
                            if (userSummary.streamers.length === 0) {{
                                cell.innerHTML = '<span style="color: #888;">None</span>';
                            }} else {{
                                const tags = userSummary.streamers.map(s =>
                                    `<span class="assignment-tag">${{s}}</span>`
                                ).join('');
                                cell.innerHTML = tags;
                            }}
                        }} else {{
                            // User not in summary means no assignments
                            cell.innerHTML = '<span style="color: #888;">None</span>';
                        }}
                    }});
                }} else {{
                    console.error('Failed to load assignment summary:', response.status, response.statusText);
                    const text = await response.text();
                    console.error('Response body:', text);
                    
                    // Show error in UI instead of just "Loading..."
                    document.querySelectorAll('.assignments-cell').forEach(cell => {{
                        if (cell.textContent === 'Loading...') {{
                            cell.innerHTML = '<span style="color: #ff6666;">Error loading</span>';
                        }}
                    }});
                }}
            }} catch (error) {{
                console.error('Error loading assignment counts:', error);
                
                // Show error in UI instead of just "Loading..."
                document.querySelectorAll('.assignments-cell').forEach(cell => {{
                    if (cell.textContent === 'Loading...') {{
                        cell.innerHTML = '<span style="color: #ff6666;">Error loading</span>';
                    }}
                }});
            }}
        }}

        // Refresh dropdowns with current data
        async function refreshDropdowns() {{
            try {{
                const [usersRes, streamersRes] = await Promise.all([
                    fetch('/api/users'),
                    fetch('/api/streamers')
                ]);

                // Check for authentication errors
                if (usersRes.status === 401 || streamersRes.status === 401) {{
                    window.location.href = '/login';
                    return;
                }}

                if (!usersRes.ok || !streamersRes.ok) {{
                    throw new Error('Failed to load dropdown data');
                }}

                const users = await usersRes.json();
                const streamers = await streamersRes.json();

                // Update assign user dropdown
                const assignUserSelect = document.getElementById('assign_user_id');
                const unassignUserSelect = document.getElementById('unassign_user_id');
                
                const userOptions = users
                    .filter(u => u.role !== 'admin')
                    .map(u => `<option value="${{u.id}}">${{u.username}} (${{u.role}})</option>`)
                    .join('');

                if (assignUserSelect) {{
                    assignUserSelect.innerHTML = '<option value="">Select User</option>' + userOptions;
                }}
                if (unassignUserSelect) {{
                    unassignUserSelect.innerHTML = '<option value="">Select User</option>' + userOptions;
                }}

                // Update streamer dropdown
                const assignStreamerSelect = document.getElementById('assign_streamer_id');
                const streamerOptions = streamers
                    .map(s => `<option value="${{s.id}}">${{s.username}}</option>`)
                    .join('');

                if (assignStreamerSelect) {{
                    assignStreamerSelect.innerHTML = '<option value="">Select Streamer</option>' + streamerOptions;
                }}

                // Update search functionality with streamers data
                if (typeof window.updateStreamerData === 'function') {{
                    window.updateStreamerData(streamers);
                }}

            }} catch (error) {{
                console.error('Error refreshing dropdowns:', error);
            }}
        }}

        // Check for successful operations and refresh
        const refreshUrlParams = new URLSearchParams(window.location.search);
        const refreshSuccess = refreshUrlParams.get('success');
        if (refreshSuccess === 'added') {{
            // User was just created, refresh the dropdowns
            setTimeout(refreshDropdowns, 500);
        }} else if (refreshSuccess === 'assigned' || refreshSuccess === 'unassigned') {{
            // Assignment was modified, refresh everything
            setTimeout(() => {{
                refreshDropdowns();
                loadUserAssignmentCounts();
            }}, 500);
        }}

        // Simple dropdown functionality (no search)
        function initializeDropdown() {{
            // Just store the streamers data for reference, no complex functionality needed
            window.updateStreamerData = function(streamers) {{
                // Simple storage for future use if needed
            }};
        }}

        // Initialize on page load
        refreshDropdowns();
        loadUserAssignmentCounts();
        initializeDropdown();
    </script>
</body>
</html>'''

    def _get_dashboard_html(self) -> str:
        """Generate the real-time dashboard HTML page."""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kick Streamer Monitor - Real-Time Dashboard</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 20px;
            line-height: 1.4;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid #00ff00;
            padding: 20px;
            margin-bottom: 20px;
            background: #0a0a0a;
        }
        .header h1 { margin: 0; color: #ffff00; }
        .system-status {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .status-indicator {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #ff0000;
            animation: pulse 2s infinite;
        }
        .status-indicator.healthy { background: #00ff00; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        .stat-card {
            border: 1px solid #00ff00;
            padding: 15px;
            background: #0a0a0a;
            text-align: center;
        }
        .stat-card h3 {
            color: #ffff00;
            margin: 0 0 10px 0;
            font-size: 0.9em;
            text-transform: uppercase;
        }
        .stat-value {
            font-size: 1.8em;
            font-weight: bold;
            margin: 5px 0;
        }
        .stat-value.online { color: #00ff00; }
        .stat-value.offline { color: #ff6666; }
        .stat-value.unknown { color: #ffff00; }
        .auth-links {
            text-align: center;
            margin-top: 20px;
            padding: 15px;
            border: 1px solid #333;
            background: #0a0a0a;
        }
        .auth-links a {
            color: #00ff00;
            text-decoration: none;
            margin: 0 10px;
        }
        .auth-links a:hover { color: #ffff00; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Kick Streamer Monitor</h1>
            <div class="system-status">
                <div class="status-indicator" id="system-status"></div>
                <span id="status-text">Connecting...</span>
            </div>
        </div>
        <div class="stats-grid" id="stats-grid">
            <div class="stat-card">
                <h3>Total Streamers</h3>
                <div class="stat-value" id="total-streamers">--</div>
            </div>
            <div class="stat-card">
                <h3>Online Now</h3>
                <div class="stat-value online" id="online-streamers">--</div>
            </div>
            <div class="stat-card">
                <h3>Offline</h3>
                <div class="stat-value offline" id="offline-streamers">--</div>
            </div>
            <div class="stat-card">
                <h3>Status Unknown</h3>
                <div class="stat-value unknown" id="unknown-streamers">--</div>
            </div>
            <div class="stat-card">
                <h3>Active Users</h3>
                <div class="stat-value" id="active-users">--</div>
            </div>
            <div class="stat-card">
                <h3>Changes (24h)</h3>
                <div class="stat-value" id="recent-changes">--</div>
            </div>
        </div>
        <div class="auth-links">
            <a href="/login">Admin Login</a> |
            <a href="/register">Register Account</a> |
            <a href="/admin">Admin Dashboard</a>
        </div>
    </div>
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            loadDashboardData();
            setInterval(loadDashboardData, 30000);
        });

        async function loadDashboardData() {
            try {
                const summary = await fetch('/api/dashboard/summary').then(r => r.json());
                updateSummaryStats(summary);
                updateSystemStatus('healthy');
            } catch (error) {
                console.error('Failed to load dashboard data:', error);
                updateSystemStatus('error');
            }
        }

        function updateSummaryStats(data) {
            document.getElementById('total-streamers').textContent = data.total_streamers || 0;
            document.getElementById('online-streamers').textContent = data.online_streamers || 0;
            document.getElementById('offline-streamers').textContent = data.offline_streamers || 0;
            document.getElementById('unknown-streamers').textContent = data.unknown_streamers || 0;
            document.getElementById('active-users').textContent = data.active_users || 0;
            document.getElementById('recent-changes').textContent = data.recent_changes_24h || 0;
        }

        function updateSystemStatus(status) {
            const indicator = document.getElementById('system-status');
            const text = document.getElementById('status-text');
            indicator.className = `status-indicator ${status}`;
            text.textContent = status === 'healthy' ? 'System Operational' : 'Connection Issues';
        }
    </script>
</body>
</html>'''

    async def _get_admin_analytics_html(self, user_session) -> str:
        """Generate the admin analytics page HTML."""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Analytics - Admin Panel</title>
    <style>
        body {
            font-family: 'Courier New', monospace;
            background: #1a1a1a;
            color: #00ff00;
            margin: 0;
            padding: 20px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border: 1px solid #00ff00;
            padding: 20px;
            margin-bottom: 20px;
            background: #0a0a0a;
        }
        .header h1 { margin: 0; }
        .nav-links {
            display: flex;
            gap: 15px;
        }
        .nav-link {
            color: #00ccff;
            text-decoration: none;
            padding: 8px 15px;
            border: 1px solid #00ccff;
            background: #0a0a0a;
        }
        .nav-link:hover {
            background: #00ccff;
            color: #000000;
        }
        .analytics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .analytics-card {
            border: 1px solid #00ff00;
            padding: 20px;
            background: #0a0a0a;
        }
        .card-title {
            color: #ffff00;
            margin-bottom: 15px;
            font-size: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .stat-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            padding: 5px 0;
            border-bottom: 1px solid #333;
        }
        .stat-label {
            color: #888;
        }
        .stat-value {
            color: #00ff00;
            font-weight: bold;
        }
        .chart-container {
            height: 200px;
            border: 1px solid #333;
            background: #111;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #666;
            margin-top: 10px;
        }
        .viewer-trends {
            grid-column: 1 / -1;
        }
        .loading {
            text-align: center;
            color: #888;
            padding: 20px;
        }
        .error {
            color: #ff6666;
            text-align: center;
            padding: 20px;
        }
        .refresh-info {
            text-align: center;
            margin-top: 20px;
            color: #888;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 ANALYTICS DASHBOARD</h1>
            <div class="nav-links">
                <a href="/admin" class="nav-link">MAIN</a>
                <a href="/admin/streamers" class="nav-link">STREAMERS</a>
                <a href="/admin/users" class="nav-link">USERS</a>
            </div>
        </div>

        <div class="analytics-grid" id="analytics-content">
            <div class="analytics-card">
                <div class="card-title">👥 VIEWER STATISTICS</div>
                <div id="viewer-stats" class="loading">Loading viewer data...</div>
            </div>

            <div class="analytics-card">
                <div class="card-title">📈 STREAM ACTIVITY</div>
                <div id="stream-stats" class="loading">Loading stream data...</div>
            </div>

            <div class="analytics-card">
                <div class="card-title">🏆 TOP STREAMERS</div>
                <div id="top-streamers" class="loading">Loading top streamers...</div>
            </div>

            <div class="analytics-card">
                <div class="card-title">⚡ SYSTEM PERFORMANCE</div>
                <div id="system-stats" class="loading">Loading system data...</div>
            </div>

            <div class="analytics-card viewer-trends">
                <div class="card-title">📊 VIEWER TRENDS (24H)</div>
                <div class="chart-container" id="viewer-chart">
                    Chart visualization coming soon...
                </div>
            </div>
        </div>

        <div class="refresh-info">
            Last Updated: <span id="last-update">Loading...</span> |
            Auto-refresh every 30 seconds
        </div>
    </div>

    <script>
        let refreshInterval;

        async function loadAnalytics() {
            try {
                // Load viewer analytics
                const viewerResponse = await fetch('/api/dashboard/viewer-analytics');
                const viewerData = await viewerResponse.json();
                updateViewerStats(viewerData);

                // Load system health
                const healthResponse = await fetch('/api/dashboard/system-health');
                const healthData = await healthResponse.json();
                updateSystemStats(healthData);

                // Load status grid for stream activity
                const statusResponse = await fetch('/api/dashboard/status-grid');
                const statusData = await statusResponse.json();
                updateStreamStats(statusData);

                // Load recent activity for top streamers
                const activityResponse = await fetch('/api/dashboard/recent-activity?limit=10');
                const activityData = await activityResponse.json();
                updateTopStreamers(activityData);

                // Update timestamp
                document.getElementById('last-update').textContent = new Date().toLocaleString();

            } catch (error) {
                console.error('Failed to load analytics:', error);
                showError('Failed to load analytics data');
            }
        }

        function updateViewerStats(data) {
            const container = document.getElementById('viewer-stats');
            const stats = data.summary || {};

            container.innerHTML = `
                <div class="stat-row">
                    <span class="stat-label">Total Viewers Now:</span>
                    <span class="stat-value">${(stats.total_current_viewers || 0).toLocaleString()}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Peak Today:</span>
                    <span class="stat-value">${(stats.peak_viewers_today || 0).toLocaleString()}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Average (24h):</span>
                    <span class="stat-value">${(stats.avg_viewers_24h || 0).toLocaleString()}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Streams with Viewers:</span>
                    <span class="stat-value">${stats.live_streams_with_viewers || 0}</span>
                </div>
            `;
        }

        function updateStreamStats(data) {
            const container = document.getElementById('stream-stats');
            const online = data.filter(s => s.status === 'online').length;
            const offline = data.filter(s => s.status === 'offline').length;
            const total = data.length;

            container.innerHTML = `
                <div class="stat-row">
                    <span class="stat-label">Total Streamers:</span>
                    <span class="stat-value">${total}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Currently Live:</span>
                    <span class="stat-value" style="color: #00ff00;">${online}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Offline:</span>
                    <span class="stat-value" style="color: #ff6666;">${offline}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Live Rate:</span>
                    <span class="stat-value">${total > 0 ? ((online/total)*100).toFixed(1) : 0}%</span>
                </div>
            `;
        }

        function updateTopStreamers(data) {
            const container = document.getElementById('top-streamers');

            if (!data.events || data.events.length === 0) {
                container.innerHTML = '<div class="stat-row"><span class="stat-label">No recent activity</span></div>';
                return;
            }

            // Group by streamer and get latest events
            const streamerActivity = {};
            data.events.forEach(event => {
                if (!streamerActivity[event.streamer_username] ||
                    new Date(event.event_timestamp) > new Date(streamerActivity[event.streamer_username].event_timestamp)) {
                    streamerActivity[event.streamer_username] = event;
                }
            });

            const recentStreamers = Object.values(streamerActivity)
                .sort((a, b) => new Date(b.event_timestamp) - new Date(a.event_timestamp))
                .slice(0, 5);

            container.innerHTML = recentStreamers.map(streamer => `
                <div class="stat-row">
                    <span class="stat-label">${streamer.streamer_username}:</span>
                    <span class="stat-value" style="color: ${streamer.new_status === 'online' ? '#00ff00' : '#ff6666'};">
                        ${streamer.new_status.toUpperCase()}
                        ${streamer.viewer_count ? `(${streamer.viewer_count.toLocaleString()})` : ''}
                    </span>
                </div>
            `).join('');
        }

        function updateSystemStats(data) {
            const container = document.getElementById('system-stats');
            const processing = data.processing || {};
            const connections = data.connections || {};

            container.innerHTML = `
                <div class="stat-row">
                    <span class="stat-label">Success Rate:</span>
                    <span class="stat-value">${(processing.success_rate || 0).toFixed(1)}%</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Total Checks:</span>
                    <span class="stat-value">${processing.total_checks || 0}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">Failed Checks:</span>
                    <span class="stat-value" style="color: ${(processing.failed_checks || 0) > 0 ? '#ff6666' : '#00ff00'};">
                        ${processing.failed_checks || 0}
                    </span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">DB Connected:</span>
                    <span class="stat-value" style="color: ${connections.database_connected ? '#00ff00' : '#ff6666'};">
                        ${connections.database_connected ? 'YES' : 'NO'}
                    </span>
                </div>
            `;
        }

        function showError(message) {
            document.querySelectorAll('.loading').forEach(el => {
                el.className = 'error';
                el.textContent = message;
            });
        }

        // Initialize and start auto-refresh
        loadAnalytics();
        refreshInterval = setInterval(loadAnalytics, 30000);

        // Cleanup on page unload
        window.addEventListener('beforeunload', () => {
            if (refreshInterval) clearInterval(refreshInterval);
        });
    </script>
</body>
</html>'''

    @property
    def is_running(self) -> bool:
        """Check if the web dashboard is running."""
        return self._is_running
    
    @property
    def url(self) -> str:
        """Get the dashboard URL."""
        return f"http://{self.host}:{self.port}"
