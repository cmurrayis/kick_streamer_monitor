#!/usr/bin/env python3

import asyncio
import aiohttp
import cloudscraper
import json
import logging
import random
import re
import requests
import socket
import sys
import time
try:
    import resource
except ImportError:
    resource = None  # Windows doesn't have resource module
import psutil
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import urljoin
from jose import jwt
from typing import Dict, Optional, Set, List
import websockets
from websockets.exceptions import ConnectionClosed

# Increase file descriptor limit (Unix/Linux only)
if resource:
    try:
        soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
        new_limit = min(soft_limit * 4, hard_limit)
        resource.setrlimit(resource.RLIMIT_NOFILE, (new_limit, hard_limit))
    except (ValueError, OSError):
        pass

# Parse arguments
parser = argparse.ArgumentParser(description='IPv6 Worker Script with Kick.com WebSocket Support')
parser.add_argument('--debug', action='store_true', help='Enable debug mode')
parser.add_argument('--interface', default='enp1s0', help='Network interface with IPv6')
parser.add_argument('--c2-url', default='http://108.61.86.99:8080/api/',
                    help='C2 server API URL')
args = parser.parse_args()

log_level = logging.DEBUG if args.debug else logging.INFO
logging.basicConfig(level=log_level, stream=sys.stderr,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', force=True)
logger = logging.getLogger(__name__)


class C2_API:
    """C2 Server API for remote command and control"""

    def __init__(self, base_url, hostname):
        self.base_url = base_url
        self.hostname = hostname
        self.auth_token = None
        self.jwt_secret = "788ab52bc6296824141020dc38099d18f4367b73573e3cb2be7aab7ad466d63f"
        self.session = aiohttp.ClientSession(headers={'User-Agent': 'C2-Client/1.0'})

    async def close(self):
        await self.session.close()

    async def _login(self):
        if self.auth_token:
            try:
                jwt.decode(self.auth_token.replace('Bearer ', ''), self.jwt_secret, algorithms=['HS256'])
                return True
            except (jwt.ExpiredSignatureError, jwt.JWTError):
                pass
        try:
            async with self.session.post(urljoin(self.base_url, "login"),
                                        json={"hostname": self.hostname}, timeout=10) as response:
                response.raise_for_status()
                data = await response.json()
                self.auth_token = f"Bearer {data['token']}"
                self.session.headers['access-token'] = self.auth_token
                return True
        except Exception as e:
            self.auth_token = None
            logger.error(f"C2_API: Failed to obtain auth token: {e}")
            return False

    async def _api_request(self, method, endpoint, **kwargs):
        if not await self._login():
            return None
        try:
            async with self.session.request(method, urljoin(self.base_url, endpoint),
                                           timeout=15, **kwargs) as response:
                response.raise_for_status()
                return await response.json() if response.content_length != 0 else {}
        except Exception as e:
            logger.error(f"C2_API: Request to {endpoint} failed: {e}")
            return None

    async def fetch_instructions(self):
        max_attempts = 3
        for attempt in range(max_attempts):
            instructions = await self._api_request('GET', f"instructions/{self.hostname}")
            if instructions is not None:
                return instructions
            logger.warning(f"Attempt {attempt + 1}/{max_attempts} to fetch instructions failed.")
            if attempt + 1 < max_attempts:
                await asyncio.sleep(5)
        return None

    async def update_status(self, status, workers):
        payload = {"client_hostname": self.hostname, "status": status, "workers": workers}
        await self._api_request('POST', "state", json=payload)

    async def clear_checkouts(self):
        logger.info("Attempting to clear any previous resource checkouts.")
        await self._api_request('POST', f"release-resources/{self.hostname}")


class IPv6Pool:
    """Manages IPv6 address allocation with exclusive assignment"""

    def __init__(self, interface: str = "enp1s0"):
        self.interface = interface
        self.available_ips = asyncio.Queue()
        self.in_use_ips = set()
        self.lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self):
        """Initialize the IPv6 pool with available addresses"""
        if self._initialized:
            return

        try:
            import subprocess
            result = subprocess.run(
                ["ip", "-6", "addr", "show", "dev", self.interface],
                capture_output=True, text=True, check=True
            )

            # Parse IPv6 addresses
            for line in result.stdout.split('\n'):
                if 'inet6' in line and 'scope global' in line:
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        addr = parts[1].split('/')[0]
                        if not addr.startswith('fe80'):
                            await self.available_ips.put(addr)
                            logger.debug(f"Added IPv6 address to pool: {addr}")

            # Count available addresses
            base_count = self.available_ips.qsize()
            if base_count > 0:
                logger.info(f"Found {base_count} IPv6 addresses configured on {self.interface}")
            else:
                logger.error(f"No IPv6 addresses found on interface {self.interface}")
                raise ValueError(f"No IPv6 addresses available on {self.interface}")

            self._initialized = True
            total_addresses = self.available_ips.qsize()
            logger.info(f"IPv6 pool initialized with {total_addresses} addresses from interface {self.interface}")

        except Exception as e:
            logger.error(f"Failed to initialize IPv6 pool: {e}")
            raise

    async def acquire(self) -> Optional[str]:
        """Acquire an exclusive IPv6 address"""
        if not self._initialized:
            await self.initialize()

        try:
            async with self.lock:
                ip = await asyncio.wait_for(self.available_ips.get(), timeout=5.0)
                self.in_use_ips.add(ip)
                logger.debug(f"Acquired IPv6: {ip}")
                return ip
        except asyncio.TimeoutError:
            logger.error("Timeout acquiring IPv6 address from pool")
            return None

    async def release(self, ip: str):
        """Release an IPv6 address back to the pool"""
        async with self.lock:
            if ip in self.in_use_ips:
                self.in_use_ips.remove(ip)
                await self.available_ips.put(ip)
                logger.debug(f"Released IPv6: {ip}")

    def stats(self) -> Dict:
        """Get pool statistics"""
        return {
            'available': self.available_ips.qsize(),
            'in_use': len(self.in_use_ips),
            'total': self.available_ips.qsize() + len(self.in_use_ips)
        }


