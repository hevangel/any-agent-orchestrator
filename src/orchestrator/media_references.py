"""Image references shared by the web CLI and the ArtCraft media service."""

IMAGE_FILES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
IMAGE_SIGNATURES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
VIDEO_REFERENCE_LIMITS = {"flow": 3, "gemini": 1, "muse": 4, "grok": 1}
AUDIO_IMAGE_REFERENCE_LIMITS = {"muse": VIDEO_REFERENCE_LIMITS["muse"]}


def validate_image_reference(mime, header, size):
    signature = IMAGE_SIGNATURES.get(mime)
    if signature is None:
        raise ValueError("Image references must be PNG, JPEG, or WebP")
    if size > MAX_IMAGE_BYTES:
        raise ValueError("Image reference exceeds 8 MiB")
    if not header.startswith(signature) or (mime == "image/webp" and header[8:12] != b"WEBP"):
        raise ValueError("Image reference does not match its declared format")
