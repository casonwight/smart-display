"""Spotify Web API controller using spotipy."""

import time
import threading
from typing import Optional, List

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIPY_AVAILABLE = True
except ImportError:
    SPOTIPY_AVAILABLE = False

from config import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_CACHE_PATH,
)

# Full scopes needed for browse features (playlists, liked songs, recently played)
SCOPES = (
    "user-modify-playback-state user-read-playback-state "
    "user-library-read user-read-recently-played "
    "playlist-read-private playlist-read-collaborative"
)

# Basic scopes for playback control only (compatible with tokens created before browse was added)
BASIC_SCOPES = "user-modify-playback-state user-read-playback-state"

# Target device name (matches raspotify config)
RASPOTIFY_DEVICE_NAME = "Kitchen Display"


class SpotifyController:
    """Controls Spotify playback via the Web API."""

    def __init__(self):
        self._sp: Optional["spotipy.Spotify"] = None
        self._device_id: Optional[str] = None
        self._device_cache_time: float = 0
        self._lock = threading.Lock()
        # Skip queue: aggregates rapid encoder rotations into a single worker thread
        self._pending_skips: int = 0   # +N = skip next N, -N = skip previous N
        self._skip_queue_lock = threading.Lock()
        self._skip_worker_running = False
        # Called after skips are successfully applied (used by MusicApp to poll for new track)
        self.on_skip_success = None
        self._init()

    def _init(self):
        if not SPOTIPY_AVAILABLE:
            print("[Spotify] spotipy not installed - controls disabled")
            return
        if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
            print("[Spotify] No credentials in config - controls disabled")
            print("[Spotify] Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET, then run scripts/spotify_auth.py")
            return
        try:
            # Try full scopes first (enables browse features)
            auth = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=SCOPES,
                cache_path=SPOTIFY_CACHE_PATH,
                open_browser=False,
            )
            token_info = auth.get_cached_token()
            if token_info:
                self._sp = spotipy.Spotify(auth_manager=auth)
                print("[Spotify] Controller initialized (full browse mode)")
                return

            # Fall back to basic scopes - works with tokens created before browse was added
            auth_basic = SpotifyOAuth(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
                redirect_uri=SPOTIFY_REDIRECT_URI,
                scope=BASIC_SCOPES,
                cache_path=SPOTIFY_CACHE_PATH,
                open_browser=False,
            )
            token_info = auth_basic.get_cached_token()
            if token_info:
                self._sp = spotipy.Spotify(auth_manager=auth_basic)
                print("[Spotify] Controller initialized (basic mode - playlists/liked/recent unavailable)")
                print("[Spotify] Run 'python scripts/spotify_auth.py' to enable browse features")
            else:
                print("[Spotify] No cached token - run: python scripts/spotify_auth.py")
        except Exception as e:
            print(f"[Spotify] Init failed: {e}")

    @property
    def available(self) -> bool:
        return self._sp is not None

    def _get_device_id(self) -> Optional[str]:
        """Return the Kitchen Display device ID, with 60-second caching."""
        now = time.time()
        if self._device_id and now - self._device_cache_time < 60:
            return self._device_id
        try:
            devices = self._sp.devices()
            device_list = devices.get("devices", [])
            names = [(d.get("name", "?"), d.get("is_active", False)) for d in device_list]
            print(f"[Spotify] Device lookup: {names}")
            # Prefer raspotify by name
            for device in device_list:
                if RASPOTIFY_DEVICE_NAME in device.get("name", ""):
                    self._device_id = device["id"]
                    self._device_cache_time = now
                    print(f"[Spotify] Using '{device.get('name')}' id={self._device_id[:8]}…")
                    return self._device_id
            # Fall back to any active device
            for device in device_list:
                if device.get("is_active"):
                    self._device_id = device["id"]
                    self._device_cache_time = now
                    print(f"[Spotify] Using active device '{device.get('name')}' id={self._device_id[:8]}…")
                    return self._device_id
            # Fall back to first device
            if device_list:
                self._device_id = device_list[0]["id"]
                self._device_cache_time = now
                print(f"[Spotify] Using first device '{device_list[0].get('name')}' id={self._device_id[:8]}…")
                return self._device_id
            print("[Spotify] No devices found in device list")
        except Exception as e:
            print(f"[Spotify] Device lookup failed: {e}")
        return None

    def _call(self, fn, *args, **kwargs) -> bool:
        """Execute a Spotify API call safely."""
        if not self.available:
            return False
        try:
            with self._lock:
                fn(*args, **kwargs)
            return True
        except Exception as e:
            print(f"[Spotify] API call failed: {e}")
            # Only invalidate device cache on connection errors, not playback restrictions
            if "Restriction violated" not in str(e):
                self._device_id = None
            return False

    def pause(self) -> bool:
        device_id = self._get_device_id()
        return self._call(self._sp.pause_playback, device_id=device_id)

    def resume(self) -> bool:
        device_id = self._get_device_id()
        return self._call(self._sp.start_playback, device_id=device_id)

    def toggle_play_pause(self, currently_playing: bool) -> bool:
        if currently_playing:
            return self.pause()
        else:
            return self.resume()

    def next_track(self) -> bool:
        self.queue_skip("next")
        return True

    def previous_track(self) -> bool:
        self.queue_skip("previous")
        return True

    def queue_skip(self, direction: str) -> None:
        """Queue a skip command (non-blocking). Multiple rapid rotations are
        aggregated so only the net count of skips fires when the device is ready."""
        if not self.available:
            return
        delta = 1 if direction == "next" else -1
        with self._skip_queue_lock:
            self._pending_skips += delta
            if not self._skip_worker_running:
                self._skip_worker_running = True
                threading.Thread(target=self._skip_worker, daemon=True).start()

    def _skip_worker(self):
        """Background worker: apply all pending skips, retrying at 1s intervals
        until the device re-registers after a play command."""
        for attempt in range(15):
            if attempt > 0:
                time.sleep(1.0)

            # Early exit if nothing left to do
            with self._skip_queue_lock:
                if self._pending_skips == 0:
                    break

            # Fresh device lookup on every attempt
            self._device_id = None
            device_id = self._get_device_id()
            if device_id is None:
                print(f"[Spotify] Skip: no device (attempt {attempt + 1}/15)…")
                continue

            # Device found — drain all pending skip batches without further delay
            total_applied = 0  # Net skips applied (positive=next, negative=previous)
            while True:
                with self._skip_queue_lock:
                    pending = self._pending_skips
                    self._pending_skips = 0
                if pending == 0:
                    break

                fn = self._sp.next_track if pending > 0 else self._sp.previous_track
                label = "next" if pending > 0 else "previous"
                count = abs(pending)
                for i in range(count):
                    try:
                        with self._lock:
                            fn(device_id=device_id)
                        print(f"[Spotify] Skip {label} OK ({i + 1}/{count})")
                        total_applied += 1 if pending > 0 else -1
                    except Exception as e:
                        err = str(e)
                        if "Restriction violated" in err:
                            print(f"[Spotify] Skip {label} restricted: {err}")
                        else:
                            print(f"[Spotify] Skip {label} {i + 1}/{count} failed: {e}")
                            self._device_id = None
                        break
            # Notify music app: pass count and direction so it can use prefetch or poll
            if self.on_skip_success and total_applied != 0:
                direction = "next" if total_applied > 0 else "previous"
                self.on_skip_success(abs(total_applied), direction)
            break  # Done — exit retry loop

        else:
            print("[Spotify] Skip: all 15 retries exhausted, command dropped")

        with self._skip_queue_lock:
            self._skip_worker_running = False
            # Restart if more skips arrived after we drained the queue
            if self._pending_skips != 0:
                self._skip_worker_running = True
                threading.Thread(target=self._skip_worker, daemon=True).start()

    def play_search(self, query: str) -> Optional[str]:
        """Search for a track and start playing it. Returns track name or None on failure."""
        if not self.available:
            return None
        try:
            results = self._sp.search(q=query, limit=1, type="track")
            tracks = results.get("tracks", {}).get("items", [])
            if not tracks:
                print(f"[Spotify] No results for: {query}")
                return None
            track = tracks[0]
            uri = track["uri"]
            name = track["name"]
            artist = track["artists"][0]["name"] if track.get("artists") else ""
            device_id = self._get_device_id()
            self._call(self._sp.start_playback, device_id=device_id, uris=[uri])
            return f"{name} by {artist}" if artist else name
        except Exception as e:
            print(f"[Spotify] Play search failed: {e}")
            return None

    # ---- Browse methods ----

    def get_playlists(self) -> List[dict]:
        """Get user's playlists."""
        if not self.available:
            return []
        try:
            with self._lock:
                results = self._sp.current_user_playlists(limit=50)
            playlists = []
            for item in results.get("items", []):
                if not item:
                    continue
                images = item.get("images", [])
                playlists.append({
                    "id": item["id"],
                    "name": item["name"],
                    "uri": item["uri"],
                    "image_url": images[0]["url"] if images else "",
                    "track_count": item.get("tracks", {}).get("total", 0),
                })
            return playlists
        except Exception as e:
            print(f"[Spotify] get_playlists failed: {e}")
            return []

    def get_playlist_tracks(self, playlist_id: str) -> List[dict]:
        """Get tracks in a playlist."""
        if not self.available:
            return []
        try:
            with self._lock:
                results = self._sp.playlist_tracks(playlist_id, limit=50)
            tracks = []
            for item in results.get("items", []):
                if not item:
                    continue
                track = item.get("track")
                if not track:
                    continue
                artists = track.get("artists", [])
                album = track.get("album", {})
                images = album.get("images", [])
                tracks.append({
                    "uri": track["uri"],
                    "name": track["name"],
                    "artist": artists[0]["name"] if artists else "",
                    "artist_id": artists[0]["id"] if artists else "",
                    "duration_ms": track.get("duration_ms", 0),
                    "image_url": images[0]["url"] if images else "",
                })
            return tracks
        except Exception as e:
            print(f"[Spotify] get_playlist_tracks failed: {e}")
            return []

    def get_recently_played(self) -> List[dict]:
        """Get recently played tracks (deduplicated)."""
        if not self.available:
            return []
        try:
            with self._lock:
                results = self._sp.current_user_recently_played(limit=30)
            tracks = []
            seen_uris: set = set()
            for item in results.get("items", []):
                track = item.get("track")
                if not track:
                    continue
                uri = track["uri"]
                if uri in seen_uris:
                    continue
                seen_uris.add(uri)
                artists = track.get("artists", [])
                album = track.get("album", {})
                images = album.get("images", [])
                tracks.append({
                    "uri": uri,
                    "name": track["name"],
                    "artist": artists[0]["name"] if artists else "",
                    "artist_id": artists[0]["id"] if artists else "",
                    "duration_ms": track.get("duration_ms", 0),
                    "image_url": images[0]["url"] if images else "",
                })
            return tracks
        except Exception as e:
            print(f"[Spotify] get_recently_played failed: {e}")
            return []

    def get_liked_songs(self, limit: int = 50) -> List[dict]:
        """Get user's liked/saved tracks."""
        if not self.available:
            return []
        try:
            with self._lock:
                results = self._sp.current_user_saved_tracks(limit=limit)
            tracks = []
            for item in results.get("items", []):
                track = item.get("track")
                if not track:
                    continue
                artists = track.get("artists", [])
                album = track.get("album", {})
                images = album.get("images", [])
                tracks.append({
                    "uri": track["uri"],
                    "name": track["name"],
                    "artist": artists[0]["name"] if artists else "",
                    "artist_id": artists[0]["id"] if artists else "",
                    "duration_ms": track.get("duration_ms", 0),
                    "image_url": images[0]["url"] if images else "",
                })
            return tracks
        except Exception as e:
            print(f"[Spotify] get_liked_songs failed: {e}")
            return []

    def get_queue(self) -> List[dict]:
        """Get current playback queue."""
        if not self.available:
            return []
        try:
            with self._lock:
                results = self._sp.queue()
            tracks = []
            # Currently playing item first
            current = results.get("currently_playing")
            if current:
                artists = current.get("artists", [])
                album = current.get("album", {})
                images = album.get("images", [])
                tracks.append({
                    "uri": current["uri"],
                    "name": current["name"],
                    "artist": artists[0]["name"] if artists else "",
                    "artist_id": artists[0]["id"] if artists else "",
                    "duration_ms": current.get("duration_ms", 0),
                    "image_url": images[0]["url"] if images else "",
                    "is_current": True,
                })
            for item in results.get("queue", [])[:20]:
                if not item:
                    continue
                artists = item.get("artists", [])
                album = item.get("album", {})
                images = album.get("images", [])
                tracks.append({
                    "uri": item["uri"],
                    "name": item["name"],
                    "artist": artists[0]["name"] if artists else "",
                    "artist_id": artists[0]["id"] if artists else "",
                    "duration_ms": item.get("duration_ms", 0),
                    "image_url": images[0]["url"] if images else "",
                })
            return tracks
        except Exception as e:
            print(f"[Spotify] get_queue failed: {e}")
            return []

    def get_next_track(self) -> Optional[dict]:
        """Return the first track in the playback queue (not currently playing)."""
        if not self.available:
            return None
        try:
            with self._lock:
                results = self._sp.queue()
            queue = results.get("queue", [])
            if not queue:
                return None
            item = queue[0]
            if not item:
                return None
            artists = item.get("artists", [])
            album = item.get("album", {})
            images = album.get("images", [])
            return {
                "uri": item["uri"],
                "name": item["name"],
                "artist": artists[0]["name"] if artists else "",
                "artist_id": artists[0]["id"] if artists else "",
                "album": album.get("name", ""),
                "duration_ms": item.get("duration_ms", 0),
                "image_url": images[0]["url"] if images else "",
            }
        except Exception as e:
            print(f"[Spotify] get_next_track failed: {e}")
            return None

    def play_track(self, uri: str, context_uri: str = None) -> bool:
        """Play a specific track, optionally within a playlist context."""
        device_id = self._get_device_id()
        if context_uri:
            ok = self._call(
                self._sp.start_playback,
                device_id=device_id,
                context_uri=context_uri,
                offset={"uri": uri},
            )
        else:
            ok = self._call(self._sp.start_playback, device_id=device_id, uris=[uri])
        if ok:
            # Device re-registers after play — invalidate cache so next command does fresh lookup
            self._device_id = None
        return ok

    def play_playlist(self, context_uri: str) -> bool:
        """Start playing a playlist from the beginning."""
        device_id = self._get_device_id()
        print(f"[Spotify] play_playlist device_id={device_id[:8] + '…' if device_id else None}")
        if device_id is None:
            print("[Spotify] play_playlist: no device available — is Kitchen Display connected to Spotify?")
            return False
        ok = self._call(self._sp.start_playback, device_id=device_id, context_uri=context_uri)
        if ok:
            # Device re-registers after play — invalidate cache so next command does fresh lookup
            self._device_id = None
        return ok

    def get_current_track(self) -> Optional[dict]:
        """Poll Spotify API for currently playing track. Fallback when librespot onevent is silent."""
        if not self.available:
            return None
        try:
            with self._lock:
                playback = self._sp.current_playback()
            if not playback:
                return None
            item = playback.get("item")
            if not item:
                return None
            artists = item.get("artists", [])
            album = item.get("album", {})
            images = album.get("images", [])
            return {
                "name": item.get("name", ""),
                "id": item.get("id", ""),
                "uri": item.get("uri", ""),
                "artist": artists[0]["name"] if artists else "",
                "artist_id": artists[0]["id"] if artists else "",
                "album": album.get("name", ""),
                "duration_ms": item.get("duration_ms", 0),
                "position_ms": playback.get("progress_ms", 0),
                "is_playing": playback.get("is_playing", False),
                "image_url": images[0]["url"] if images else "",
                "context": playback.get("context"),
            }
        except Exception as e:
            print(f"[Spotify] get_current_track failed: {e}")
            return None

    def get_related_tracks(self, track_id: str, artist_id: str,
                           limit: int = 20, artist_name: str = "") -> List[dict]:
        """Get radio-like tracks related to a given track/artist.

        Tries three strategies in order:
          1. sp.recommendations()         — removed by Spotify Nov 2024, fails gracefully
          2. artist top-tracks endpoint   — works when account/market allows it
          3. search by artist name        — always available, reliable fallback
        """
        if not self.available:
            return []

        def _parse_tracks(items, max_count=limit):
            out = []
            for item in items[:max_count]:
                if not item:
                    continue
                artists = item.get("artists", [])
                album = item.get("album", {})
                images = album.get("images", [])
                out.append({
                    "uri": item["uri"],
                    "name": item["name"],
                    "id": item.get("id", ""),
                    "artist": artists[0]["name"] if artists else "",
                    "artist_id": artists[0]["id"] if artists else "",
                    "duration_ms": item.get("duration_ms", 0),
                    "image_url": images[0]["url"] if images else "",
                })
            return out

        # 1. Recommendations (removed by Spotify Nov 2024)
        try:
            with self._lock:
                results = self._sp.recommendations(seed_tracks=[track_id], limit=limit)
            tracks = _parse_tracks(results.get("tracks", []))
            if tracks:
                print(f"[Spotify] Recommendations: got {len(tracks)} tracks")
                return tracks
        except Exception as e:
            print(f"[Spotify] recommendations unavailable, trying artist top tracks…")

        # 2. Artist top tracks (spotipy sends ?country=US which may 403; try both market values)
        if artist_id:
            trid = self._sp._get_id("artist", artist_id)
            for market in ("from_token", "US"):
                try:
                    with self._lock:
                        results = self._sp._get(f"artists/{trid}/top-tracks", market=market)
                    tracks = _parse_tracks(results.get("tracks", []))
                    if tracks:
                        print(f"[Spotify] Artist top tracks (market={market}): got {len(tracks)} tracks")
                        return tracks
                except Exception:
                    pass
            print(f"[Spotify] artist_top_tracks failed for all markets, trying search…")

        # 3. Search by artist name — always available
        name = artist_name or ""
        if not name and artist_id:
            # Try to resolve artist name from the API (best effort)
            try:
                with self._lock:
                    info = self._sp.artist(artist_id)
                name = info.get("name", "")
            except Exception:
                pass
        if name:
            # Use _get() directly to avoid spotipy passing market=None as a query param
            # Spotify caps search limit at 10 in development/quota-restricted apps
            search_limit = min(limit, 10)
            for q in (f"artist:{name}", name):
                try:
                    with self._lock:
                        results = self._sp._get("search", q=q, type="track",
                                                limit=search_limit, offset=0)
                    items = results.get("tracks", {}).get("items", [])
                    tracks = _parse_tracks(items)
                    if tracks:
                        print(f"[Spotify] Search fallback (q={q!r}): got {len(tracks)} tracks")
                        return tracks
                except Exception as e:
                    print(f"[Spotify] search (q={q!r}) failed: {e}")

        print(f"[Spotify] get_related_tracks: all strategies exhausted, no tracks found")
        return []

    def start_radio(self, track_id: str, artist_id: str = "", artist_name: str = "") -> bool:
        """Queue related tracks as a radio for the given track/artist."""
        print(f"[Spotify] start_radio: seed={track_id[:8]}…  artist={artist_name or artist_id[:8] if artist_id else '?'}…")
        tracks = self.get_related_tracks(track_id, artist_id, artist_name=artist_name)
        if not tracks:
            print("[Spotify] start_radio: no tracks to queue")
            return False
        queued = 0
        for track in tracks:
            uri = track.get("uri", "")
            if uri and self.add_to_queue(uri):
                queued += 1
            else:
                break
        names = [t.get("name", "?") for t in tracks[:3]]
        print(f"[Spotify] start_radio: queued {queued}/{len(tracks)} tracks — {names}")
        return queued > 0

    def add_to_queue(self, uri: str) -> bool:
        """Add a track URI to the playback queue."""
        device_id = self._get_device_id()
        return self._call(self._sp.add_to_queue, uri, device_id=device_id)
