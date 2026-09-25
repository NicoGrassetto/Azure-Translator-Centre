"""Fail when notebook.ipynb doesn't demonstrate the installed SDK's full client surface.

Every public entry point of TextTranslationClient (the constructor and each public method)
must have a recipe that passes each of its named parameters by keyword. Every request model
those entry points accept, such as TranslationTarget, must be constructed somewhere in the
notebook, and each of its fields must be passed by keyword in at least one construction.
The notebook must also import only names the SDK still provides and target the service API
version used by the installed SDK.
"""

from __future__ import annotations

import ast
import enum
import inspect
import json
import re
import sys
import typing
from collections.abc import Callable, Iterable
from importlib import import_module
from importlib.metadata import version
from pathlib import Path

import azure.ai.translation.text as sdk
from azure.ai.translation.text import models

PACKAGE = "azure-ai-translation-text"
CLIENT_CLASS = "TextTranslationClient"
CLIENT_VARIABLE = "client"
CONSTRUCTOR = "__init__"
DEFAULT_NOTEBOOK = Path(__file__).resolve().parents[1] / "notebook.ipynb"


def label(entry_point: str) -> str:
    return f"{CLIENT_CLASS}(...)" if entry_point == CONSTRUCTOR else f"{CLIENT_VARIABLE}.{entry_point}(...)"


def signatures(member: Callable[..., object]) -> list[inspect.Signature]:
    # Overloads can declare parameters that the implementation only accepts through **kwargs.
    return [inspect.signature(member), *(inspect.signature(overload) for overload in typing.get_overloads(member))]


def named_parameters(member: Callable[..., object]) -> list[str]:
    """Return the parameters a call can pass by keyword, across the implementation and its overloads."""
    keyword_kinds = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    parameters = [parameter for signature in signatures(member) for parameter in signature.parameters.values()]
    return list(dict.fromkeys(p.name for p in parameters if p.name != "self" and p.kind in keyword_kinds))


def client_members() -> dict[str, Callable[..., object]]:
    client = getattr(sdk, CLIENT_CLASS, None)
    if client is None:
        sys.exit(f"{PACKAGE} {version(PACKAGE)} no longer exports {CLIENT_CLASS}; the notebook needs a migration.")

    members = {CONSTRUCTOR: client.__init__}
    members.update((name, member) for name, member in inspect.getmembers(client, callable) if not name.startswith("_"))
    return members


def model_fields(model: type) -> list[str]:
    # Generated models declare their fields as the keyword-only parameters of an __init__ overload.
    parameters = [parameter for signature in signatures(model.__init__) for parameter in signature.parameters.values()]
    fields = dict.fromkeys(parameter.name for parameter in parameters if parameter.kind is parameter.KEYWORD_ONLY)
    return list(fields) or list(inspect.get_annotations(model))


def request_models(members: Iterable[Callable[..., object]]) -> dict[str, list[str]]:
    """Map every model class reachable from the client's parameters to its fields."""
    classes = {
        name: member
        for name in models.__all__
        if isinstance(member := getattr(models, name), type) and not issubclass(member, enum.Enum)
    }

    # Annotations are matched as text because the SDK's forward references don't resolve at runtime.
    def referenced(annotations: Iterable[object]) -> set[str]:
        return {name for annotation in annotations for name in re.findall(r"\w+", str(annotation)) if name in classes}

    parameters = [p for member in members for signature in signatures(member) for p in signature.parameters.values()]
    pending = referenced(parameter.annotation for parameter in parameters)
    fields: dict[str, list[str]] = {}
    while pending:
        name = pending.pop()
        fields[name] = model_fields(classes[name])
        pending |= referenced(inspect.get_annotations(classes[name]).values()) - fields.keys()
    return fields


def sdk_surface() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    members = client_members()
    entry_points = {label(name): named_parameters(member) for name, member in members.items()}
    request_model_fields = {f"{name}(...)": fields for name, fields in request_models(members.values()).items()}
    return entry_points, request_model_fields


def call_label(call: ast.Call, request_model_labels: set[str]) -> str | None:
    func = call.func
    if isinstance(func, ast.Name) and func.id == CLIENT_CLASS:
        return label(CONSTRUCTOR)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == CLIENT_VARIABLE:
        return label(func.attr)
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
    return f"{name}(...)" if f"{name}(...)" in request_model_labels else None


