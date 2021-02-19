from typing import TYPE_CHECKING
from typing import List
from typing import Union

from .dependency_package import DependencyPackage


if TYPE_CHECKING:
    from poetry.core.packages import Dependency
    from poetry.core.packages import Package


class PackageCollection(list):
    def __init__(
        self,
        dependency: "Dependency",
        packages: List[Union["Package", DependencyPackage]] = None,
    ) -> None:
        self._dependency = dependency

        if packages is None:
            packages = []

        super(PackageCollection, self).__init__()

        for package in packages:
            self.append(package)

    def append(self, package: Union["Package", DependencyPackage]) -> None:
        if isinstance(package, DependencyPackage):
            package = package.package

        package = DependencyPackage(self._dependency, package)

        return super(PackageCollection, self).append(package)