class IPv6Bot:
    """Main bot managing viewer workers with C2 server coordination"""

    def __init__(self, c2_api, interface: str = "enp1s0", debug: bool = False):
        self.c2_api = c2_api
        self.interface = interface
        self.debug = debug
        self.ipv6_pool = IPv6Pool(interface=interface)
        self.worker_manager_tasks = set()
        self.total_failures = 0
        self.active_sessions = 0
        self.open_sockets = 0
        self.required_workers = 0
        self.is_online = False
        self.streamer_name = None
        self.channel_id = None
        self.livestream_id = None
        self.client_token = 'e1393935a959b4020a4491574f6490129f678acdaa92760471263db43487f823'
        # Add rate limiter for WebSocket connections
        self.websocket_semaphore = asyncio.Semaphore(5)  # Max 5 concurrent WebSocket connection attempts
        self.last_websocket_time = 0

    async def run(self):
        """Main bot loop that fetches instructions from C2 server"""
        await self.c2_api.clear_checkouts()

        # Initialize IPv6 pool
        await self.ipv6_pool.initialize()
        pool_stats = self.ipv6_pool.stats()
        logger.info(f"IPv6 Pool initialized: {pool_stats}")

        summary_task = asyncio.create_task(self._log_summary_periodically())

        try:
            while True:
                instructions = await self.c2_api.fetch_instructions()
                if instructions:
                    await self._process_instructions(instructions)
                else:
                    logger.warning("Failed to get instructions. Assuming OFFLINE.")
                    await self._process_instructions({'status': 'offline', 'count': 0})
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            logger.info("Main loop cancelled.")
        finally:
            logger.info("Shutting down all tasks...")
            summary_task.cancel()
            await self._shutdown()

    async def _log_summary_periodically(self):
        """Display status summary periodically"""
        while True:
            try:
                cpu_usage = psutil.cpu_percent()
                mem_usage = psutil.virtual_memory().percent
                status = "ONLINE" if self.is_online else "OFFLINE"
                target = self.streamer_name or "N/A"
                pool_stats = self.ipv6_pool.stats()

                summary = (
                    f"Status: {status} | "
                    f"Target: {target} | "
                    f"Workers: {self.active_sessions}/{self.required_workers} | "
                    f"Open Sockets: {self.open_sockets}/{self.required_workers} | "
                    f"IPv6 Pool: {pool_stats['available']}/{pool_stats['total']} | "
                    f"Failures: {self.total_failures} | "
                    f"CPU: {cpu_usage:.1f}% | "
                    f"Memory: {mem_usage:.1f}%   "
                )
                print(summary, end='\r', file=sys.stdout, flush=True)
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break

    async def _process_instructions(self, instructions):
        """Process instructions from C2 server"""
        status = instructions.get('status')

        if status == 'online':
            was_offline = not self.is_online
            self.is_online = True
            self.required_workers = instructions.get('count', 0)
            self.streamer_name = instructions.get('target')
            self.channel_id = instructions.get('channel_id')  # Get from C2 API
            self.livestream_id = instructions.get('livestream_id')  # Get from C2 API if live

            if was_offline:
                logger.info(f"Transitioning to ONLINE for target: {self.streamer_name} (channel_id: {self.channel_id})")

            if not self.streamer_name:
                logger.error("Stream is ONLINE but 'target' is missing.")
                self.is_online = False
                self.required_workers = 0
            elif not self.channel_id:
                logger.error(f"Stream is ONLINE but 'channel_id' is missing for {self.streamer_name}")
        else:
            if self.is_online:
                logger.info("Transitioning to OFFLINE.")
            self.is_online = False
            self.required_workers = 0
            self.channel_id = None
            self.livestream_id = None

        await self._adjust_worker_managers()
        await self.c2_api.update_status("Online" if self.is_online else "Offline", self.active_sessions)

    async def _adjust_worker_managers(self):
        """Adjust number of worker managers based on requirements"""
        self.worker_manager_tasks = {t for t in self.worker_manager_tasks if not t.done()}

        while len(self.worker_manager_tasks) > self.required_workers:
            self.worker_manager_tasks.pop().cancel()

        # Stagger worker starts to avoid thundering herd and rate limiting
        workers_to_add = self.required_workers - len(self.worker_manager_tasks)
        if workers_to_add > 0:
            logger.info(f"Starting {workers_to_add} workers with staggered delays")

            # Calculate appropriate delay based on number of workers
            # For 100 workers, spread over ~60 seconds
            if workers_to_add <= 10:
                delay_per_worker = 0.2
            elif workers_to_add <= 50:
                delay_per_worker = 0.5
            else:
                delay_per_worker = 0.8

            async def start_workers_staggered():
                for i in range(workers_to_add):
                    self.worker_manager_tasks.add(asyncio.create_task(self._worker_manager()))
                    if i < workers_to_add - 1:  # Don't delay after last worker
                        await asyncio.sleep(delay_per_worker + random.uniform(0, 0.3))

            # Start the staggered worker creation as a background task
            asyncio.create_task(start_workers_staggered())

    async def _worker_manager(self):
        """Manages a single viewer session"""
        manager_id = id(asyncio.current_task()) % 1000
        logger_mgr = logging.getLogger(f"Manager-{manager_id:03d}")

        try:
            while True:
                if not self.is_online:
                    logger_mgr.debug("Idle (master status is OFFLINE).")
                    await asyncio.sleep(15)
                    continue

                # Rate limit WebSocket connections to avoid overwhelming the network
                # If we have too many open sockets, wait before creating new ones
                while self.open_sockets >= self.required_workers * 0.9:  # Keep 10% buffer
                    logger_mgr.debug(f"Connection throttling: {self.open_sockets}/{self.required_workers} sockets open")
                    await asyncio.sleep(2)

                # Acquire IPv6 address from pool
                logger_mgr.debug("Waiting to acquire IPv6 from pool...")
                ipv6_address = await self.ipv6_pool.acquire()
                if not ipv6_address:
                    logger_mgr.error("Failed to acquire IPv6 address - pool may be exhausted")
                    logger_mgr.info(f"Pool stats: {self.ipv6_pool.stats()}")
                    await asyncio.sleep(30)  # Wait longer if pool is exhausted
                    continue

                self.active_sessions += 1

                try:
                    logger_mgr.info(f"Thread {manager_id} acquired unique IPv6: {ipv6_address} for '{self.streamer_name}'")
                    # No additional delay here since we stagger in _adjust_worker_managers
                    await self._execute_viewer_simulation(ipv6_address, logger_mgr)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.total_failures += 1
                    logger_mgr.error(f"Session failed: {e}", exc_info=self.debug)
                finally:
                    self.active_sessions -= 1
                    await self.ipv6_pool.release(ipv6_address)

        except asyncio.CancelledError:
            logger_mgr.debug("Manager cancelled and shutting down.")
        except Exception as e:
            logger_mgr.error(f"Manager fatal error: {e}", exc_info=self.debug)

    async def _execute_viewer_simulation(self, ipv6_address: str, logger_mgr):
        """Execute a single viewer simulation with IPv6 binding"""
        # Use more realistic browser settings for CloudScraper
        scraper = cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'windows',
                'desktop': True
            }
        )
        max_session_attempts = 5

        # Create persistent socket binding for entire session
        import functools
        original_socket = socket.socket

        def bound_socket(*args, **kwargs):
            sock = original_socket(*args, **kwargs)
            try:
                if sock.family == socket.AF_INET6 and sock.type == socket.SOCK_STREAM:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    sock.bind((ipv6_address, 0))
                    # Only log first binding to reduce log spam
                    if not hasattr(bound_socket, 'logged'):
                        logger_mgr.info(f"Thread bound to IPv6: {ipv6_address}")
                        bound_socket.logged = True
            except OSError as e:
                if e.errno != 22:  # Ignore "already bound" errors
                    logger_mgr.debug(f"Could not bind IPv6: {e}")
            except Exception:
                pass
            return sock

        # Apply socket binding for entire session
        socket.socket = bound_socket

        try:
            for attempt in range(max_session_attempts):
                try:
                    # Use channel_id and livestream_id from C2 instructions
                    if not self.channel_id:
                        raise ValueError(f"No channel_id available for {self.streamer_name}")

                    logger_mgr.debug(f"Session attempt {attempt + 1}/{max_session_attempts}: Establishing session for channel_id {self.channel_id}")

                    # IMPORTANT: Still make the channel API call to establish session
                    # Kick tracks the full flow from same IP: channel API -> token -> websocket
                    await self._establish_session(scraper, logger_mgr)

                    # Now get WebSocket token with established session
                    ws_token = await self._get_websocket_token(scraper, logger_mgr)
                    if not ws_token:
                        raise ValueError("Failed to get WebSocket token")

                    # Handle WebSocket connection with provided IDs
                    await self._handle_websocket_heartbeat(
                        self.channel_id,
                        self.livestream_id,
                        ws_token,
                        ipv6_address,
                        logger_mgr
                    )

                    # WebSocket ended, retry if attempts remain
                    logger_mgr.warning("WebSocket handling ended. Will retry if attempts remain.")
                    raise ConnectionClosed(None, "WebSocket handling completed")

                except asyncio.CancelledError:
                    logger_mgr.info("Viewer simulation cancelled.")
                    raise

                except Exception as e:
                    # Clean up the error message
                    error_msg = str(e)
                    if "Failed to get WebSocket token" in error_msg:
                        logger_mgr.warning(f"Session error (attempt {attempt + 1}/{max_session_attempts}): WebSocket token acquisition failed")
                    else:
                        logger_mgr.warning(f"Session error (attempt {attempt + 1}/{max_session_attempts}): {error_msg[:100]}",
                                         exc_info=self.debug)

                    if attempt + 1 >= max_session_attempts:
                        logger_mgr.error("Max session retry attempts reached.")
                        raise

                    await asyncio.sleep(random.uniform(5, 10))
        finally:
            # Restore original socket
            socket.socket = original_socket
            try:
                scraper.close()
            except:
                pass

    async def _establish_session(self, scraper, logger_mgr):
        """
        Make channel API call to establish session with Kick.
        This is required for viewer count to register properly.
        We use the IDs from C2 but still make the API call for session tracking.
        Socket binding is already handled in _execute_viewer_simulation.
        """
        if not self.streamer_name:
            return False

        try:
            # Make channel API call to establish session
            api_url = f"https://kick.com/api/v2/channels/{self.streamer_name}"
            logger_mgr.debug(f"Establishing session via channel API: {api_url}")

            channel_response = await asyncio.to_thread(scraper.get, api_url, timeout=20)

            if channel_response.status_code == 403:
                logger_mgr.error(f"Got 403 Forbidden - Cloudflare block detected")
                raise ValueError("403 Forbidden - Cloudflare protection active")

            channel_response.raise_for_status()

            # We don't need to parse the response since we have IDs from C2
            # This call is just to establish the session
            logger_mgr.debug(f"Session established for {self.streamer_name} (status: {channel_response.status_code})")
            return True

        except (ConnectionError, requests.exceptions.ConnectionError) as e:
            # Handle connection errors more gracefully
            if "RemoteDisconnected" in str(e):
                logger_mgr.warning(f"Channel API connection dropped by server (common during high load)")
            else:
                logger_mgr.warning(f"Channel API connection error: {str(e)[:100]}")
            return False
        except Exception as e:
            logger_mgr.error(f"Failed to establish session: {e}", exc_info=self.debug)
            return False

    async def _get_websocket_token(self, scraper, logger_mgr):
        """Get WebSocket token only (channel_id and livestream_id come from C2 API)"""
        if not self.streamer_name:
            return None

        try:
            # Socket binding is already handled in _execute_viewer_simulation
            # Get WebSocket token
            token_url = "https://websockets.kick.com/viewer/v1/token"
            headers = {
                'Referer': f'https://kick.com/{self.streamer_name}',
                'Origin': 'https://kick.com',
                'x-client-token': self.client_token,
                'User-Agent': scraper.headers.get('User-Agent', 'Mozilla/5.0')
            }

            logger_mgr.debug(f"Fetching WebSocket token from: {token_url}")
            token_response = await asyncio.to_thread(scraper.get, token_url, headers=headers, timeout=20)

            if token_response.status_code == 403:
                logger_mgr.error("Got 403 on token request - Cloudflare block")
                raise ValueError("403 Forbidden on token request")

            token_response.raise_for_status()
            token_data = token_response.json()
            ws_token = token_data.get("data", {}).get("token") or token_data.get("token")

            if not ws_token:
                logger_mgr.error(f"Token response: {token_data}")
                raise ValueError("Failed to get WebSocket token")

            logger_mgr.debug("Successfully retrieved WebSocket token")
            return ws_token

        except (ConnectionError, requests.exceptions.ConnectionError) as e:
            # Handle connection errors more gracefully - these are common
            if "RemoteDisconnected" in str(e):
                logger_mgr.warning(f"Token request connection dropped by server (common during high load)")
            else:
                logger_mgr.warning(f"Token request connection error: {str(e)[:100]}")
            return None
        except Exception as e:
            # Only show full traceback in debug mode
            logger_mgr.error(f"Failed to get WebSocket token: {e}", exc_info=self.debug)
            return None


    async def _handle_websocket_heartbeat(self, channel_id, livestream_id, token, ipv6_address, logger_mgr):
        """Handle WebSocket connection with IPv6 binding and retry logic"""
        ws_url = f"wss://websockets.kick.com/viewer/v1/connect?token={token}"
        max_ws_retries = 3
        base_retry_delay = 2

        for retry_attempt in range(max_ws_retries):
            # Use semaphore ONLY for connection establishment to avoid thundering herd
            async with self.websocket_semaphore:
                # Add minimum delay between WebSocket connections to avoid rate limiting
                current_time = time.time()
                time_since_last = current_time - self.last_websocket_time
                if time_since_last < 0.2:  # Minimum 200ms between connections
                    await asyncio.sleep(0.2 - time_since_last)
                self.last_websocket_time = time.time()

                logger_mgr.debug(f"WebSocket connection attempt {retry_attempt + 1}/{max_ws_retries} for channel {channel_id}")

                try:
                    # Connect WebSocket with increased timeout and retry logic
                    websocket = await websockets.connect(
                        ws_url,
                        ping_interval=20,
                        ping_timeout=20,
                        open_timeout=30,  # Increase from default 10s to 30s
                        close_timeout=10
                    )
                except Exception as e:
                    logger_mgr.warning(f"Failed to establish WebSocket connection: {e}")
                    if retry_attempt + 1 >= max_ws_retries:
                        return
                    await asyncio.sleep(base_retry_delay * (retry_attempt + 1))
                    continue

            # Semaphore released here - connection established
            # Now handle the WebSocket session without holding the semaphore
            try:
                self.open_sockets += 1
                logger_mgr.info(f"WebSocket connected for channel ID {channel_id} (total: {self.open_sockets})")

                # Socket binding is already handled at session level in _execute_viewer_simulation
                # No need to bind again for WebSocket connection
                try:
                    async with websocket:
                        # Send initial handshake
                        initial_handshake = {
                            "type": "channel_handshake",
                            "data": {"message": {"channelId": str(channel_id)}}
                        }
                        await websocket.send(json.dumps(initial_handshake))
                        logger_mgr.debug("Sent initial channel handshake")

                        # Send initial ping
                        await websocket.send(json.dumps({"type": "ping"}))
                        logger_mgr.debug("Sent initial ping")

                        async def receive_handler():
                            """Handle incoming messages"""
                            try:
                                async for message in websocket:
                                    if self.debug:
                                        logger_mgr.debug(f"Received: {message[:200]}")
                                    try:
                                        data = json.loads(message)
                                        if data.get('type') == 'pong':
                                            logger_mgr.debug("Received pong")
                                    except json.JSONDecodeError:
                                        logger_mgr.warning(f"Could not decode: {message[:200]}")
                            except ConnectionClosed:
                                logger_mgr.debug("Receive handler: connection closed")
                            except Exception as e:
                                logger_mgr.error(f"Receive handler error: {e}")

                        async def periodic_handshake():
                            """Send handshakes every 15 seconds"""
                            msg = {"type": "channel_handshake", "data": {"message": {"channelId": str(channel_id)}}}
                            while True:
                                await asyncio.sleep(15)
                                if not self.is_online:
                                    break
                                try:
                                    await websocket.send(json.dumps(msg))
                                    logger_mgr.debug("Sent periodic handshake")
                                except (ConnectionClosed, Exception) as e:
                                    logger_mgr.debug(f"Handshake loop ending: {e}")
                                    break

                        async def periodic_ping():
                            """Send pings every 30 seconds"""
                            while True:
                                await asyncio.sleep(30)
                                if not self.is_online:
                                    break
                                try:
                                    await websocket.send(json.dumps({"type": "ping"}))
                                    logger_mgr.debug("Sent periodic ping")
                                except (ConnectionClosed, Exception) as e:
                                    logger_mgr.debug(f"Ping loop ending: {e}")
                                    break

                        async def tracking_event():
                            """Send tracking event every 2 minutes (critical for viewer count!)"""
                            # Send initial tracking event immediately if we have a livestream
                            if livestream_id:
                                try:
                                    tracking_msg = {
                                        "type": "user_event",
                                        "data": {
                                            "message": {
                                                "name": "tracking.user.watch.livestream",
                                                "channel_id": int(channel_id),
                                                "livestream_id": int(livestream_id)
                                            }
                                        }
                                    }
                                    await websocket.send(json.dumps(tracking_msg))
                                    logger_mgr.info(f"Sent initial tracking event for livestream {livestream_id}")
                                except (ConnectionClosed, Exception) as e:
                                    logger_mgr.debug(f"Failed to send initial tracking event: {e}")

                            # Continue sending every 2 minutes
                            while True:
                                await asyncio.sleep(120)  # 2 minutes
                                if not self.is_online:
                                    break
                                try:
                                    if livestream_id:
                                        tracking_msg = {
                                            "type": "user_event",
                                            "data": {
                                                "message": {
                                                    "name": "tracking.user.watch.livestream",
                                                    "channel_id": int(channel_id),
                                                    "livestream_id": int(livestream_id)
                                                }
                                            }
                                        }
                                        await websocket.send(json.dumps(tracking_msg))
                                        logger_mgr.debug(f"Sent periodic tracking event for livestream {livestream_id}")
                                except (ConnectionClosed, Exception) as e:
                                    logger_mgr.debug(f"Tracking loop ending: {e}")
                                    break

                        # Run all tasks concurrently
                        await asyncio.gather(
                            receive_handler(),
                            periodic_handshake(),
                            periodic_ping(),
                            tracking_event()  # This is critical for being counted!
                        )

                        logger_mgr.info(f"WebSocket tasks completed for channel {channel_id}")
                        # Success - WebSocket completed normally, will decrement in finally block
                        return  # Success - exit retry loop

                except (ConnectionClosed, websockets.exceptions.ConnectionClosedError) as e:
                    logger_mgr.warning(f"WebSocket connection closed: {e}")
                    raise
                except websockets.exceptions.InvalidStatus as e:
                    # Handle HTTP 429 (Too Many Requests) specifically
                    if hasattr(e, 'status_code') and e.status_code == 429:
                        logger_mgr.warning(f"HTTP 429 Rate limited - will retry with backoff")
                        # Extract Retry-After header if available
                        retry_after = 10  # Default 10 seconds
                        if hasattr(e, 'headers') and 'Retry-After' in e.headers:
                            retry_after = int(e.headers['Retry-After'])
                        logger_mgr.info(f"Waiting {retry_after} seconds before retry (Cloudflare rate limit)")
                        await asyncio.sleep(retry_after + random.uniform(1, 3))  # Add jitter
                        raise  # Re-raise to trigger retry logic
                    else:
                        logger_mgr.error(f"WebSocket handshake failed: {e}")
                        raise
                except Exception as e:
                    logger_mgr.error(f"WebSocket error: {e}", exc_info=self.debug)
                    raise

                finally:
                    # Always decrement the socket counter when WebSocket connection ends
                    self.open_sockets -= 1
                    logger_mgr.info(f"WebSocket disconnected for channel {channel_id} (remaining: {self.open_sockets})")

            except asyncio.TimeoutError as e:
                # Note: socket counter decremented in the inner finally block
                logger_mgr.warning(f"WebSocket timeout (attempt {retry_attempt + 1}/{max_ws_retries}): {e}")

                if retry_attempt + 1 >= max_ws_retries:
                    logger_mgr.error(f"Max WebSocket retries reached for channel {channel_id}")
                    raise

                # Exponential backoff with jitter
                retry_delay = base_retry_delay * (2 ** retry_attempt) + random.uniform(0, 2)
                logger_mgr.info(f"Retrying WebSocket connection in {retry_delay:.1f} seconds...")
                await asyncio.sleep(retry_delay)

            except (ConnectionClosed, websockets.exceptions.ConnectionClosedError) as e:
                # Note: socket counter decremented in the inner finally block
                logger_mgr.warning(f"WebSocket closed (attempt {retry_attempt + 1}/{max_ws_retries}): {e}")

                if retry_attempt + 1 >= max_ws_retries:
                    raise

                await asyncio.sleep(base_retry_delay)

            except websockets.exceptions.InvalidStatus as e:
                # Note: socket counter decremented in the inner finally block
                # Special handling for HTTP 429
                if hasattr(e, 'status_code') and e.status_code == 429:
                    logger_mgr.warning(f"HTTP 429 on attempt {retry_attempt + 1}/{max_ws_retries}")
                    # Use longer delay for rate limiting
                    retry_delay = 10 + (retry_attempt * 5) + random.uniform(0, 5)
                    logger_mgr.info(f"Rate limited - waiting {retry_delay:.1f}s before retry")
                    await asyncio.sleep(retry_delay)
                else:
                    logger_mgr.error(f"WebSocket error for channel {channel_id}: {e}", exc_info=self.debug)
                    if retry_attempt + 1 >= max_ws_retries:
                        raise
                    await asyncio.sleep(base_retry_delay)

        # If we get here, all retries failed
        logger_mgr.error(f"All WebSocket connection attempts failed for channel {channel_id}")

    async def _shutdown(self):
        """Shutdown all workers cleanly"""
        print("\nCancelling all worker manager tasks...", file=sys.stdout)
        self.is_online = False
        self.required_workers = 0

        tasks_to_cancel = list(self.worker_manager_tasks)
        for task in tasks_to_cancel:
            if not task.done():
                task.cancel()

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        self.worker_manager_tasks.clear()
        await self.c2_api.update_status("Offline", 0)
        print("Shutdown complete.", file=sys.stdout)


async def main():
    c2_api = C2_API(base_url=args.c2_url, hostname=socket.gethostname())
    bot = IPv6Bot(c2_api=c2_api, interface=args.interface, debug=args.debug)

    main_task = None
    try:
        main_task = asyncio.create_task(bot.run())
        await main_task
    except asyncio.CancelledError:
        logger.info("Main task was cancelled.")
    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt, exiting.", file=sys.stdout)
        if main_task and not main_task.done():
            main_task.cancel()
            try:
                await main_task
            except asyncio.CancelledError:
                logger.info("Main task successfully cancelled.")
    finally:
        logger.info("Closing C2 API session.")
        await c2_api.close()
        logger.info("Program exited.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt at top level.", file=sys.stdout)