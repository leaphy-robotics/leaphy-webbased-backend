"""Code for caching compiled C++ code"""

from hashlib import md5

from cachetools import TTLCache

from conf import settings

code_cache = TTLCache(
    maxsize=settings.max_code_caches, ttl=settings.code_cache_duration
)

library_cache = TTLCache(
    maxsize=settings.max_library_caches, ttl=settings.library_cache_duration
)


def get_code_cache_key(code: str):
    """Return a consistent md5 hash for c++ code"""
    code = code.replace(" ", "").replace("\n", "")
    return md5(code.encode()).hexdigest()
