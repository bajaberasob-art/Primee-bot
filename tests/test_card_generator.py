import asyncio
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp import web
from PIL import Image

from cogs import card_generator as cards
from cogs import card_images
from level_progression import total_xp_for_level, xp_required


def image_bytes(color="#26cbab", size=(160, 160)):
    result = io.BytesIO()
    Image.new("RGB", size, color).save(result, "PNG")
    return result.getvalue()


def animated_gif_bytes(colors=("#ff0000", "#00ff00", "#0000ff")):
    result = io.BytesIO()
    frames = [Image.new("RGB", (120, 80), color) for color in colors]
    frames[0].save(
        result, "GIF", save_all=True, append_images=frames[1:],
        duration=[110, 140, 190], loop=0, disposal=2,
    )
    return result.getvalue()


class CardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user = SimpleNamespace(display_name="لونا • Lona 👑", name="lona", display_avatar=None)
        self.level = 16
        self.xp = total_xp_for_level(16) + 1530
        self.required = xp_required(16)
        with card_images._cache_lock:
            card_images._cache.clear()

    async def render(self, settings=None, **kwargs):
        return await cards.generate_rank_card(
            kwargs.get("user", self.user), kwargs.get("level", self.level),
            kwargs.get("xp", self.xp), kwargs.get("required", self.required),
            kwargs.get("rank", 5), kwargs.get("total", 1284), settings or {})

    def validate(self, data, layout="vertical"):
        self.assertIsInstance(data, io.BytesIO)
        self.assertEqual(data.tell(), 0)
        self.assertTrue(data.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(data) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, cards.LAYOUTS[layout])
            self.assertEqual(getattr(image, "n_frames", 1), 1)
            image.verify()

    async def test_all_eight_layouts_are_distinct_valid_pngs(self):
        results = []
        for layout in cards.LAYOUTS:
            data = await self.render({"card_layout": layout})
            self.validate(data, layout)
            results.append(data.getvalue())
        self.assertEqual(len(set(results)), 8)

    async def test_expanded_card_controls_change_the_static_rank_card(self):
        plain = await self.render()
        designed = await self.render({
            "card_design": {
                "glowStrength": 92, "particleDensity": 0, "particleColor": "#ff9900",
                "barStyle": "segmented", "frame": "diamond", "bgOverlay": 48,
                "bgBlur": 12, "animationEnabled": True, "animationStyle": "aurora",
                "animationIntensity": 90,
                "stats": {"messages": False, "voice": True, "streak": False, "serverRank": False},
            },
        })
        self.validate(designed)
        self.assertNotEqual(plain.getvalue(), designed.getvalue())

    async def test_level_up_gif_has_eighteen_moving_frames_and_static_rank_stays_png(self):
        settings = {
            "card_design": {
                **cards.CARD_DESIGN_DEFAULTS,
                "animationStyle": "beam",
                "animationIntensity": 70,
            },
        }
        gif = await cards.generate_level_up_gif(
            self.user, self.level, self.xp, self.required, 5, 1284, settings,
        )
        self.assertEqual(gif.tell(), 0)
        with Image.open(gif) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.n_frames, 18)
            # GIF stores frame timing in 10 ms units; 85 ms is encoded as 80 ms.
            self.assertEqual(image.info["duration"], 80)
            image.seek(0)
            first = image.convert("RGB").tobytes()
            image.seek(8)
            middle = image.convert("RGB").tobytes()
            self.assertNotEqual(first, middle)
        self.validate(await self.render())

    async def test_disabled_animation_returns_single_frame_gif_preview(self):
        settings = {
            "card_design": {
                **cards.CARD_DESIGN_DEFAULTS,
                "animationEnabled": False,
            },
        }
        gif = await cards.generate_level_up_gif(
            self.user, self.level, self.xp, self.required, 5, 1284, settings,
        )
        with Image.open(gif) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.n_frames, 1)

    async def test_animated_background_frames_survive_rank_card_rendering(self):
        background = animated_gif_bytes()

        async def fetch(url):
            return background if url else None

        settings = {
            "card_bg_url": "https://example.com/animated.gif",
            "card_design": {
                **cards.CARD_DESIGN_DEFAULTS,
                "animationEnabled": False,
                "animationIntensity": 0,
            },
        }
        with patch("cogs.card_generator.fetch_image", side_effect=fetch):
            gif = await cards.generate_level_up_gif(
                self.user, self.level, self.xp, self.required, 5, 1284, settings,
            )
        with Image.open(gif) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.n_frames, 3)
            self.assertEqual(image.info.get("loop"), 0)
            self.assertEqual(image.info.get("duration"), 110)
            image.seek(0)
            first = image.convert("RGB").tobytes()
            image.seek(1)
            second = image.convert("RGB").tobytes()
            self.assertNotEqual(first, second)

    async def test_animated_background_detection_is_independent_of_light_animation(self):
        settings = {
            "card_bg_url": "https://example.com/animated.gif",
            "card_design": {"animationEnabled": False, "animationIntensity": 0},
        }
        with patch(
            "cogs.card_generator.fetch_image",
            new=AsyncMock(return_value=animated_gif_bytes()),
        ):
            self.assertTrue(await cards.has_animated_background(settings))
            self.assertTrue(await cards.should_use_animated_card(settings))

    async def test_default_and_unknown_layout_are_vertical(self):
        default = await self.render()
        unknown = await self.render({"card_layout": "unknown"})
        self.assertEqual(default.getvalue(), unknown.getvalue())
        self.validate(default)

    async def test_all_particles_render_and_differ(self):
        results = []
        for mode in cards.PARTICLES:
            data = await self.render({"card_particles": mode})
            self.validate(data)
            results.append(data.getvalue())
        self.assertEqual(len(set(results)), len(cards.PARTICLES))

    async def test_avatar_download_and_circular_crop(self):
        self.user.display_avatar = SimpleNamespace(url="https://cdn.discordapp.com/avatar.png")
        async def fetch(url):
            return image_bytes("#26cbab") if url else None
        with patch("cogs.card_generator.fetch_image", side_effect=fetch):
            data = await self.render()
        with Image.open(data) as image:
            self.assertEqual(image.getpixel((280, 225)), (38, 203, 171))
            self.assertNotEqual(image.getpixel((166, 112)), (38, 203, 171))

    async def test_missing_and_failed_avatar_have_same_fallback(self):
        missing = await self.render()
        self.user.display_avatar = SimpleNamespace(url="https://example.com/missing")
        with patch("cogs.card_generator.fetch_image", new=AsyncMock(return_value=None)):
            failed = await self.render()
        self.assertEqual(missing.getvalue(), failed.getvalue())

    async def test_discord_asset_static_size_requested(self):
        from unittest.mock import Mock
        asset = Mock()
        asset.with_size.return_value = asset
        asset.with_static_format.return_value = asset
        asset.url = "https://cdn.discordapp.com/avatar.png"
        self.user.display_avatar = asset
        with patch("cogs.card_generator.fetch_image", new=AsyncMock(return_value=None)):
            await self.render()
        asset.with_size.assert_called_once_with(512)
        asset.with_static_format.assert_called_once_with("png")

    def test_progress_cumulative_xp_and_new_formula(self):
        earned, required, ratio = cards.calculate_progress(16, self.xp, self.required)
        self.assertEqual((earned, required), (1530, 2180))
        self.assertAlmostEqual(ratio, 1530 / 2180)
        self.assertEqual(cards.calculate_progress(0, 50, 100), (50, 100, .5))

    def test_progress_clamps_negative_excess_zero_and_invalid_values(self):
        for level, xp, cost, expected in [(0, -100, 100, 0), (0, 200, 100, 1),
                                          (0, 5, 0, 0), (3, 0, 100, 0),
                                          (0, float("nan"), None, 0)]:
            self.assertEqual(cards.calculate_progress(level, xp, cost)[2], expected)

    def test_rtl_progress_bar_fills_from_the_right_and_keeps_zero_empty(self):
        card = cards.Card((100, 50), "#26cbab", None, "none")
        card.bar((10, 10, 40, 8), 0.25, style="solid", direction="rtl")
        self.assertEqual(card.image.getpixel((49 * cards.SCALE, 14 * cards.SCALE)),
                         (38, 203, 171))
        self.assertNotEqual(card.image.getpixel((12 * cards.SCALE, 14 * cards.SCALE)),
                            (38, 203, 171))

        empty = cards.Card((100, 50), "#26cbab", None, "none")
        empty.bar((10, 10, 40, 8), 0.0, style="solid", direction="rtl")
        self.assertNotEqual(empty.image.getpixel((49 * cards.SCALE, 14 * cards.SCALE)),
                            (38, 203, 171))

    def test_streak_emblem_uses_scaled_coordinates(self):
        card = cards.Card(
            (1000, 300), "#42B9FF", None, "none", {"glowStrength": 0},
        )
        theme = cards._streak_card_theme({"stage_key": "spark"})
        cards._draw_streak_emblem(card, (908, 226), 34, None, "⚡", theme)

        # The emblem belongs in the lower-right panel, not the center track.
        self.assertEqual(
            card.image.getpixel((454 * cards.SCALE, 113 * cards.SCALE)),
            (7, 7, 11),
        )
        self.assertEqual(
            card.image.getpixel((908 * cards.SCALE, 209 * cards.SCALE)),
            (66, 185, 255),
        )

    async def test_level_zero_and_high_level_render(self):
        self.validate(await self.render(level=0, xp=0, required=100, rank=0, total=0))
        self.validate(await self.render(level=100000, xp=total_xp_for_level(100000)+100,
                                        required=xp_required(100000), rank=999999, total=1000000))

    async def test_arabic_shaping_and_unicode_names(self):
        shaped = cards.display_text("مرحبا بالعالم")
        self.assertNotEqual(shaped, "مرحبا بالعالم")
        self.assertTrue(any("\ufb50" <= c <= "\ufeff" for c in shaped))
        for name in ("مرحبا بالعالم", "Café • Ελληνικά • Привет 👑", "Lona 😀", "日本語 User"):
            self.user.display_name = name
            self.validate(await self.render({"card_layout": "minimal"}), "minimal")

    async def test_long_name_and_control_characters_are_safe(self):
        self.user.display_name = ("عربي 👑 ABC " * 100) + "\n\x00\u202e"
        self.validate(await self.render())
        self.assertNotIn("\x00", cards.display_text(self.user.display_name))

    async def test_missing_font_fallback(self):
        with patch("cogs.card_generator.FONT_DIR", Path("/nonexistent/fonts")):
            self.validate(await self.render())

    async def test_missing_optional_stats_and_present_stats(self):
        missing = await self.render({"card_layout": "stats"})
        present = await self.render({"card_layout": "stats", "total_messages": 29059,
                                     "total_voice_seconds": 5760, "current_streak": 11})
        self.assertNotEqual(missing.getvalue(), present.getvalue())
        self.validate(present, "stats")

    async def test_level_up_stats_render_in_every_card_layout(self):
        values = {
            "total_messages": 29059,
            "total_voice_seconds": 5760,
            "current_streak": 11,
        }
        for layout in cards.LAYOUTS:
            with_stats = await self.render({
                "card_layout": layout, "card_show_stats": True, **values,
            })
            hidden_stats = await self.render({
                "card_layout": layout, "card_show_stats": False, **values,
            })
            self.assertNotEqual(with_stats.getvalue(), hidden_stats.getvalue(), layout)
            self.validate(with_stats, layout)

    async def test_accent_colors_and_invalid_color_fallback(self):
        values = []
        for color in ("#f2aacb", "#6366f1", "#13ddb8", "not-a-color"):
            result = await self.render({"card_color": color})
            self.validate(result)
            values.append(result.getvalue())
        self.assertEqual(values[0], values[3])
        self.assertEqual(len(set(values)), 3)

    async def test_static_animated_bar_equivalent(self):
        plain = await self.render({"card_animated_bar": False})
        sheen = await self.render({"card_animated_bar": True})
        self.assertNotEqual(plain.getvalue(), sheen.getvalue())
        self.validate(sheen)

    async def test_no_files_written_by_generation(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                await self.render()
                self.assertEqual(os.listdir(directory), [])
            finally:
                os.chdir(cwd)

    async def test_render_runs_outside_event_loop_thread(self):
        import threading
        loop_thread = threading.get_ident()
        original = cards._render
        threads = []
        def capture(*args):
            threads.append(threading.get_ident())
            return original(*args)
        with patch("cogs.card_generator._render", side_effect=capture):
            await self.render()
        self.assertNotEqual(threads, [loop_thread])

    async def test_render_concurrency_is_bounded(self):
        import threading
        import time
        active, maximum = 0, 0
        lock = threading.Lock()
        def capture(*args):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(.02)
            with lock:
                active -= 1
            return io.BytesIO(b"test")
        with patch("cogs.card_generator._render", side_effect=capture):
            await asyncio.gather(*(self.render() for _ in range(8)))
        self.assertLessEqual(maximum, 3)

    def test_dependencies_and_bundled_fonts(self):
        import PIL
        self.assertTrue(PIL.__version__)
        self.assertTrue(aiohttp.__version__)
        for font in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "NotoEmoji.ttf"):
            self.assertTrue((cards.FONT_DIR / font).is_file())

    def test_invalid_image_and_pixel_limit_rejected(self):
        with self.assertRaises(Exception):
            card_images.normalize_image(b"not an image")
        with patch("cogs.card_images.MAX_PIXELS", 10):
            with self.assertRaises(ValueError):
                card_images.normalize_image(image_bytes())

    def test_bounded_animated_gif_is_retained_by_image_normalizer(self):
        source = animated_gif_bytes()
        normalized = card_images.normalize_image(source)
        self.assertEqual(normalized, source)
        with Image.open(io.BytesIO(normalized)) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.n_frames, 3)

    def test_private_urls_credentials_ports_and_schemes_rejected(self):
        for url in ("http://127.0.0.1/image", "http://[::1]/image", "http://10.0.0.1/img",
                    "http://169.254.169.254/latest/meta-data", "http://224.0.0.1/img",
                    "http://localhost/img", "ftp://example.com/img", "file:///etc/passwd",
                    "https://user:secret@example.com/image", "http://example.com:8080/image"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                card_images.validate_url(url)

    async def test_dns_private_answer_rejected(self):
        import socket
        loop = asyncio.get_running_loop()
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=records)):
            with self.assertRaises(ValueError):
                await card_images.PublicResolver().resolve("evil.example", 443)

    async def test_bad_urls_fall_back_without_network(self):
        baseline = await self.render()
        for url in ("not-a-url", "http://127.0.0.1/image", "file:///private", None):
            result = await self.render({"card_bg_url": url})
            self.assertEqual(result.getvalue(), baseline.getvalue())

    async def test_same_url_concurrent_requests_are_coalesced(self):
        async def download(url):
            await asyncio.sleep(.01)
            return image_bytes()
        with patch("cogs.card_images._download", side_effect=download) as fetch:
            results = await asyncio.gather(*(card_images.fetch_image("https://example.com/image") for _ in range(8)))
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(all(result == results[0] for result in results))

    async def test_failed_download_negative_cache_and_expiry(self):
        with patch("cogs.card_images._download", new=AsyncMock(side_effect=aiohttp.ClientError())) as fetch:
            with patch("cogs.card_images.time", SimpleNamespace(monotonic=lambda: 100)):
                self.assertIsNone(await card_images.fetch_image("https://example.com/fail"))
                self.assertIsNone(await card_images.fetch_image("https://example.com/fail"))
            with patch("cogs.card_images.time", SimpleNamespace(monotonic=lambda: 131)):
                self.assertIsNone(await card_images.fetch_image("https://example.com/fail"))
            self.assertEqual(fetch.call_count, 2)

    async def test_cache_limits(self):
        with patch("cogs.card_images.CACHE_ITEMS", 2):
            with patch("cogs.card_images._download", new=AsyncMock(return_value=image_bytes())):
                for index in range(5):
                    await card_images.fetch_image(f"https://example.com/{index}")
        self.assertEqual(len(card_images._cache), 2)


class HTTPBackgroundTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = 0
        self.data = image_bytes("#7733bb", (800, 500))
        async def background(request):
            self.calls += 1
            return web.Response(body=self.data, content_type="image/png")
        async def invalid(request):
            return web.Response(body=b"<html>not an image</html>")
        async def redirect(request):
            raise web.HTTPFound("http://127.0.0.1/private")
        app = web.Application()
        app.router.add_get("/bg", background)
        app.router.add_get("/invalid", invalid)
        app.router.add_get("/redirect", redirect)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        with card_images._cache_lock:
            card_images._cache.clear()

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_real_aiohttp_download_background_render_cache_and_ttl(self):
        user = SimpleNamespace(name="Lona", display_avatar=None)
        # Tests alone permit this local fixture; production validation is unchanged.
        with patch("cogs.card_images.validate_url", side_effect=lambda url: url):
            first = await cards.generate_rank_card(user, 0, 25, 100, 1, 50, {"card_bg_url": self.base+"/bg"})
            second = await cards.generate_rank_card(user, 0, 25, 100, 1, 50, {"card_bg_url": self.base+"/bg"})
        self.assertEqual(self.calls, 1)
        self.assertEqual(first.getvalue(), second.getvalue())
        plain = await cards.generate_rank_card(user, 0, 25, 100, 1, 50, {})
        self.assertNotEqual(first.getvalue(), plain.getvalue())
        with card_images._cache_lock:
            card_images._cache[self.base+"/bg"] = (0, self.data)
        with patch("cogs.card_images.validate_url", side_effect=lambda url: url):
            await card_images.fetch_image(self.base+"/bg")
        self.assertEqual(self.calls, 2)

    async def test_invalid_remote_image_and_oversized_body_fall_back(self):
        with patch("cogs.card_images.validate_url", side_effect=lambda url: url):
            self.assertIsNone(await card_images.fetch_image(self.base+"/invalid"))
            with patch("cogs.card_images.MAX_BYTES", 10):
                self.assertIsNone(await card_images.fetch_image(self.base+"/bg"))

    async def test_redirect_to_private_address_is_blocked(self):
        real_validate = card_images.validate_url
        def fixture_only(url):
            return url if url == self.base+"/redirect" else real_validate(url)
        with patch("cogs.card_images.validate_url", side_effect=fixture_only):
            self.assertIsNone(await card_images.fetch_image(self.base+"/redirect"))


if __name__ == "__main__":
    unittest.main()