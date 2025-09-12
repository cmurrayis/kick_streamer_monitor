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

from aiohttp import web, WSMsgType
from aiohttp.web import Request, Response, WebSocketResponse

logger = logging.getLogger(__name__)


class WebDashboardService:
    """
    Lightweight web dashboard for daemon mode monitoring.
    
    Provides HTTP endpoints and WebSocket for real-time updates.
    """
    
    def __init__(self, monitor_service, host: str = "0.0.0.0", port: int = 8080):
        self.monitor_service = monitor_service
        self.host = host
        self.port = port
        
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
            
            # Create aiohttp application
            self.app = web.Application()
            
            # Add routes
            self.app.router.add_get('/', self._handle_dashboard)
            self.app.router.add_get('/api/status', self._handle_api_status)
            self.app.router.add_get('/api/streamers', self._handle_api_streamers)
            self.app.router.add_get('/ws', self._handle_websocket)
            
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
        """Serve the main dashboard HTML page."""
        html_content = self._get_dashboard_html()
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
            text-align: center;
            border: 1px solid #00ff00;
            padding: 10px;
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
            <h1>🎮 KICK STREAMER MONITOR</h1>
            <p>Real-time Status Dashboard</p>
            <span id="connection-status">
                <span class="connection-indicator disconnected"></span>
                Connecting...
            </span>
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