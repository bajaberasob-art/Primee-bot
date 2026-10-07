"""Bounded, memory-only image downloads for rank cards (including SSRF guards)."""
import asyncio
import io
import ipaddress
import logging
import socket
import threading
import time
import warnings
from collections import OrderedDict
from urllib.parse import urljoin, urlsplit

import aiohttp
from PIL import Image, ImageOps

logger = logging.getLogger("RankCard")
MAX_BYTES = 6 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_ANIMATED_FRAMES = 30
MAX_ANIMATED_PIXELS = 24_000_000
CACHE_BYTES = 24 * 1024 * 1024
CACHE_ITEMS = 64
CACHE_TTL = 3600
_cache = OrderedDict()
_cache_lock = threading.Lock()
_pending = {}


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("image URL must be HTTP(S)")
    if parts.username or parts.password or parts.port not in {None, 80, 443}:
        raise ValueError("unsupported image URL authority")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        if parts.hostname.lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError("private image host")
    else:
        if not address.is_global or address.is_multicast:
            raise ValueError("private image address")
    return url


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate DNS answers used by the connector, avoiding DNS-rebinding races."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM)
        answers = []
        for af, _, proto, _, address in records:
            ip = address[0]
            address = ipaddress.ip_address(ip)
            if not address.is_global or address.is_multicast:
                raise ValueError("image DNS resolved to a private address")
            answers.append({"hostname": host, "host": ip, "port": port,
                            "family": af, "proto": proto, "flags": socket.AI_NUMERICHOST})
        return answers

    async def close(self):
        pass


def normalize_image(data):
    """Reject oversized images and preserve small, bounded animated GIFs."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("image byte limit")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise ValueError("unsupported image format")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("image pixel limit")
            frame_count = getattr(source, "n_frames", 1)
            if (
                source.format == "GIF"
                and 1 < frame_count <= MAX_ANIMATED_FRAMES
                and source.width * source.height * frame_count <= MAX_ANIMATED_PIXELS
            ):
                # Decode each frame before retaining the compressed bytes. The
                # frame and aggregate-pixel limits bound work in the card worker.
                for index in range(frame_count):
                    source.seek(index)
                    source.convert("RGB").load()
                return bytes(data)
            source.seek(0)
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            result = io.BytesIO()
            image.save(result, format="PNG")
            return result.getvalue()


async def _download(url):
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=4)
    timeout = aiohttp.ClientTimeout(total=8, connect=3)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
        for _ in range(4):
            validate_url(url)
            async with session.get(url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()
                if response.content_length and response.content_length > MAX_BYTES:
                    raise ValueError("image byte limit")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("image byte limit")
                return await asyncio.to_thread(normalize_image, bytes(data))
    raise ValueError("too many image redirects")


async def _load(url):
    try:
        validate_url(url)
        data = await _download(url)
        ttl = CACHE_TTL
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        # Do not log arbitrary URLs (which can contain signed query strings).
        logger.debug("Rank-card image unavailable; using generated fallback", exc_info=False)
        data, ttl = None, 30
    with _cache_lock:
        _cache[url] = (time.monotonic() + ttl, data)
        _cache.move_to_end(url)
        while len(_cache) > CACHE_ITEMS or sum(len(item[1] or b"") for item in _cache.values()) > CACHE_BYTES:
            _cache.popitem(last=False)
    return data


async def fetch_image(url):
    if not isinstance(url, str) or not url or len(url) > 4096:
        return None
    with _cache_lock:
        cached = _cache.get(url)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(url)
            return cached[1]
        _cache.pop(url, None)
    key = (asyncio.get_running_loop(), url)
    if key not in _pending:
        task = asyncio.create_task(_load(url))
        _pending[key] = task
        task.add_done_callback(lambda completed: _pending.pop(key, None))
    return await asyncio.shield(_pending[key])