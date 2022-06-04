from __future__ import annotations

from typing import cast

from cleo.io.inputs.string_input import StringInput
from cleo.io.io import IO

from poetry.console.application import Application
from poetry.console.commands.command import Command
from poetry.console.commands.self.show.plugins import SelfShowPluginsCommand


class PluginShowCommand(Command):

    name = "plugin show"

    description = "Shows information about the currently installed plugins."
    help = (
        "<warning>This command is deprecated. Use <c2>self show plugins</> "
        "command instead.</warning>"
    )
    hidden = True

    def handle(self) -> int:
        self.line_error(self.help)

        application = cast(Application, self.application)
        command: SelfShowPluginsCommand = cast(
            SelfShowPluginsCommand, application.find("self show plugins")
        )

        exit_code: int = command.run(
            IO(
                StringInput(""),
                self.io.output,
                self.io.error_output,
            )
        )
        return exit_code
