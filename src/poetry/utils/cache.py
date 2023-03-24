from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import shutil
import time

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Generic
from typing import TypeVar

from poetry.utils.wheel import InvalidWheelName
from poetry.utils.wheel import Wheel


if TYPE_CHECKING:
    from poetry.core.packages.utils.link import Link

    from poetry.utils.env import Env


# Used by Cachy for items that do not expire.
MAX_DATE = 9999999999
T = TypeVar("T")


def decode(string: bytes, encodings: list[str] | None = None) -> str:
    """
    Compatiblity decode function pulled from cachy.

    :param string: The byte string to decode.
    :param encodings: List of encodings to apply
    :return: Decoded string
    """
    if encodings is None:
        encodings = ["utf-8", "latin1", "ascii"]

    for encoding in encodings:
        with contextlib.suppress(UnicodeDecodeError):
            return string.decode(encoding)

    return string.decode(encodings[0], errors="ignore")


def encode(string: str, encodings: list[str] | None = None) -> bytes:
    """
    Compatibility encode function from cachy.

    :param string: The string to encode.
    :param encodings: List of encodings to apply
    :return: Encoded byte string
    """
    if encodings is None:
        encodings = ["utf-8", "latin1", "ascii"]

    for encoding in encodings:
        with contextlib.suppress(UnicodeDecodeError):
            return string.encode(encoding)

    return string.encode(encodings[0], errors="ignore")


def _expiration(minutes: int) -> int:
    """
    Calculates the time in seconds since epoch that occurs 'minutes' from now.

    :param minutes: The number of minutes to count forward
    """
    return round(time.time()) + minutes * 60


_HASHES = {
    "md5": (hashlib.md5, 2),
    "sha1": (hashlib.sha1, 4),
    "sha256": (hashlib.sha256, 8),
}


@dataclasses.dataclass(frozen=True)
class CacheItem(Generic[T]):
    """
    Stores data and metadata for cache items.
    """

    data: T
    expires: int | None = None

    @property
    def expired(self) -> bool:
        """
        Return true if the cache item has exceeded its expiration period.
        """
        return self.expires is not None and time.time() >= self.expires


@dataclasses.dataclass(frozen=True)
class FileCache(Generic[T]):
    """
    Cachy-compatible minimal file cache. Stores subsequent data in a JSON format.

    :param path: The path that the cache starts at.
    :param hash_type: The hash to use for encoding keys/building directories.
    """

    path: Path
    hash_type: str = "sha256"

    def __post_init__(self) -> None:
        if self.hash_type not in _HASHES:
            raise ValueError(
                f"FileCache.hash_type is unknown value: '{self.hash_type}'."
            )

    def get(self, key: str) -> T | None:
        return self._get_payload(key)

    def has(self, key: str) -> bool:
        """
        Determine if a file exists and has not expired in the cache.
        :param key: The cache key
        :returns: True if the key exists in the cache
        """
        return self.get(key) is not None

    def put(self, key: str, value: Any, minutes: int | None = None) -> None:
        """
        Store an item in the cache.

        :param key: The cache key
        :param value: The cache value
        :param minutes: The lifetime in minutes of the cached value
        """
        payload: CacheItem[Any] = CacheItem(
            value, expires=_expiration(minutes) if minutes is not None else None
        )
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(self._serialize(payload))

    def forget(self, key: str) -> None:
        """
        Remove an item from the cache.

        :param key: The cache key
        """
        path = self._path(key)
        if path.exists():
            path.unlink()

    def flush(self) -> None:
        """
        Clear the cache.
        """
        shutil.rmtree(self.path)

    def remember(
        self, key: str, callback: T | Callable[[], T], minutes: int | None = None
    ) -> T:
        """
        Get an item from the cache, or use a default from callback.

        :param key: The cache key
        :param callback: Callback function providing default value
        :param minutes: The lifetime in minutes of the cached value
        """
        value = self.get(key)
        if value is None:
            value = callback() if callable(callback) else callback
            self.put(key, value, minutes)
        return value

    def _get_payload(self, key: str) -> T | None:
        path = self._path(key)

        if not path.exists():
            return None

        with open(path, "rb") as f:
            payload = self._deserialize(f.read())

        if payload.expired:
            self.forget(key)
            return None
        else:
            return payload.data

    def _path(self, key: str) -> Path:
        hash_type, parts_count = _HASHES[self.hash_type]
        h = hash_type(encode(key)).hexdigest()
        parts = [h[i : i + 2] for i in range(0, len(h), 2)][:parts_count]
        return Path(self.path, *parts, h)

    def _serialize(self, payload: CacheItem[T]) -> bytes:
        expires = payload.expires or MAX_DATE
        data = json.dumps(payload.data)
        return encode(f"{expires:010d}{data}")

    def _deserialize(self, data_raw: bytes) -> CacheItem[T]:
        data_str = decode(data_raw)
        data = json.loads(data_str[10:])
        expires = int(data_str[:10])
        return CacheItem(data, expires)


class ArtifactCache:
    def __init__(self, *, cache_dir: Path) -> None:
        self._cache_dir = cache_dir

    def get_cache_directory_for_link(self, link: Link) -> Path:
        key_parts = {"url": link.url_without_fragment}

        if link.hash_name is not None and link.hash is not None:
            key_parts[link.hash_name] = link.hash

        if link.subdirectory_fragment:
            key_parts["subdirectory"] = link.subdirectory_fragment

        key = hashlib.sha256(
            json.dumps(
                key_parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("ascii")
        ).hexdigest()

        split_key = [key[:2], key[2:4], key[4:6], key[6:]]

        return self._cache_dir.joinpath(*split_key)

    def get_cached_archive_for_link(
        self,
        link: Link,
        *,
        strict: bool,
        env: Env | None = None,
    ) -> Path | None:
        assert strict or env is not None

        archives = self._get_cached_archives_for_link(link)
        if not archives:
            return None

        candidates: list[tuple[float | None, Path]] = []
        for archive in archives:
            if strict:
                # in strict mode return the original cached archive instead of the
                # prioritized archive type.
                if link.filename == archive.name:
                    return archive
                continue

            assert env is not None

            if archive.suffix != ".whl":
                candidates.append((float("inf"), archive))
                continue

            try:
                wheel = Wheel(archive.name)
            except InvalidWheelName:
                continue

            if not wheel.is_supported_by_environment(env):
                continue

            candidates.append(
                (wheel.get_minimum_supported_index(env.supported_tags), archive),
            )

        if not candidates:
            return None

        return min(candidates)[1]

    def _get_cached_archives_for_link(self, link: Link) -> list[Path]:
        cache_dir = self.get_cache_directory_for_link(link)

        archive_types = ["whl", "tar.gz", "tar.bz2", "bz2", "zip"]
        paths = []
        for archive_type in archive_types:
            for archive in cache_dir.glob(f"*.{archive_type}"):
                paths.append(Path(archive))

        return paths
