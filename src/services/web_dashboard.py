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
from models.user import UserRole, UserSession

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
        self._websocket_connections = set()
    
    async def start(self) -> None:
        """Start the web dashboard server."""
        if self._is_running:
            logger.warning("Web dashboard already running")
            return
        
        try:
            logger.info(f"Starting web dashboard on {self.host}:{self.port}")
            
            # Connect auth manager to database service
            if hasattr(self.monitor_service, 'database_service'):
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
            
            # Admin routes (protected)
            self.app.router.add_get('/admin', self._handle_admin_dashboard)
            self.app.router.add_get('/admin/streamers', self._handle_admin_streamers)
            self.app.router.add_post('/admin/streamers/add', self._handle_add_streamer)
            self.app.router.add_post('/admin/streamers/remove', self._handle_remove_streamer)
            self.app.router.add_post('/admin/streamers/toggle', self._handle_toggle_streamer)
            self.app.router.add_get('/admin/users', self._handle_admin_users)
            self.app.router.add_post('/admin/users/add', self._handle_add_user)
            self.app.router.add_post('/admin/users/assign', self._handle_assign_streamer)
            self.app.router.add_post('/admin/users/unassign', self._handle_unassign_streamer)
            
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
                    # Show user dashboard
                    html_content = self._get_user_dashboard_html(user_session)
                    return Response(text=html_content, content_type='text/html')
        
        # No valid session, show splash page
        html_content = self._get_splash_page_html()
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
        html_content = self._get_login_html()
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
    
    async def _handle_admin_dashboard(self, request: Request) -> Response:
        """Serve the admin dashboard."""
        user_session = self._require_admin(request)
        if not user_session:
            # Redirect to login
            response = Response(status=302)
            response.headers['Location'] = '/login'
            return response
        
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
        user_info = await self._require_admin(request)
        if not user_info:
            return Response(status=401, text="Unauthorized")
        
        try:
            data = await request.post()
            streamer_id = data.get('streamer_id', '').strip()
            
            if not streamer_id:
                return Response(status=400, text="Streamer ID required")
            
            # Remove streamer via database service
            logger.info(f"Admin {user_info['username']} removing streamer ID: {streamer_id}")
            
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?success=removed'
            return response
            
        except Exception as e:
            logger.error(f"Remove streamer error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?error=remove_failed'
            return response
    
    async def _handle_toggle_streamer(self, request: Request) -> Response:
        """Handle toggling streamer monitoring status."""
        user_info = await self._require_admin(request)
        if not user_info:
            return Response(status=401, text="Unauthorized")
        
        try:
            data = await request.post()
            streamer_id = data.get('streamer_id', '').strip()
            
            if not streamer_id:
                return Response(status=400, text="Streamer ID required")
            
            # Toggle streamer status via database service
            logger.info(f"Admin {user_info['username']} toggling streamer ID: {streamer_id}")
            
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?success=toggled'
            return response
            
        except Exception as e:
            logger.error(f"Toggle streamer error: {e}")
            response = Response(status=302)
            response.headers['Location'] = '/admin/streamers?error=toggle_failed'
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
    
    
    def _get_login_html(self, error_message: str = "") -> str:
        """Generate the login page HTML."""
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
            
            # Create assignment
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
            success = await self.database_service.remove_user_streamer_assignment(
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

    def _get_admin_dashboard_html(self, user_info: Dict[str, Any]) -> str:
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
                <div class="admin-info">Logged in as: {user_info['username']}</div>
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
            <div class="menu-card" style="opacity: 0.5;">
                <div class="menu-title">📈 ANALYTICS</div>
                <div class="menu-desc">Coming soon...</div>
            </div>
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
    
    def _get_admin_streamers_html(self, user_info: Dict[str, Any]) -> str:
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
        const urlParams = new URLSearchParams(window.location.search);
        const success = urlParams.get('success');
        const error = urlParams.get('error');
        const messageContainer = document.getElementById('message-container');

        if (success) {
            const messages = {
                'added': 'Streamer added successfully!',
                'removed': 'Streamer removed successfully!',
                'toggled': 'Streamer status toggled successfully!'
            };
            messageContainer.innerHTML = `<div class="success-message">${messages[success] || 'Operation successful!'}</div>`;
        } else if (error) {
            const messages = {
                'add_failed': 'Failed to add streamer. Please try again.',
                'remove_failed': 'Failed to remove streamer. Please try again.',
                'toggle_failed': 'Failed to toggle streamer status. Please try again.'
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
                                <form method="post" action="/admin/streamers/toggle" style="display: inline;">
                                    <input type="hidden" name="streamer_id" value="${streamer.id}">
                                    <button type="submit" class="action-btn">TOGGLE</button>
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
        for user in users:
            user_rows += f'''
                <tr>
                    <td>{user.id}</td>
                    <td>{user.username}</td>
                    <td>{user.email}</td>
                    <td>{user.display_name or "-"}</td>
                    <td class="role-{user.role}">{user.role.upper()}</td>
                    <td class="status-{user.status}">{user.status.upper()}</td>
                    <td>TODO: Load assignments</td>
                    <td>{user.created_at.strftime("%Y-%m-%d %H:%M") if user.created_at else "-"}</td>
                </tr>
            '''
        
        # Generate user options for assignment dropdown
        user_options = ""
        for user in users:
            if user.role != 'admin':
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
        .assignment-form {{
            display: flex;
            gap: 15px;
            align-items: end;
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
            <h3>🔗 ASSIGN STREAMER TO USER</h3>
            <form method="post" action="/admin/users/assign" class="assignment-form">
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
            </form>
        </div>
    </div>

    <script>
        // Check for messages in URL params
        const urlParams = new URLSearchParams(window.location.search);
        const success = urlParams.get('success');
        const error = urlParams.get('error');
        const messageContainer = document.getElementById('message-container');

        if (success) {{
            const messages = {{
                'added': 'User created successfully!',
                'assigned': 'Streamer assigned successfully!',
                'unassigned': 'Streamer unassigned successfully!'
            }};
            messageContainer.innerHTML = `<div class="success-message">${{messages[success] || 'Operation successful!'}}</div>`;
        }} else if (error) {{
            const messages = {{
                'add_failed': 'Failed to create user. Please try again.',
                'assign_failed': 'Failed to assign streamer. Please try again.',
                'unassign_failed': 'Failed to unassign streamer. Please try again.',
                'missing_fields': 'Please fill in all required fields.'
            }};
            messageContainer.innerHTML = `<div class="error-message">${{messages[error] || 'Operation failed!'}}</div>`;
        }}
    </script>
</body>
</html>'''

    def _get_dashboard_html(self) -> str:
        """Generate the dashboard HTML page."""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kick Streamer Monitor Dashboard</title>
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
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .stat-card {
            border: 1px solid #00ff00;
            padding: 15px;
            background: #0a0a0a;
        }
        .stat-title { color: #ffff00; font-weight: bold; margin-bottom: 10px; }
        .stat-value { font-size: 1.2em; }
        .streamers-table {
            width: 100%;
            border-collapse: collapse;
            border: 1px solid #00ff00;
            margin-top: 20px;
        }
        .streamers-table th, .streamers-table td {
            border: 1px solid #00ff00;
            padding: 8px;
            text-align: left;
        }
        .streamers-table th { background: #0a0a0a; color: #ffff00; }
        .status-online { color: #00ff00; }
        .status-offline { color: #ff6600; }
        .status-unknown { color: #666666; }
        .last-update { text-align: center; margin-top: 20px; color: #666; }
        .connection-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 5px;
        }
        .connected { background: #00ff00; }
        .disconnected { background: #ff0000; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>🎮 KICK STREAMER MONITOR</h1>
                <p>Real-time Status Dashboard</p>
            </div>
            <div style="text-align: right;">
                <span id="connection-status">
                    <span class="connection-indicator disconnected"></span>
                    Connecting...
                </span>
                <br><br>
                <a href="/login" style="color: #00ff00; text-decoration: none; font-size: 12px;">🔐 Admin Login</a>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-title">SERVICE STATUS</div>
                <div class="stat-value" id="service-status">Loading...</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">TOTAL STREAMERS</div>
                <div class="stat-value" id="total-streamers">-</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">ONLINE</div>
                <div class="stat-value status-online" id="online-count">-</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">OFFLINE</div>
                <div class="stat-value status-offline" id="offline-count">-</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">SUCCESS RATE</div>
                <div class="stat-value" id="success-rate">-</div>
            </div>
            <div class="stat-card">
                <div class="stat-title">UPTIME</div>
                <div class="stat-value" id="uptime">-</div>
            </div>
        </div>

        <table class="streamers-table">
            <thead>
                <tr>
                    <th>Username</th>
                    <th>Status</th>
                    <th>Last Seen Online</th>
                    <th>Last Update</th>
                </tr>
            </thead>
            <tbody id="streamers-tbody">
                <tr><td colspan="4" style="text-align: center;">Loading streamers...</td></tr>
            </tbody>
        </table>

        <div class="last-update">
            Last Updated: <span id="last-update">Never</span>
        </div>
    </div>

    <script>
        let ws = null;
        let reconnectInterval = null;

        function connectWebSocket() {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
            
            ws.onopen = function() {
                document.getElementById('connection-status').innerHTML = 
                    '<span class="connection-indicator connected"></span>Connected';
                clearInterval(reconnectInterval);
            };
            
            ws.onmessage = function(event) {
                const data = JSON.parse(event.data);
                if (data.type === 'update') {
                    updateDashboard(data.stats, data.streamers);
                }
            };
            
            ws.onclose = function() {
                document.getElementById('connection-status').innerHTML = 
                    '<span class="connection-indicator disconnected"></span>Disconnected';
                
                // Reconnect every 5 seconds
                if (!reconnectInterval) {
                    reconnectInterval = setInterval(connectWebSocket, 5000);
                }
            };
        }

        function updateDashboard(stats, streamers) {
            // Update service status
            const serviceStatus = stats.service_status || {};
            document.getElementById('service-status').textContent = 
                serviceStatus.is_running ? 'RUNNING' : 'STOPPED';
            
            // Update streamer counts
            const streamerStats = stats.streamers || {};
            document.getElementById('total-streamers').textContent = streamerStats.total_monitored || 0;
            document.getElementById('online-count').textContent = streamerStats.online || 0;
            document.getElementById('offline-count').textContent = streamerStats.offline || 0;
            
            // Update processing stats
            const processing = stats.processing || {};
            const successRate = processing.success_rate || 0;
            document.getElementById('success-rate').textContent = `${successRate.toFixed(1)}%`;
            
            // Update uptime
            const uptime = serviceStatus.uptime_seconds || 0;
            document.getElementById('uptime').textContent = formatUptime(uptime);
            
            // Update streamers table
            updateStreamersTable(streamers);
            
            // Update timestamp
            document.getElementById('last-update').textContent = new Date().toLocaleString();
        }

        function updateStreamersTable(streamers) {
            const tbody = document.getElementById('streamers-tbody');
            
            if (!streamers || streamers.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center;">No streamers found</td></tr>';
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
                        <td>${streamer.username}</td>
                        <td class="${statusClass}">${streamer.status.toUpperCase()}</td>
                        <td>${lastSeen}</td>
                        <td>${lastUpdate}</td>
                    </tr>
                `;
            }).join('');
        }

        function formatUptime(seconds) {
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const secs = Math.floor(seconds % 60);
            return `${hours}h ${minutes}m ${secs}s`;
        }

        // Initialize
        connectWebSocket();
        
        // Fallback: fetch data every 30 seconds if WebSocket fails
        setInterval(async () => {
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                try {
                    const [statsRes, streamersRes] = await Promise.all([
                        fetch('/api/status'),
                        fetch('/api/streamers')
                    ]);
                    
                    const stats = await statsRes.json();
                    const streamers = await streamersRes.json();
                    
                    updateDashboard(stats, streamers);
                } catch (error) {
                    console.error('Failed to fetch data:', error);
                }
            }
        }, 30000);
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