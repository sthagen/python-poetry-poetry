import logging
import re

from importlib import import_module
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Optional
from typing import Type
from typing import cast

from cleo.application import Application as BaseApplication
from cleo.events.console_command_event import ConsoleCommandEvent
from cleo.events.console_events import COMMAND
from cleo.events.event_dispatcher import EventDispatcher
from cleo.exceptions import CleoException
from cleo.formatters.style import Style
from cleo.io.inputs.argv_input import ArgvInput
from cleo.io.inputs.input import Input
from cleo.io.io import IO
from cleo.io.outputs.output import Output

from poetry.__version__ import __version__

from .command_loader import CommandLoader
from .commands.command import Command


def load_command(name: str) -> Callable:
    def _load() -> Type[Command]:
        module = import_module(
            "poetry.console.commands.{}".format(".".join(name.split(" ")))
        )
        command_class = getattr(
            module, "{}Command".format("".join(c.title() for c in name.split(" ")))
        )

        return command_class()

    return _load


COMMANDS = [
    "about",
    "add",
    "build",
    "check",
    "config",
    "export",
    "init",
    "install",
    "lock",
    "new",
    "publish",
    "remove",
    "run",
    "search",
    "shell",
    "show",
    "update",
    "version",
    # Cache commands
    "cache clear",
    "cache list",
    # Debug commands
    "debug info",
    "debug resolve",
    # Env commands
    "env info",
    "env list",
    "env remove",
    "env use",
    # Plugin commands
    "plugin add",
    "plugin remove",
    "plugin show",
    # Self commands
    "self update",
    # Source commands
    "source add",
    "source remove",
    "source show",
]

if TYPE_CHECKING:
    from cleo.io.inputs.definition import Definition

    from poetry.console.commands.installer_command import InstallerCommand
    from poetry.poetry import Poetry


