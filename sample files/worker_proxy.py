import asyncio, aiohttp, m3u8, argparse, socket, time, random, string, logging, resource, psutil, sys, json
import re
import websockets # Ensure websockets is imported for ConnectionClosed
from websockets_proxy import Proxy, proxy_connect
import cloudscraper
from fake_useragent import UserAgent
from urllib.parse import urljoin
from jose import jwt
from datetime import datetime, timezone

try:
    soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
    new_limit = min(soft_limit * 4, hard_limit)
    resource.setrlimit(resource.RLIMIT_NOFILE, (new_limit, hard_limit))
except (ValueError, OSError): pass

parser = argparse.ArgumentParser(description='Async Worker Script with Kick.com WebSocket Support')
parser.add_argument('--debug', action='store_true', help='Enable debug mode to print detailed logs')
args = parser.parse_args()
log_level = logging.DEBUG if args.debug else logging.INFO
logging.basicConfig(level=log_level, stream=sys.stderr, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', force=True)


class C2_API:
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
            except (jwt.ExpiredSignatureError, jwt.JWTError): pass
        try:
            async with self.session.post(urljoin(self.base_url, "login"), json={"hostname": self.hostname}, timeout=10) as response:
                response.raise_for_status()
                data = await response.json()
                self.auth_token = f"Bearer {data['token']}"
                self.session.headers['access-token'] = self.auth_token
                return True
        except Exception as e:
            self.auth_token = None
            logging.error(f"C2_API: Failed to obtain auth token: {e}")
            return False
    async def _api_request(self, method, endpoint, **kwargs):
        if not await self._login(): return None
        try:
            async with self.session.request(method, urljoin(self.base_url, endpoint), timeout=15, **kwargs) as response:
                response.raise_for_status()
                return await response.json() if response.content_length != 0 else {}
        except Exception as e:
            logging.error(f"C2_API: Request to {endpoint} failed: {e}")
            return None
    async def fetch_instructions(self):
        max_attempts = 3
        for attempt in range(max_attempts):
            instructions = await self._api_request('GET', f"instructions/{self.hostname}")
            if instructions is not None:
                return instructions
            logging.warning(f"Attempt {attempt + 1}/{max_attempts} to fetch instructions failed.")
            if attempt + 1 < max_attempts:
                await asyncio.sleep(5)
        return None
    async def fetch_proxies(self, count):
        if count <= 0: return []
        data = await self._api_request('GET', f"proxies/{self.hostname}/{count}")
        return [p[0] for p in data.get('updated_data', []) if p] if data else []
    async def update_status(self, status, workers):
        payload = {"client_hostname": self.hostname, "status": status, "workers": workers}
        await self._api_request('POST', "state", json=payload)
    async def clear_checkouts(self):
        logging.info("Attempting to clear any previous resource checkouts.")
        await self._api_request('POST', f"release-resources/{self.hostname}")

class HLSBot:
    def __init__(self, c2_api, debug=False):
        self.c2_api = c2_api
        self.debug = debug # Retain self.debug from original
        self.worker_manager_tasks = set()
        self.total_failures = 0
        self.active_sessions = 0
        self.open_sockets = 0
        self.required_workers = 0
        self.playback_url = None
        self.proxy_pool = asyncio.Queue()
        self.ua_generator = UserAgent()
        self.is_online = False
        self.streamer_name = None
    async def run(self):
        await self.c2_api.clear_checkouts()
        summary_task = asyncio.create_task(self._log_summary_periodically())
        pool_manager_task = asyncio.create_task(self._proxy_pool_manager())
        try:
            while True:
                instructions = await self.c2_api.fetch_instructions()
                if instructions:
                    await self._process_instructions(instructions)
                else:
                    logging.warning("Failed to get instructions after multiple retries. Assuming OFFLINE.")
                    await self._process_instructions({'status': 'offline', 'count': 0})
                await asyncio.sleep(30)
        except asyncio.CancelledError:
            logging.info("Main loop cancelled.")
        finally:
            logging.info("Shutting down all tasks...")
            summary_task.cancel()
            pool_manager_task.cancel()
            await self._shutdown()
    async def _log_summary_periodically(self):
        while True:
            try:
                cpu_usage = psutil.cpu_percent()
                mem_usage = psutil.virtual_memory().percent
                status = "ONLINE" if self.is_online else "OFFLINE"
                target = self.streamer_name or "N/A"
                summary = (
                    f"Status: {status} | "
                    f"Target: {target} | "
                    f"Workers: {self.active_sessions}/{self.required_workers} | "
                    f"Open Sockets: {self.open_sockets}/{self.required_workers} | "
                    f"Proxy Pool: {self.proxy_pool.qsize()} | "
                    f"Failures: {self.total_failures} | "
                    f"CPU: {cpu_usage:.1f}% | "
                    f"Memory: {mem_usage:.1f}%   "
                )
                print(summary, end='\r', file=sys.stdout, flush=True)
                await asyncio.sleep(1)
            except asyncio.CancelledError: break
    async def _proxy_pool_manager(self):
        MAX_FETCH_BATCH_SIZE = 100
        while True:
            try:
                if self.is_online:
                    needed = self.required_workers - self.proxy_pool.qsize()
                    if needed > 0:
                        fetch_size = min(needed, MAX_FETCH_BATCH_SIZE)
                        logging.info(f"Proxy pool needs {needed} (fetching {fetch_size})...")
                        new_proxies = await self.c2_api.fetch_proxies(fetch_size)
                        if new_proxies:
                            for proxy in new_proxies:
                                await self.proxy_pool.put(proxy)
                            logging.info(f"Added {len(new_proxies)} proxies. Pool size: {self.proxy_pool.qsize()}.")
                await asyncio.sleep(5)
            except asyncio.CancelledError: break
            except Exception as e:
                logging.error(f"Error in proxy pool manager: {e}")
                await asyncio.sleep(30)
    async def _process_instructions(self, instructions):
        status = instructions.get('status')
        if status == 'online':
            was_offline = not self.is_online
            self.is_online = True
            self.required_workers = instructions.get('count', 0)
            self.playback_url = instructions.get('playback_url')
            self.streamer_name = instructions.get('target')
            if was_offline:
                logging.info("Transitioning to ONLINE. Populating initial proxy pool...")
                initial_needed = self.required_workers - self.proxy_pool.qsize()
                if initial_needed > 0:
                    initial_proxies = await self.c2_api.fetch_proxies(initial_needed)
                    if initial_proxies:
                        for proxy in initial_proxies:
                            await self.proxy_pool.put(proxy)
                        logging.info(f"Initial population complete. Pool size: {self.proxy_pool.qsize()}.")
            if not self.playback_url or not self.streamer_name:
                logging.error("Stream is ONLINE but 'playback_url' or 'target' is missing.")
                self.is_online = False
                self.required_workers = 0
        else:
            if self.is_online:
                logging.info("Transitioning to OFFLINE. Clearing proxy pool.")
                while not self.proxy_pool.empty(): self.proxy_pool.get_nowait()
            self.is_online = False
            self.required_workers = 0
        await self._adjust_worker_managers()
        await self.c2_api.update_status("Online" if self.is_online else "Offline", self.active_sessions)
    async def _adjust_worker_managers(self):
        self.worker_manager_tasks = {t for t in self.worker_manager_tasks if not t.done()}
        while len(self.worker_manager_tasks) > self.required_workers:
            self.worker_manager_tasks.pop().cancel()
        while len(self.worker_manager_tasks) < self.required_workers:
            self.worker_manager_tasks.add(asyncio.create_task(self._worker_manager()))
    
    async def _worker_manager(self):
        manager_id = id(asyncio.current_task()) % 1000
        logger = logging.getLogger(f"Manager-{manager_id:03d}")
        try:
            while True:
                if not self.is_online:
                    logger.debug("Idle (master status is OFFLINE).")
                    await asyncio.sleep(15)
                    continue
                
                logger.debug("Waiting to acquire a proxy from the central pool...")
                proxy = await self.proxy_pool.get()
                self.active_sessions += 1
                try:
                    logger.debug(f"Acquired proxy. Starting session for '{self.streamer_name}'.")
                    await self._execute_viewer_simulation(proxy, logger)
                except asyncio.CancelledError:
                    # This ensures the CancelledError from _execute_viewer_simulation
                    # (ultimately from _handle_websocket_heartbeat) is re-raised
                    # to stop this worker_manager task if it's cancelled.
                    raise
                except Exception as e:
                    self.total_failures += 1
                    logger.error(f"Session with proxy failed terminally and will be restarted. Reason: {e}", exc_info=self.debug) # Use self.debug
                finally:
                    self.active_sessions -= 1
                    self.proxy_pool.task_done()
        except asyncio.CancelledError:
            logger.debug("Manager has been cancelled and is shutting down.")
        except Exception as e:
            logger.error(f"Manager caught unexpected fatal error: {e}", exc_info=self.debug) # Use self.debug

    async def _execute_viewer_simulation(self, proxy, logger):
        scraper = cloudscraper.create_scraper(browser={'custom': 'ScraperBot/1.0'})
        scraper.proxies = {"http": proxy, "https": proxy}
        max_session_attempts = 5

        try:
            for attempt in range(max_session_attempts): # Original loop starts attempt from 0
                try:
                    session_user_agent = self.ua_generator.random
                    scraper.headers.update({'User-Agent': session_user_agent, 'Referer': f'https://kick.com/{self.streamer_name}'})
                    
                    logger.debug(f"Session attempt {attempt + 1}/{max_session_attempts}: Getting fresh stream details.")
                    details = await self._get_stream_details(scraper, logger) # Pass logger
                    if not details:
                        raise ValueError("Failed to get stream details")

                    await self._handle_websocket_heartbeat(
                        details['channel_id'], details['ws_token'], proxy, session_user_agent, logger
                    )
                    # If _handle_websocket_heartbeat returns, it implies the WebSocket connection
                    # or its tasks ended. In our new design, this means it should be retried.
                    logger.warning("WebSocket handling ended. This will trigger a session retry if attempts remain.")
                    raise websockets.ConnectionClosed(None, "WebSocket handling completed or connection closed")


                except asyncio.CancelledError:
                    logger.info("Viewer simulation was cancelled by manager.")
                    raise

                except Exception as e:
                    logger.warning(f"Recoverable session error (attempt {attempt + 1}/{max_session_attempts}): {e}. Re-authenticating...", exc_info=self.debug) # Use self.debug
                    if attempt + 1 >= max_session_attempts:
                        logger.error("Max session retry attempts reached. Failing the worker.")
                        raise 
                    
                    await asyncio.sleep(random.uniform(5, 10))
        finally:
            try:
                scraper.close()
            except: # Original script had a bare except
                pass

    async def _get_stream_details(self, scraper, logger): # Added logger as it's used
        if not self.streamer_name: return None
        base_url = "https://kick.com"
        channel_page_url = urljoin(base_url, self.streamer_name)
        client_token = None
        try:
            logger.debug(f"Fetching main page to find JS files: {channel_page_url}")
            page_response = await asyncio.to_thread(scraper.get, channel_page_url, timeout=30)
            page_response.raise_for_status()
            js_paths = re.findall(r'src="(/_next/static/chunks/[^"]+\.js)"', page_response.text)
            if not js_paths: raise ValueError("Could not find any Next.js JS files in page source.")
            logger.debug(f"Found {len(js_paths)} JS files, searching for token...")
            for path in js_paths:
                js_full_url = urljoin(base_url, path)
                js_response = await asyncio.to_thread(scraper.get, js_full_url, timeout=20)
                # Original regex from your script:
                token_match = re.search(r'NEXT_PUBLIC_WEBSOCKET_CLIENT_TOKEN.*?default\("([a-f0-9]+)"\)', js_response.text)
                if token_match:
                    client_token = token_match.group(1)
                    logger.debug(f"Success! Found token in {path}")
                    break
            if not client_token: raise ValueError("FATAL: Could not find client token after scanning JS files.")
            api_url = f"https://kick.com/api/v2/channels/{self.streamer_name}"
            channel_response = await asyncio.to_thread(scraper.get, api_url, timeout=20)
            channel_data = channel_response.json()
            channel_id = channel_data.get("id")
            if not channel_id: raise ValueError("Could not find Channel ID ('id') in the API response.")
            token_url = "https://websockets.kick.com/viewer/v1/token"
            headers = {'Referer': channel_page_url, 'Origin': base_url, 'Accept': 'application/json', 'x-client-token': client_token}
            token_response = await asyncio.to_thread(scraper.get, token_url, headers=headers, timeout=20)
            ws_token = token_response.json().get("data", {}).get("token")
            if not ws_token: raise ValueError("Failed to get the final WebSocket token from the nested JSON.")
            logger.debug("Successfully retrieved all necessary authentication details.")
            return {'channel_id': channel_id, 'ws_token': ws_token}
        except Exception as e:
            logger.error(f"Failed during stream detail acquisition: {e}", exc_info=self.debug) # Use self.debug
            raise

    async def _handle_websocket_heartbeat(self, channel_id, token, proxy, user_agent, logger):
        ws_url = f"wss://websockets.kick.com/viewer/v1/connect?token={token}"
        proxy_obj = Proxy.from_url(proxy)
        try:
            self.open_sockets += 1
            logger.debug(f"Attempting WebSocket connection to {ws_url} via proxy {proxy_obj.proxy_host}:{proxy_obj.proxy_port} for channel {channel_id}. Open sockets: {self.open_sockets}")
            try:
                # Using ping_interval and ping_timeout from your original script's _handle_websocket_heartbeat
                async with proxy_connect(
                    ws_url, proxy=proxy_obj, user_agent_header=user_agent,
                    open_timeout=15, close_timeout=10,
                    ping_interval=20, ping_timeout=20 # These are for WebSocket RFC PINGs
                ) as websocket:
                    logger.info(f"WebSocket connection established for channel ID {channel_id}.")

                    # 1. Send Initial Channel Handshake (as observed in browser)
                    initial_handshake_payload = {
                        "type": "channel_handshake",
                        "data": {"message": {"channelId": str(channel_id)}}
                    }
                    await websocket.send(json.dumps(initial_handshake_payload))
                    logger.debug(f"Sent initial channel_handshake: {initial_handshake_payload}")

                    # 2. Send Initial Ping (as observed in browser)
                    initial_ping_payload = {"type": "ping"}
                    await websocket.send(json.dumps(initial_ping_payload))
                    logger.debug(f"Sent initial ping: {initial_ping_payload}")

                    async def receive_handler_internal():
                        """Handles incoming messages from the server."""
                        async for message in websocket:
                            if self.debug: # Check self.debug for logging level
                                logger.debug(f"RAW WebSocket message received: {message[:200]}")
                            try:
                                data = json.loads(message)
                                if self.debug: # Check self.debug for logging level
                                    logger.debug(f"Parsed WebSocket data: {data}")

                                if data.get('type') == 'pong':
                                    logger.debug("Client received {'type':'pong'} from server.")
                                # The original script's receive_handler had pusher:ping and pusher_internal:subscription_succeeded.
                                # Since Kick.com seems to use a custom protocol, these are unlikely to be sent by the server.
                                # We log other messages to see what we actually get (e.g., chat messages).
                                else:
                                    logger.info(f"Received other WebSocket message (type: {data.get('type', 'N/A')}, event: {data.get('event', 'N/A')})")
                                    if self.debug and 'data' in data:
                                       logger.debug(f"Message data content: {data['data']}")

                            except json.JSONDecodeError:
                                logger.warning(f"Could not decode JSON from WebSocket message: {message[:200]}")
                            except AttributeError:
                                parsed_data_str = str(data) if 'data' in locals() else "Unknown structure"
                                logger.warning(f"Parsed message is not a dictionary or lacks expected structure: {parsed_data_str}")
                            except Exception as e:
                                logger.error(f"Error in receive_handler_internal: {e}", exc_info=self.debug) # Use self.debug

                    async def periodic_handshake_sender_internal():
                        """Sends periodic channel_handshake messages every ~15 seconds."""
                        handshake_interval_seconds = 15  # As observed
                        msg_payload = {"type": "channel_handshake", "data": {"message": {"channelId": str(channel_id)}}}
                        msg_json = json.dumps(msg_payload)
                        
                        while True:
                            await asyncio.sleep(handshake_interval_seconds)
                            if not websocket.open:
                                logger.info("Periodic Handshake sender: WebSocket no longer open, stopping.")
                                break
                            if not self.is_online: 
                                logger.info("Periodic Handshake sender: Master status OFFLINE, stopping.")
                                break
                            try:
                                await websocket.send(msg_json)
                                logger.debug(f"Sent periodic channel_handshake: {msg_payload}")
                            except websockets.ConnectionClosed:
                                logger.warning("Periodic Handshake sender: WebSocket closed while trying to send.")
                                break
                            except Exception as e:
                                logger.error(f"Periodic Handshake sender: Error sending handshake: {e}", exc_info=self.debug) # Use self.debug
                                break
                    
                    async def client_json_pinger_internal():
                        """Sends application-level JSON pings from the client every ~30 seconds."""
                        app_ping_interval_seconds = 30  # As observed
                        ping_message_json = json.dumps({"type": "ping"})
                        
                        while True:
                            await asyncio.sleep(app_ping_interval_seconds)
                            if not websocket.open:
                                logger.info("Client JSON pinger: WebSocket no longer open, stopping.")
                                break
                            if not self.is_online: 
                                logger.info("Client JSON pinger: Master status OFFLINE, stopping.")
                                break
                            try:
                                await websocket.send(ping_message_json)
                                logger.debug("Client sent application-level {'type':'ping'}")
                            except websockets.ConnectionClosed:
                                logger.warning("Client JSON pinger: WebSocket closed while trying to send ping.")
                                break
                            except Exception as e:
                                logger.error(f"Client JSON pinger: Error sending application ping: {e}", exc_info=self.debug) # Use self.debug
                                break
                    
                    await asyncio.gather(
                        receive_handler_internal(),
                        periodic_handshake_sender_internal(),
                        client_json_pinger_internal()
                    )
                    logger.info(f"WebSocket tasks for channel {channel_id} have completed. Connection likely closed.")

            except websockets.exceptions.InvalidStatusCode as e:
                logger.error(f"WebSocket handshake failed for channel {channel_id}. Status: {e.status_code}. Headers: {e.headers}", exc_info=self.debug)
                raise # Re-raise to be caught by _execute_viewer_simulation's retry logic
            except Exception as e: # Catch other errors during connect or during gather
                logger.error(f"Error during WebSocket connection or handling for channel {channel_id}: {e}", exc_info=self.debug) # Use self.debug
                raise # Re-raise to be caught by _execute_viewer_simulation's retry logic
            finally:
                logger.warning(f"WebSocket connection for channel {channel_id} has been closed or failed to establish properly.")
                self.open_sockets -= 1
                logger.debug(f"Decremented open_sockets to {self.open_sockets} for channel {channel_id}")
        except asyncio.CancelledError:
            logger.info(f"WebSocket task for channel ID {channel_id} was cancelled.")
            raise

    async def _shutdown(self):
        print("\nCancelling all worker manager tasks...", file=sys.stdout)
        self.is_online = False
        self.required_workers = 0
        # Create a list of tasks to cancel to avoid issues with modifying set during iteration
        tasks_to_cancel = list(self.worker_manager_tasks)
        for task in tasks_to_cancel:
            if not task.done(): # Check if task is not already done
                task.cancel()
        # Wait for all tasks to complete their cancellation
        if tasks_to_cancel: # Only gather if there were tasks
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        self.worker_manager_tasks.clear() # Clear the set after cancellation

        await self.c2_api.update_status("Offline", 0)
        print("Shutdown complete.", file=sys.stdout)

async def main():
    CONTROL_API_URL = "http://947w29bj18c09j.claimcasino.com:8910/api/"
    c2_api = C2_API(base_url=CONTROL_API_URL, hostname=socket.gethostname())
    bot = HLSBot(c2_api=c2_api, debug=args.debug) # Pass debug to HLSBot
    
    main_task = None
    try:
        main_task = asyncio.create_task(bot.run())
        await main_task
    except asyncio.CancelledError:
        logging.info("Main task was cancelled.") # Log if bot.run() is cancelled
    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt, exiting.", file=sys.stdout)
        if main_task and not main_task.done(): # If bot.run() is running, cancel it
            main_task.cancel()
            try:
                await main_task # Allow cleanup in bot.run() finally block
            except asyncio.CancelledError:
                logging.info("Main task successfully cancelled by KeyboardInterrupt.") # Expected
    finally:
        # This ensures c2_api.close() is called even if bot.run() has issues
        logging.info("Closing C2 API session in main finally block.")
        await c2_api.close()
        logging.info("Program exited from main.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # This secondary try/except is mostly redundant if the main() handles KI,
        # but it's harmless. The main() function's KI handling is more robust.
        print("\nCaught KeyboardInterrupt at top level, exiting.", file=sys.stdout)
