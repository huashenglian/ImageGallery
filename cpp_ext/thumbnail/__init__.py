from __future__ import annotations

try:
    from cpp_ext.thumbnail._thumbnail import thumb_generate_batch, ThumbResult
except ImportError:
    thumb_generate_batch = None
    ThumbResult = None