class Application(BaseApplication):
    def __init__(self) -> None:
        super().__init__("poetry", __version__)

        self._poetry = None
        self._io: Optional[IO] = None
        self._disable_plugins = False
        self._plugins_loaded = False

        dispatcher = EventDispatcher()
        dispatcher.add_listener(COMMAND, self.register_command_loggers)
        dispatcher.add_listener(COMMAND, self.configure_env)
        dispatcher.add_listener(COMMAND, self.configure_installer)
        self.set_event_dispatcher(dispatcher)

        command_loader = CommandLoader({name: load_command(name) for name in COMMANDS})
        self.set_command_loader(command_loader)

    @property
    def poetry(self) -> "Poetry":
        from pathlib import Path

        from poetry.factory import Factory

        if self._poetry is not None:
            return self._poetry

        self._poetry = Factory().create_poetry(
            Path.cwd(), io=self._io, disable_plugins=self._disable_plugins
        )

        return self._poetry

    @property
    def command_loader(self) -> CommandLoader:
        return self._command_loader

    def reset_poetry(self) -> None:
        self._poetry = None

    def create_io(
        self,
        input: Optional[Input] = None,
        output: Optional[Output] = None,
        error_output: Optional[Output] = None,
    ) -> IO:
        io = super().create_io(input, output, error_output)

        # Set our own CLI styles
        formatter = io.output.formatter
        formatter.set_style("c1", Style("cyan"))
        formatter.set_style("c2", Style("default", options=["bold"]))
        formatter.set_style("info", Style("blue"))
        formatter.set_style("comment", Style("green"))
        formatter.set_style("warning", Style("yellow"))
        formatter.set_style("debug", Style("default", options=["dark"]))
        formatter.set_style("success", Style("green"))

        # Dark variants
        formatter.set_style("c1_dark", Style("cyan", options=["dark"]))
        formatter.set_style("c2_dark", Style("default", options=["bold", "dark"]))
        formatter.set_style("success_dark", Style("green", options=["dark"]))

        io.output.set_formatter(formatter)
        io.error_output.set_formatter(formatter)

        self._io = io

        return io

    def _run(self, io: IO) -> int:
        self._disable_plugins = io.input.parameter_option("--no-plugins")

        self._load_plugins(io)

        return super()._run(io)

    def _configure_io(self, io: IO) -> None:
        # We need to check if the command being run
        # is the "run" command.
        definition = self.definition
        try:
            io.input.bind(definition)
        except CleoException:
            pass

        name = io.input.first_argument
        if name == "run":
            from .io.inputs.run_argv_input import RunArgvInput

            input = cast(ArgvInput, io.input)
            run_input = RunArgvInput([self._name or ""] + input._tokens)
            # For the run command reset the definition
            # with only the set options (i.e. the options given before the command)
            for option_name, value in input.options.items():
                if value:
                    option = definition.option(option_name)
                    run_input.add_parameter_option("--" + option.name)
                    if option.shortcut:
                        shortcuts = re.split(r"\|-?", option.shortcut.lstrip("-"))
                        shortcuts = [s for s in shortcuts if s]
                        for shortcut in shortcuts:
                            run_input.add_parameter_option("-" + shortcut.lstrip("-"))

            try:
                run_input.bind(definition)
            except CleoException:
                pass

            for option_name, value in input.options.items():
                if value:
                    run_input.set_option(option_name, value)

            io.set_input(run_input)

        return super()._configure_io(io)

    def register_command_loggers(
        self, event: ConsoleCommandEvent, event_name: str, _: Any
    ) -> None:
        from .logging.io_formatter import IOFormatter
        from .logging.io_handler import IOHandler

        command = event.command
        if not isinstance(command, Command):
            return

        io = event.io

        loggers = [
            "poetry.packages.locker",
            "poetry.packages.package",
            "poetry.utils.password_manager",
        ]

        loggers += command.loggers

        handler = IOHandler(io)
        handler.setFormatter(IOFormatter())

        for logger in loggers:
            logger = logging.getLogger(logger)

            logger.handlers = [handler]

            level = logging.WARNING
            # The builders loggers are special and we can actually
            # start at the INFO level.
            if logger.name.startswith("poetry.core.masonry.builders"):
                level = logging.INFO

            if io.is_debug():
                level = logging.DEBUG
            elif io.is_very_verbose() or io.is_verbose():
                level = logging.INFO

            logger.setLevel(level)

    def configure_env(
        self, event: ConsoleCommandEvent, event_name: str, _: Any
    ) -> None:
        from .commands.env_command import EnvCommand

        command: EnvCommand = cast(EnvCommand, event.command)
        if not isinstance(command, EnvCommand):
            return

        if command.env is not None:
            return

        from poetry.utils.env import EnvManager

        io = event.io
        poetry = command.poetry

        env_manager = EnvManager(poetry)
        env = env_manager.create_venv(io)

        if env.is_venv() and io.is_verbose():
            io.write_line(f"Using virtualenv: <comment>{env.path}</>")

        command.set_env(env)

    def configure_installer(
        self, event: ConsoleCommandEvent, event_name: str, _: Any
    ) -> None:
        from .commands.installer_command import InstallerCommand

        command: InstallerCommand = cast(InstallerCommand, event.command)
        if not isinstance(command, InstallerCommand):
            return

        # If the command already has an installer
        # we skip this step
        if command.installer is not None:
            return

        self._configure_installer(command, event.io)

    def _configure_installer(self, command: "InstallerCommand", io: "IO") -> None:
        from poetry.installation.installer import Installer

        poetry = command.poetry
        installer = Installer(
            io,
            command.env,
            poetry.package,
            poetry.locker,
            poetry.pool,
            poetry.config,
        )
        installer.use_executor(poetry.config.get("experimental.new-installer", False))
        command.set_installer(installer)

    def _load_plugins(self, io: IO) -> None:
        if self._plugins_loaded:
            return

        self._disable_plugins = io.input.has_parameter_option("--no-plugins")

        if not self._disable_plugins:
            from poetry.plugins.plugin_manager import PluginManager

            manager = PluginManager("application.plugin")
            manager.load_plugins()
            manager.activate(self)

        self._plugins_loaded = True

    @property
    def _default_definition(self) -> "Definition":
        from cleo.io.inputs.option import Option

        definition = super()._default_definition

        definition.add_option(
            Option("--no-plugins", flag=True, description="Disables plugins.")
        )

        return definition


def main() -> int:
    return Application().run()


if __name__ == "__main__":
    main()
