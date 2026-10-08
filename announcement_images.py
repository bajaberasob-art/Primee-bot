"""Small validated separator images kept in the existing SQLite database."""
import asyncio
import hashlib
import io
import warnings

import aiosqlite
from PIL import Image, UnidentifiedImageError

import database


MAX_IMAGE_BYTES = 2 * 1024 * 1024
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}


def validate_image(payload):
    if not payload or len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("اختر صورة بحجم لا يتجاوز 2 ميجابايت.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format not in FORMATS:
                    raise ValueError("الصيغ المقبولة PNG أو JPG أو GIF فقط.")
                width, height = image.size
                if not 1 <= width <= 4096 or not 1 <= height <= 4096:
                    raise ValueError("أبعاد الصورة يجب ألا تتجاوز 4096 × 4096.")
                mime = FORMATS[image.format]
                image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning,
            Image.DecompressionBombError) as error:
        raise ValueError("الملف ليس صورة سليمة من الصيغ المقبولة.") from error
    return {
        "id": hashlib.sha256(payload).hexdigest(), "mime": mime,
        "width": width, "height": height, "size": len(payload),
    }


async def upload(guild_id, payload):
    metadata = await asyncio.to_thread(validate_image, payload)
    async with database.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO announcement_line_images
               (guild_id,image_id,mime,width,height,payload,created_at)
               VALUES (?,?,?,?,?,?,strftime('%s','now'))
               ON CONFLICT(guild_id,image_id) DO UPDATE SET created_at=excluded.created_at""",
            (guild_id, metadata["id"], metadata["mime"], metadata["width"], metadata["height"], payload),
        )
        # Bound abandoned drafts, but never remove the currently configured image.
        await db.execute(
            """DELETE FROM announcement_line_images WHERE guild_id=?
               AND image_id NOT IN (
                 SELECT image_id FROM announcement_line_images WHERE guild_id=?
                 ORDER BY created_at DESC,rowid DESC LIMIT 3
               ) AND image_id != COALESCE((
                 SELECT line_image_id FROM announcement_reaction_settings WHERE guild_id=?
               ),'')""",
            (guild_id, guild_id, guild_id),
        )
        await db.commit()
    return public_metadata(guild_id, metadata)


def public_metadata(guild_id, metadata):
    return {**metadata, "url": f"api/guild/{guild_id}/announcement-reactions/image/{metadata['id']}"}


async def get_image(guild_id, image_id, include_payload=False):
    if not isinstance(image_id, str) or len(image_id) != 64 or any(c not in "0123456789abcdef" for c in image_id):
        return None
    columns = "image_id AS id,mime,width,height,length(payload) AS size"
    if include_payload:
        columns += ",payload"
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            f"SELECT {columns} FROM announcement_line_images WHERE guild_id=? AND image_id=?",
            (guild_id, image_id),
        ) as cursor:
            row = await cursor.fetchone()
    return public_metadata(guild_id, dict(row)) if row else None
