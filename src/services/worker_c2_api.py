"""
Worker C2 (Command and Control) API service.

Provides API endpoints for remote worker nodes to check in and obtain
their job instructions from the snags database worker table.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
import hashlib
import secrets

from aiohttp import web
from aiohttp.web import Request, Response
import jwt

logger = logging.getLogger(__name__)


class WorkerC2API:
    """
    C2 API endpoints for worker nodes.

    Provides authentication and job instruction delivery for remote workers
    based on the snags database worker table configuration.
    """

    def __init__(self, database_service, jwt_secret: Optional[str] = None):
        self.database_service = database_service
        # Use provided secret or generate a new one
        self.jwt_secret = jwt_secret or secrets.token_hex(32)
        self.worker_sessions = {}  # Track active worker sessions

    def setup_routes(self, app: web.Application):
        """Setup C2 API routes on the provided aiohttp application."""
        # Worker C2 endpoints
        app.router.add_post('/api/login', self.handle_worker_login)
        app.router.add_get('/api/instructions/{hostname}', self.handle_get_instructions)
        app.router.add_post('/api/state', self.handle_update_state)
        app.router.add_post('/api/release-resources/{hostname}', self.handle_release_resources)

    async def handle_worker_login(self, request: Request) -> Response:
        """
        Handle worker node login and JWT token generation.

        Expected payload: {"hostname": "worker-node-01"}
        Returns: {"token": "jwt_token_here"}
        """
        try:
            data = await request.json()
            hostname = data.get('hostname')

            if not hostname:
                return web.json_response({'error': 'hostname required'}, status=400)

            # Check if hostname exists in worker table
            async with self.database_service.snags_service.get_connection() as conn:
                query = "SELECT hostname FROM worker WHERE hostname = $1"
                result = await conn.fetchval(query, hostname)

                if not result:
                    logger.warning(f"Unknown hostname attempted login: {hostname}")
                    return web.json_response({'error': 'Invalid hostname'}, status=403)

            # Generate JWT token
            payload = {
                'hostname': hostname,
                'exp': datetime.now(timezone.utc) + timedelta(hours=24),
                'iat': datetime.now(timezone.utc)
            }

            token = jwt.encode(payload, self.jwt_secret, algorithm='HS256')

            # Track session
            self.worker_sessions[hostname] = {
                'last_seen': datetime.now(timezone.utc),
                'token': token,
                'active_workers': 0
            }

            logger.info(f"Worker node logged in: {hostname}")
            return web.json_response({'token': token})

        except Exception as e:
            logger.error(f"Error in worker login: {e}")
            return web.json_response({'error': 'Internal server error'}, status=500)

    async def _verify_token(self, request: Request) -> Optional[str]:
        """
        Verify JWT token from request header.
        Returns hostname if valid, None otherwise.
        """
        auth_header = request.headers.get('access-token', '')
        if not auth_header.startswith('Bearer '):
            return None

        token = auth_header.replace('Bearer ', '')

        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=['HS256'])
            return payload.get('hostname')
        except jwt.ExpiredSignatureError:
            logger.debug("Expired JWT token")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug(f"Invalid JWT token: {e}")
            return None

    async def handle_get_instructions(self, request: Request) -> Response:
        """
        Get job instructions for a specific worker node.

        Returns:
        - status: "online" or "offline"
        - count: number of workers to spawn
        - target: streamer username to monitor
        - channel_id: Kick channel ID (new)
        - livestream_id: Kick livestream ID if live (new)
        """
        try:
            hostname = request.match_info['hostname']

            # Verify authentication
            auth_hostname = await self._verify_token(request)
            if not auth_hostname or auth_hostname != hostname:
                return web.json_response({'error': 'Unauthorized'}, status=401)

            # Get worker assignment from snags database
            async with self.database_service.snags_service.get_connection() as snags_conn:
                worker_query = """
                SELECT target, count, str_status, wrk_status
                FROM worker
                WHERE hostname = $1
                """
                worker_record = await snags_conn.fetchrow(worker_query, hostname)

                if not worker_record:
                    logger.warning(f"No assignment found for hostname: {hostname}")
                    return web.json_response({
                        'status': 'offline',
                        'count': 0
                    })

                target = worker_record['target']
                count = worker_record['count'] or 0

            # If no target assigned, return offline
            if not target:
                return web.json_response({
                    'status': 'offline',
                    'count': 0
                })

            # Get streamer information from main database
            async with self.database_service.get_connection() as conn:
                streamer_query = """
                SELECT username, status, channel_id, livestream_id, is_live
                FROM streamer
                WHERE username = $1
                """
                streamer_record = await conn.fetchrow(streamer_query, target)

                if not streamer_record:
                    logger.warning(f"Target streamer not found: {target}")
                    return web.json_response({
                        'status': 'offline',
                        'count': 0,
                        'target': target
                    })

                # Determine final status (str_status from worker table can override)
                # str_status in worker table allows manual override of worker behavior
                worker_str_status = worker_record.get('str_status', '').lower()
                streamer_is_online = streamer_record['status'] == 'online'

                # If str_status is set in worker table, use it; otherwise use streamer status
                if worker_str_status in ['online', 'offline']:
                    final_status = worker_str_status
                else:
                    final_status = 'online' if streamer_is_online else 'offline'

                # Build response
                response_data = {
                    'status': final_status,
                    'count': count if final_status == 'online' else 0,
                    'target': target,
                    'channel_id': streamer_record['channel_id'],
                }

                # Only include livestream_id if stream is actually live
                if streamer_record['is_live'] and streamer_record['livestream_id']:
                    response_data['livestream_id'] = streamer_record['livestream_id']

                logger.debug(f"Instructions for {hostname}: {response_data}")
                return web.json_response(response_data)

        except Exception as e:
            logger.error(f"Error getting instructions for {hostname}: {e}")
            return web.json_response({'error': 'Internal server error'}, status=500)

    async def handle_update_state(self, request: Request) -> Response:
        """
        Update worker node state/statistics.

        Expected payload: {
            "client_hostname": "worker-node-01",
            "status": "Online" or "Offline",
            "workers": 10  // active worker count
        }
        """
        try:
            # Verify authentication
            auth_hostname = await self._verify_token(request)
            if not auth_hostname:
                return web.json_response({'error': 'Unauthorized'}, status=401)

            data = await request.json()
            hostname = data.get('client_hostname')
            status = data.get('status')
            workers = data.get('workers', 0)

            if hostname != auth_hostname:
                return web.json_response({'error': 'Hostname mismatch'}, status=403)

            # Update session tracking
            if hostname in self.worker_sessions:
                self.worker_sessions[hostname].update({
                    'last_seen': datetime.now(timezone.utc),
                    'status': status,
                    'active_workers': workers
                })

            # Update worker statistics in database using existing columns
            async with self.database_service.snags_service.get_connection() as conn:
                update_query = """
                UPDATE worker
                SET wrk_count = $1,
                    wrk_status = $2,
                    updated_at = $3
                WHERE hostname = $4
                """
                await conn.execute(
                    update_query,
                    workers,
                    status.lower(),  # Convert "Online" to "online" for wrk_status
                    datetime.now(timezone.utc),
                    hostname
                )

            logger.debug(f"State update from {hostname}: {status} with {workers} workers")
            return web.json_response({'success': True})

        except Exception as e:
            logger.error(f"Error updating worker state: {e}")
            return web.json_response({'error': 'Internal server error'}, status=500)

    async def handle_release_resources(self, request: Request) -> Response:
        """
        Handle resource release/cleanup for a worker node.

        This is called when a worker starts up to clear any previous allocations.
        """
        try:
            hostname = request.match_info['hostname']

            # Verify authentication
            auth_hostname = await self._verify_token(request)
            if not auth_hostname or auth_hostname != hostname:
                return web.json_response({'error': 'Unauthorized'}, status=401)

            # Clear any tracked state for this worker
            if hostname in self.worker_sessions:
                self.worker_sessions[hostname]['active_workers'] = 0

            # Clear worker count in database
            async with self.database_service.snags_service.get_connection() as conn:
                update_query = """
                UPDATE worker
                SET wrk_count = 0,
                    wrk_status = 'offline',
                    updated_at = $1
                WHERE hostname = $2
                """
                await conn.execute(update_query, datetime.now(timezone.utc), hostname)

            logger.info(f"Released resources for worker: {hostname}")
            return web.json_response({'success': True})

        except Exception as e:
            logger.error(f"Error releasing resources for {hostname}: {e}")
            return web.json_response({'error': 'Internal server error'}, status=500)

    def get_worker_status(self) -> Dict[str, Any]:
        """Get current status of all worker nodes."""
        active_workers = []
        total_active = 0

        for hostname, session in self.worker_sessions.items():
            last_seen = session.get('last_seen')
            is_active = False

            if last_seen:
                time_diff = datetime.now(timezone.utc) - last_seen
                is_active = time_diff.total_seconds() < 300  # Consider active if seen in last 5 minutes

            worker_count = session.get('active_workers', 0)
            if is_active:
                total_active += worker_count

            active_workers.append({
                'hostname': hostname,
                'status': session.get('status', 'Unknown'),
                'active_workers': worker_count,
                'last_seen': last_seen.isoformat() if last_seen else None,
                'is_active': is_active
            })

        return {
            'total_nodes': len(active_workers),
            'total_active_workers': total_active,
            'nodes': active_workers
        }