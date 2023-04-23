from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cleo.io.buffered_io import BufferedIO
from deepdiff import DeepDiff
from packaging.utils import canonicalize_name
from poetry.core.constraints.version import parse_constraint

from poetry.factory import Factory
from poetry.plugins.plugin import Plugin
from poetry.repositories.legacy_repository import LegacyRepository
from poetry.repositories.pypi_repository import PyPiRepository
from poetry.repositories.repository_pool import Priority
from poetry.toml.file import TOMLFile
from tests.helpers import mock_metadata_entry_points


if TYPE_CHECKING:
    from cleo.io.io import IO
    from pytest_mock import MockerFixture

    from poetry.poetry import Poetry
    from tests.types import FixtureDirGetter


class MyPlugin(Plugin):
    def activate(self, poetry: Poetry, io: IO) -> None:
        io.write_line("Setting readmes")
        poetry.package.readmes = ("README.md",)


def test_create_poetry(fixture_dir: FixtureDirGetter) -> None:
    poetry = Factory().create_poetry(fixture_dir("sample_project"))

    package = poetry.package

    assert package.name == "sample-project"
    assert package.version.text == "1.2.3"
    assert package.description == "Some description."
    assert package.authors == ["Sébastien Eustace <sebastien@eustace.io>"]
    assert package.license.id == "MIT"

    for readme in package.readmes:
        assert (
            readme.relative_to(fixture_dir("sample_project")).as_posix() == "README.rst"
        )

    assert package.homepage == "https://python-poetry.org"
    assert package.repository_url == "https://github.com/python-poetry/poetry"
    assert package.keywords == ["packaging", "dependency", "poetry"]

    assert package.python_versions == "~2.7 || ^3.6"
    assert str(package.python_constraint) == ">=2.7,<2.8 || >=3.6,<4.0"

    dependencies = {}
    for dep in package.requires:
        dependencies[dep.name] = dep

    cleo = dependencies["cleo"]
    assert cleo.pretty_constraint == "^0.6"
    assert not cleo.is_optional()

    pendulum = dependencies["pendulum"]
    assert pendulum.pretty_constraint == "branch 2.0"
    assert pendulum.is_vcs()
    assert pendulum.vcs == "git"
    assert pendulum.branch == "2.0"
    assert pendulum.source == "https://github.com/sdispater/pendulum.git"
    assert pendulum.allows_prereleases()

    requests = dependencies["requests"]
    assert requests.pretty_constraint == "^2.18"
    assert not requests.is_vcs()
    assert not requests.allows_prereleases()
    assert requests.is_optional()
    assert requests.extras == frozenset(["security"])

    pathlib2 = dependencies["pathlib2"]
    assert pathlib2.pretty_constraint == "^2.2"
    assert parse_constraint(pathlib2.python_versions) == parse_constraint("~2.7")
    assert not pathlib2.is_optional()

    demo = dependencies["demo"]
    assert demo.is_file()
    assert not demo.is_vcs()
    assert demo.name == "demo"
    assert demo.pretty_constraint == "*"

    demo = dependencies["my-package"]
    assert not demo.is_file()
    assert demo.is_directory()
    assert not demo.is_vcs()
    assert demo.name == "my-package"
    assert demo.pretty_constraint == "*"

    simple_project = dependencies["simple-project"]
    assert not simple_project.is_file()
    assert simple_project.is_directory()
    assert not simple_project.is_vcs()
    assert simple_project.name == "simple-project"
    assert simple_project.pretty_constraint == "*"

    functools32 = dependencies["functools32"]
    assert functools32.name == "functools32"
    assert functools32.pretty_constraint == "^3.2.3"
    assert (
        str(functools32.marker)
        == 'python_version ~= "2.7" and sys_platform == "win32" or python_version in'
        ' "3.4 3.5"'
    )

    assert "db" in package.extras

    classifiers = package.classifiers

    assert classifiers == [
        "Topic :: Software Development :: Build Tools",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ]

    assert package.all_classifiers == [
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Build Tools",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ]


@pytest.mark.parametrize(
    ("project",),
    [
        ("simple_project",),
        ("project_with_extras",),
    ],
)
def test_create_pyproject_from_package(
    project: str, fixture_dir: FixtureDirGetter
) -> None:
    poetry = Factory().create_poetry(fixture_dir(project))
    package = poetry.package

    pyproject = Factory.create_pyproject_from_package(package)

    result = pyproject["tool"]["poetry"]
    expected = poetry.pyproject.poetry_config

    # Extras are normalized as they are read.
    extras = expected.pop("extras", None)
    if extras is not None:
        normalized_extras = {
            canonicalize_name(extra): dependencies
            for extra, dependencies in extras.items()
        }
        expected["extras"] = normalized_extras

    # packages do not support this at present
    expected.pop("scripts", None)

    # remove any empty sections
    sections = list(expected.keys())
    for section in sections:
        if not expected[section]:
            expected.pop(section)

    assert not DeepDiff(expected, result)


def test_create_poetry_with_packages_and_includes(
    fixture_dir: FixtureDirGetter,
) -> None:
    poetry = Factory().create_poetry(fixture_dir("with-include"))

    package = poetry.package

    assert package.packages == [
        {"include": "extra_dir/**/*.py"},
        {"include": "extra_dir/**/*.py"},
        {"include": "my_module.py"},
        {"include": "package_with_include"},
        {"include": "tests", "format": "sdist"},
        {"include": "for_wheel_only", "format": ["wheel"]},
        {"include": "src_package", "from": "src"},
    ]

    assert package.include == [
        {"path": "extra_dir/vcs_excluded.txt", "format": []},
        {"path": "notes.txt", "format": []},
    ]


def test_create_poetry_with_multi_constraints_dependency(
    fixture_dir: FixtureDirGetter,
) -> None:
    poetry = Factory().create_poetry(
        fixture_dir("project_with_multi_constraints_dependency")
    )

    package = poetry.package

    assert len(package.requires) == 2


def test_poetry_with_default_source_legacy(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    io = BufferedIO()
    poetry = Factory().create_poetry(fixture_dir("with_default_source_legacy"), io=io)

    assert len(poetry.pool.repositories) == 1
    assert "Found deprecated key" in io.fetch_error()


def test_poetry_with_default_source(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    io = BufferedIO()
    poetry = Factory().create_poetry(fixture_dir("with_default_source"), io=io)

    assert len(poetry.pool.repositories) == 1
    assert io.fetch_error() == ""


@pytest.mark.parametrize(
    "project",
    ("with_non_default_source_implicit", "with_non_default_source_explicit"),
)
def test_poetry_with_non_default_source(
    project: str, fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    poetry = Factory().create_poetry(fixture_dir(project))

    assert not poetry.pool.has_default()
    assert poetry.pool.has_repository("PyPI")
    assert poetry.pool.get_priority("PyPI") is Priority.SECONDARY
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert poetry.pool.has_repository("foo")
    assert poetry.pool.get_priority("foo") is Priority.PRIMARY
    assert isinstance(poetry.pool.repository("foo"), LegacyRepository)
    assert {repo.name for repo in poetry.pool.repositories} == {"PyPI", "foo"}


def test_poetry_with_non_default_secondary_source_legacy(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    poetry = Factory().create_poetry(
        fixture_dir("with_non_default_secondary_source_legacy")
    )

    assert poetry.pool.has_repository("PyPI")
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert poetry.pool.get_priority("PyPI") is Priority.DEFAULT
    assert poetry.pool.has_repository("foo")
    assert isinstance(poetry.pool.repository("foo"), LegacyRepository)
    assert [repo.name for repo in poetry.pool.repositories] == ["PyPI", "foo"]


def test_poetry_with_non_default_secondary_source(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    poetry = Factory().create_poetry(fixture_dir("with_non_default_secondary_source"))

    assert poetry.pool.has_repository("PyPI")
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert poetry.pool.get_priority("PyPI") is Priority.DEFAULT
    assert poetry.pool.has_repository("foo")
    assert isinstance(poetry.pool.repository("foo"), LegacyRepository)
    assert [repo.name for repo in poetry.pool.repositories] == ["PyPI", "foo"]


def test_poetry_with_non_default_multiple_secondary_sources_legacy(
    fixture_dir: FixtureDirGetter,
    with_simple_keyring: None,
) -> None:
    poetry = Factory().create_poetry(
        fixture_dir("with_non_default_multiple_secondary_sources_legacy")
    )

    assert poetry.pool.has_repository("PyPI")
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert poetry.pool.get_priority("PyPI") is Priority.DEFAULT
    assert poetry.pool.has_repository("foo")
    assert isinstance(poetry.pool.repository("foo"), LegacyRepository)
    assert poetry.pool.has_repository("bar")
    assert isinstance(poetry.pool.repository("bar"), LegacyRepository)
    assert {repo.name for repo in poetry.pool.repositories} == {"PyPI", "foo", "bar"}


def test_poetry_with_non_default_multiple_secondary_sources(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    poetry = Factory().create_poetry(
        fixture_dir("with_non_default_multiple_secondary_sources")
    )

    assert poetry.pool.has_repository("PyPI")
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert poetry.pool.get_priority("PyPI") is Priority.DEFAULT
    assert poetry.pool.has_repository("foo")
    assert isinstance(poetry.pool.repository("foo"), LegacyRepository)
    assert poetry.pool.has_repository("bar")
    assert isinstance(poetry.pool.repository("bar"), LegacyRepository)
    assert {repo.name for repo in poetry.pool.repositories} == {"PyPI", "foo", "bar"}


def test_poetry_with_non_default_multiple_sources_legacy(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    poetry = Factory().create_poetry(
        fixture_dir("with_non_default_multiple_sources_legacy")
    )

    assert not poetry.pool.has_default()
    assert poetry.pool.has_repository("bar")
    assert isinstance(poetry.pool.repository("bar"), LegacyRepository)
    assert poetry.pool.has_repository("PyPI")
    assert poetry.pool.get_priority("PyPI") is Priority.SECONDARY
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert poetry.pool.has_repository("foo")
    assert isinstance(poetry.pool.repository("foo"), LegacyRepository)
    assert {repo.name for repo in poetry.pool.repositories} == {"bar", "PyPI", "foo"}


def test_poetry_with_non_default_multiple_sources(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    poetry = Factory().create_poetry(fixture_dir("with_non_default_multiple_sources"))

    assert not poetry.pool.has_default()
    assert poetry.pool.has_repository("PyPI")
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert poetry.pool.get_priority("PyPI") is Priority.SECONDARY
    assert poetry.pool.has_repository("bar")
    assert isinstance(poetry.pool.repository("bar"), LegacyRepository)
    assert poetry.pool.has_repository("foo")
    assert isinstance(poetry.pool.repository("foo"), LegacyRepository)
    assert {repo.name for repo in poetry.pool.repositories} == {"PyPI", "bar", "foo"}


def test_poetry_with_no_default_source(fixture_dir: FixtureDirGetter) -> None:
    poetry = Factory().create_poetry(fixture_dir("sample_project"))

    assert poetry.pool.has_repository("PyPI")
    assert poetry.pool.get_priority("PyPI") is Priority.DEFAULT
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert {repo.name for repo in poetry.pool.repositories} == {"PyPI"}


def test_poetry_with_explicit_source(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    poetry = Factory().create_poetry(fixture_dir("with_explicit_source"))

    assert len(poetry.pool.repositories) == 1
    assert len(poetry.pool.all_repositories) == 2
    assert poetry.pool.has_repository("PyPI")
    assert poetry.pool.get_priority("PyPI") is Priority.DEFAULT
    assert isinstance(poetry.pool.repository("PyPI"), PyPiRepository)
    assert poetry.pool.has_repository("explicit")
    assert isinstance(poetry.pool.repository("explicit"), LegacyRepository)
    assert [repo.name for repo in poetry.pool.repositories] == ["PyPI"]


def test_poetry_with_two_default_sources_legacy(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    with pytest.raises(ValueError) as e:
        Factory().create_poetry(fixture_dir("with_two_default_sources_legacy"))

    assert str(e.value) == "Only one repository can be the default."


def test_poetry_with_two_default_sources(
    fixture_dir: FixtureDirGetter, with_simple_keyring: None
) -> None:
    with pytest.raises(ValueError) as e:
        Factory().create_poetry(fixture_dir("with_two_default_sources"))

    assert str(e.value) == "Only one repository can be the default."


def test_validate(fixture_dir: FixtureDirGetter) -> None:
    complete = TOMLFile(fixture_dir("complete.toml"))
    content = complete.read()["tool"]["poetry"]

    assert Factory.validate(content) == {"errors": [], "warnings": []}


def test_validate_fails(fixture_dir: FixtureDirGetter) -> None:
    complete = TOMLFile(fixture_dir("complete.toml"))
    content = complete.read()["tool"]["poetry"]
    content["this key is not in the schema"] = ""

    expected = (
        "Additional properties are not allowed "
        "('this key is not in the schema' was unexpected)"
    )

    assert Factory.validate(content) == {"errors": [expected], "warnings": []}


def test_create_poetry_fails_on_invalid_configuration(
    fixture_dir: FixtureDirGetter,
) -> None:
    with pytest.raises(RuntimeError) as e:
        Factory().create_poetry(fixture_dir("invalid_pyproject") / "pyproject.toml")

    expected = """\
The Poetry configuration is invalid:
  - 'description' is a required property
  - Project name (invalid) is same as one of its dependencies
"""
    assert str(e.value) == expected


def test_create_poetry_with_local_config(fixture_dir: FixtureDirGetter) -> None:
    poetry = Factory().create_poetry(fixture_dir("with_local_config"))

    assert not poetry.config.get("virtualenvs.in-project")
    assert not poetry.config.get("virtualenvs.create")
    assert not poetry.config.get("virtualenvs.options.always-copy")
    assert not poetry.config.get("virtualenvs.options.no-pip")
    assert not poetry.config.get("virtualenvs.options.no-setuptools")
    assert not poetry.config.get("virtualenvs.options.system-site-packages")


def test_create_poetry_with_plugins(
    mocker: MockerFixture, fixture_dir: FixtureDirGetter
) -> None:
    mock_metadata_entry_points(mocker, MyPlugin)

    poetry = Factory().create_poetry(fixture_dir("sample_project"))

    assert poetry.package.readmes == ("README.md",)
