"""Image references shared by the web CLI and the ArtCraft media service."""

IMAGE_FILES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
IMAGE_SIGNATURES = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MUSE_MAX_FILE_BYTES = 50 * 1024 * 1024
MUSE_WEB_REFERENCE_LIMIT = 9
MUSE_MEDIA_FILES = {
    **IMAGE_FILES,
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
}
VIDEO_REFERENCE_LIMITS = {"flow": 3, "gemini": 1, "muse": 4, "grok": 1}
AUDIO_IMAGE_REFERENCE_LIMITS = {"muse": VIDEO_REFERENCE_LIMITS["muse"]}


def validate_muse_media_file(suffix, size):
    if suffix not in MUSE_MEDIA_FILES:
        raise ValueError("Muse files must be PNG, JPEG, WebP, MP4, MOV, WebM, MP3, WAV, M4A, or OGG")
    if size > MUSE_MAX_FILE_BYTES:
        raise ValueError("Muse file exceeds 50 MiB")


def validate_image_reference(mime, header, size):
    signature = IMAGE_SIGNATURES.get(mime)
    if signature is None:
        raise ValueError("Image references must be PNG, JPEG, or WebP")
    if size > MAX_IMAGE_BYTES:
        raise ValueError("Image reference exceeds 8 MiB")
    if not header.startswith(signature) or (mime == "image/webp" and header[8:12] != b"WEBP"):
        raise ValueError("Image reference does not match its declared format")
