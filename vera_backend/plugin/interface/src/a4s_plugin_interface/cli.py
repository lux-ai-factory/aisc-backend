import argparse
import tomllib
from pathlib import Path
from importlib.resources import files

from rich.console import Console
from rich.prompt import Prompt

console = Console()


def _prompt(text, default):
    return Prompt.ask(f"[bold cyan]{text}[/]", console=console, default=default)


def _get_package_name(pyproject: Path):
    data = tomllib.loads(pyproject.read_text())
    return data["project"]["name"].replace("-", "_")


def _render(template_name: str, context: dict) -> str:
    template_file = files("a4s_plugin_interface").joinpath("templates", template_name)
    template = template_file.read_text(encoding="utf-8")
    for key, value in context.items():
        template = template.replace(f"{{{{ {key} }}}}", value)
    return template


def _init_or_update_init_file(src_pkg, context) -> bool:
    init_file = src_pkg / "__init__.py"
    init_content = _render("init.py.tpl", context)

    if init_file.exists():
        existing = init_file.read_text(encoding="utf-8")

        # check if import line already exists
        import_line = f"from .{context['import_path']} import {context['plugin_name']}"
        if import_line not in existing:
            # append the new import
            existing = f"{import_line}\n" + existing.rstrip()
        else:
            console.print(
                f"[red]❌ Import already in {str(init_file)}: '{import_line}'[/]"
            )
            return False

        # update __all__
        if "__all__" in existing:
            # find current __all__ list and append the class
            import re

            pattern = r"__all__\s*=\s*\[([^\]]*)\]"
            match = re.search(pattern, existing)
            if match:
                classes = match.group(1).split(",")
                classes = [c.strip(" '\"") for c in classes]
                if context["plugin_name"] not in classes:
                    classes.append(context["plugin_name"])
                else:
                    console.print(
                        f"[red]❌ A plugin with the same name '{context['plugin_name']}' "
                        "already exists. Abording.[/]"
                    )
                    return False

                new_all = ", ".join(f'"{c}"' for c in classes)
                existing = re.sub(pattern, f"__all__ = [{new_all}]", existing)
        else:
            # __all__ does not exist, create it
            existing += f"\n__all__ = ['{context['plugin_name']}']\n"

        init_file.write_text(existing, encoding="utf-8")
        console.print(f"[green]✅ Updated __init__.py with {context['plugin_name']}[/]")
    else:
        # __init__.py does not exist, create it from template
        init_file.write_text(init_content, encoding="utf-8")
        console.print(f"[green]✅ Created __init__.py with {context['plugin_name']}[/]")

    return True


def init_plugin(force=False):
    root = Path.cwd()
    pyproject = root / "pyproject.toml"

    if not pyproject.exists():
        console.print("[red]❌ No pyproject.toml found. Run inside a project.[/]")
        return

    package_name = _get_package_name(pyproject)
    src_pkg = root / "src" / package_name

    plugin_name = _prompt("Plugin class name", "Plugin")
    plugin_path_input = _prompt(
        "Plugin path (path/to/file, .py added automatically if omitted)", "plugin"
    )

    # remove .py if user included it
    if plugin_path_input.endswith(".py"):
        plugin_path_input = plugin_path_input[:-3]

    parts = [p for p in plugin_path_input.strip("/").split("/") if p]

    plugin_dir = src_pkg.joinpath(*parts[:-1]) if len(parts) > 1 else src_pkg
    plugin_dir.mkdir(parents=True, exist_ok=True)

    plugin_file = plugin_dir / f"{parts[-1]}.py"
    import_path = ".".join(parts)

    context = {
        "import_path": import_path,
        "package_name": package_name,
        "plugin_name": plugin_name,
    }

    if not plugin_file.exists() or force:
        is_valid_name_and_dest = _init_or_update_init_file(src_pkg, context)
        if is_valid_name_and_dest:
            plugin_file.write_text(_render("plugin.py.tpl", context))
            console.print(
                f"[green]✅ Plugin class '{plugin_name}' template written to file {plugin_file}[/]"
            )

            console.print(
                f"[bold magenta]🎉 Plugin '{plugin_name}' has been successfully initialized![/]"
            )
    else:
        console.print(f"[red]❌ File already exists: '{plugin_file}'.[/]")


def main():
    parser = argparse.ArgumentParser(prog="parent-package")
    sub = parser.add_subparsers(dest="command")

    init_cmd = sub.add_parser("init-plugin")
    init_cmd.add_argument("--force", action="store_true")

    args = parser.parse_args()

    if args.command == "init-plugin":
        init_plugin(force=args.force)
    else:
        parser.print_help()