def notebook_trees(notebook: Path) -> list[ast.Module]:
    trees = []
    for cell in json.loads(notebook.read_text(encoding="utf-8"))["cells"]:
        if cell["cell_type"] != "code":
            continue
        # IPython magics and shell escapes aren't valid Python syntax.
        lines = "".join(cell["source"]).splitlines()
        trees.append(ast.parse("\n".join(line for line in lines if not line.lstrip().startswith(("%", "!")))))
    return trees


def in_sdk(module_name: str) -> bool:
    return module_name == sdk.__name__ or module_name.startswith(f"{sdk.__name__}.")


def importable(module_name: str) -> bool:
    try:
        import_module(module_name)
    except ImportError:
        return False
    return True


def import_gaps(trees: list[ast.Module]) -> list[str]:
    gaps = []
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                gaps += [
                    f"notebook imports {alias.name}, which the SDK no longer provides"
                    for alias in node.names
                    if in_sdk(alias.name) and not importable(alias.name)
                ]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and in_sdk(node.module):
                if not importable(node.module):
                    gaps.append(f"notebook imports from {node.module}, which the SDK no longer provides")
                    continue
                module = import_module(node.module)
                gaps += [
                    f"notebook imports {alias.name} from {node.module}, which the SDK no longer provides"
                    for alias in node.names
                    if alias.name != "*"
                    and not hasattr(module, alias.name)
                    and not importable(f"{node.module}.{alias.name}")
                ]
    return gaps


def api_version_gaps(calls: dict[str, list[ast.Call]]) -> list[str]:
    constructor = label(CONSTRUCTOR)
    parameter = inspect.signature(client_members()[CONSTRUCTOR]).parameters.get("api_version")
    if parameter is None:
        return []  # A notebook that still passes api_version is reported as passing an unknown parameter.
    latest = parameter.default
    if not isinstance(latest, str):
        return [f"can't read the SDK's default api_version for {constructor}; update scripts/check_sdk_coverage.py"]

    gaps = []
    for call in calls.get(constructor, []):
        pinned = next((keyword.value for keyword in call.keywords if keyword.arg == "api_version"), None)
        if not isinstance(pinned, ast.Constant) or not isinstance(pinned.value, str):
            gaps.append(f"{constructor} must pin api_version={latest!r} as a string literal")
        elif pinned.value != latest:
            gaps.append(f"{constructor} pins api_version={pinned.value!r}; the SDK uses {latest!r}")
    return gaps


def find_gaps(notebook: Path) -> list[str]:
    entry_points, request_model_fields = sdk_surface()
    expected = entry_points | request_model_fields
    trees = notebook_trees(notebook)

    calls: dict[str, list[ast.Call]] = {}
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (name := call_label(node, set(request_model_fields))):
                calls.setdefault(name, []).append(node)
    used = {name: {kw.arg for call in nodes for kw in call.keywords if kw.arg} for name, nodes in calls.items()}

    gaps = [f"missing recipe for {name}" for name in sorted(entry_points.keys() - used.keys())]
    gaps += [f"no recipe constructs {name}" for name in sorted(request_model_fields.keys() - used.keys())]
    for name in sorted(expected.keys() & used.keys()):
        if missing := [parameter for parameter in expected[name] if parameter not in used[name]]:
            gaps.append(f"{name} doesn't pass: {', '.join(missing)}")
        if unknown := sorted(used[name] - set(expected[name])):
            gaps.append(f"{name} passes parameters the SDK no longer accepts: {', '.join(unknown)}")
    removed = sorted(used.keys() - expected.keys())
    gaps += [f"notebook calls {name}, which the SDK no longer provides" for name in removed]
    gaps += import_gaps(trees)
    gaps += api_version_gaps(calls)
    return gaps


def main() -> int:
    notebook = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_NOTEBOOK
    gaps = find_gaps(notebook)
    prefix = f"{PACKAGE} {version(PACKAGE)}"
    if gaps:
        print(f"{prefix}: {len(gaps)} gap(s) in {notebook.name}")
        print("\n".join(f"- {gap}" for gap in gaps))
        return 1
    print(f"{prefix}: {notebook.name} covers every {CLIENT_CLASS} entry point and request model")
    return 0


if __name__ == "__main__":
    sys.exit(main())
