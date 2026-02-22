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
            # Prefer raspotify by name
            for device in device_list:
                if RASPOTIFY_DEVICE_NAME in device.get("name", ""):
                    self._device_id = device["id"]
                    self._device_cache_time = now
                    return self._device_id
            # Fall back to any active device
            for device in device_list:
                if device.get("is_active"):
                    self._device_id = device["id"]
                    self._device_cache_time = now
                    return self._device_id
            # Fall back to first device
            if device_list:
                self._device_id = device_list[0]["id"]
                self._device_cache_time = now
                return self._device_id
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
        device_id = self._get_device_id()
        return self._call(self._sp.next_track, device_id=device_id)

    def previous_track(self) -> bool:
        device_id = self._get_device_id()
        return self._call(self._sp.previous_track, device_id=device_id)

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
                    "duration_ms": item.get("duration_ms", 0),
                    "image_url": images[0]["url"] if images else "",
                })
            return tracks
        except Exception as e:
            print(f"[Spotify] get_queue failed: {e}")
            return []

    def play_track(self, uri: str, context_uri: str = None) -> bool:
        """Play a specific track, optionally within a playlist context."""
        device_id = self._get_device_id()
        if context_uri:
            return self._call(
                self._sp.start_playback,
                device_id=device_id,
                context_uri=context_uri,
                offset={"uri": uri},
            )
        return self._call(self._sp.start_playback, device_id=device_id, uris=[uri])

    def play_playlist(self, context_uri: str) -> bool:
        """Start playing a playlist from the beginning."""
        device_id = self._get_device_id()
        return self._call(self._sp.start_playback, device_id=device_id, context_uri=context_uri)

    def add_to_queue(self, uri: str) -> bool:
        """Add a track URI to the playback queue."""
        device_id = self._get_device_id()
        return self._call(self._sp.add_to_queue, uri, device_id=device_id)
