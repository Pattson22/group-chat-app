# content_type -> message "type" category, used both to validate uploads
# and to decide what type a message gets when it references this media.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": "image",
    "image/png": "image",
    "image/gif": "image",
    "image/webp": "image",
    "application/pdf": "file",
    "text/plain": "file",
}
