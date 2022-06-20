from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Iterator
from typing import Mapping

from poetry.utils.constants import REQUESTS_TIMEOUT


if TYPE_CHECKING:
    from collections.abc import Callable

    from poetry.core.packages.package import Package
    from requests import Session

    from poetry.utils.authenticator import Authenticator


_canonicalize_regex = re.compile("[-_]+")


def canonicalize_name(name: str) -> str:
    return _canonicalize_regex.sub("-", name).lower()


def module_name(name: str) -> str:
    return canonicalize_name(name).replace(".", "_").replace("-", "_")


@contextmanager
def directory(path: Path) -> Iterator[Path]:
    cwd = Path.cwd()
    try:
        os.chdir(path)
        yield path
    finally:
        os.chdir(cwd)


def _on_rm_error(func: Callable[[str], None], path: str, exc_info: Exception) -> None:
    if not os.path.exists(path):
        return

    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_directory(
    path: Path | str, *args: Any, force: bool = False, **kwargs: Any
) -> None:
    """
    Helper function handle safe removal, and optionally forces stubborn file removal.
    This is particularly useful when dist files are read-only or git writes read-only
    files on Windows.

    Internally, all arguments are passed to `shutil.rmtree`.
    """
    if Path(path).is_symlink():
        return os.unlink(str(path))

    kwargs["onerror"] = kwargs.pop("onerror", _on_rm_error if force else None)
    shutil.rmtree(path, *args, **kwargs)


def merge_dicts(d1: dict[str, Any], d2: dict[str, Any]) -> None:
    for k in d2.keys():
        if k in d1 and isinstance(d1[k], dict) and isinstance(d2[k], Mapping):
            merge_dicts(d1[k], d2[k])
        else:
            d1[k] = d2[k]


def download_file(
    url: str,
    dest: str,
    session: Authenticator | Session | None = None,
    chunk_size: int = 1024,
) -> None:
    import requests

    from poetry.puzzle.provider import Indicator

    get = requests.get if not session else session.get

    response = get(url, stream=True, timeout=REQUESTS_TIMEOUT)
    response.raise_for_status()

    set_indicator = False
    with Indicator.context() as update_context:
        update_context(f"Downloading {url}")

        if "Content-Length" in response.headers:
            try:
                total_size = int(response.headers["Content-Length"])
            except ValueError:
                total_size = 0

            fetched_size = 0
            last_percent = 0

            # if less than 1MB, we simply show that we're downloading
            # but skip the updating
            set_indicator = total_size > 1024 * 1024

        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)

                    if set_indicator:
                        fetched_size += len(chunk)
                        percent = (fetched_size * 100) // total_size
                        if percent > last_percent:
                            last_percent = percent
                            update_context(f"Downloading {url} {percent:3}%")


def get_package_version_display_string(
    package: Package, root: Path | None = None
) -> str:
    if package.source_type in ["file", "directory"] and root:
        assert package.source_url is not None
        path = Path(os.path.relpath(package.source_url, root.as_posix())).as_posix()
        return f"{package.version} {path}"

    pretty_version: str = package.full_pretty_version
    return pretty_version


def paths_csv(paths: list[Path]) -> str:
    return ", ".join(f'"{c!s}"' for c in paths)


def is_dir_writable(path: Path, create: bool = False) -> bool:
    try:
        if not path.exists():
            if not create:
                return False
            path.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryFile(dir=str(path)):
            pass
    except OSError:
        return False
    else:
        return True


def pluralize(count: int, word: str = "") -> str:
    if count == 1:
        return word
    return word + "s"


def safe_extra(extra: str) -> str:
    """Convert an arbitrary string to a standard 'extra' name.

    Any runs of non-alphanumeric characters are replaced with a single '_',
    and the result is always lowercased.

    See
    https://github.com/pypa/setuptools/blob/452e13c/pkg_resources/__init__.py#L1423-L1431.
    """
    return re.sub("[^A-Za-z0-9.-]+", "_", extra).lower()
