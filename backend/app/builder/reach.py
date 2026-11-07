"""What the check's code is: everything `functions/check.py` reaches (ADR-0039 step 6f). Pure.

A change the builder makes to the check waits for a person, because no test case sees a check
made weaker. A list of the check's files is not enough: on NK-04 the builder put a new, weaker
check in a new file and called it from the old one. So the check's code is found by following
it: from every top-level definition in `functions/check.py`, each function, class and constant
it names, in its own module or one it imports from the definition's sources, and so on. A
definition counts as changed when its syntax tree differs, so layout and comments do not.

What it does not follow: code reached by name at run time (`getattr`, `importlib`). The diff a
person reads still shows it.
"""

from __future__ import annotations

import ast

_DEF = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
CHECK = "functions/check.py"


def changed_in_check(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """`path: name` of each definition the check reaches, before or after, that is not the same."""
    old, new = _reach(before), _reach(after)
    return sorted(f"{path}: {name}" for path, name in set(old) | set(new) if old.get((path, name)) != new.get((path, name)))


def _reach(sources: dict[str, str]) -> dict[tuple[str, str], str]:
    modules: dict[str, tuple[dict, dict, dict]] = {}

    def module(path: str):
        if path not in modules:
            modules[path] = _index(path, sources)
        return modules[path]

    if CHECK not in sources:
        return {}
    seen: dict[tuple[str, str], str] = {}
    todo = [(CHECK, name) for name in module(CHECK)[0]]
    while todo:
        path, name = todo.pop()
        defs, names, mods = module(path)
        if (path, name) in seen:
            continue
        if name in defs:
            node = defs[name]
            seen[(path, name)] = ast.dump(node)
            for used in ast.walk(node):
                if isinstance(used, ast.Name):
                    todo += _resolve(path, used.id, defs, names)
                elif isinstance(used, ast.Attribute) and isinstance(used.value, ast.Name) and used.value.id in mods:
                    todo.append((mods[used.value.id], used.attr))
        elif name in names:  # imported into this module from another
            todo += _resolve(path, name, defs, names)
    return seen


def _resolve(path, name, defs, names) -> list[tuple[str, str]]:
    if name in defs:
        return [(path, name)]
    if name in names:
        target, imported = names[name]
        return [(target, imported)]
    return []


def _index(path: str, sources: dict[str, str]) -> tuple[dict, dict, dict]:
    """A module's top-level definitions; names it imports from the sources, and modules it imports."""
    try:
        tree = ast.parse(sources[path])
    except SyntaxError:
        return {"*": ast.Constant(sources[path])}, {}, {}
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, _DEF):
            defs[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                for t in target.elts if isinstance(target, ast.Tuple) else [target]:
                    if isinstance(t, ast.Name):
                        defs[t.id] = node
    names: dict[str, tuple[str, str]] = {}
    mods: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                local = alias.asname or alias.name
                if sub := _path_of(f"{node.module}.{alias.name}", sources):
                    mods[local] = sub  # `from pkg import module`
                elif target := _path_of(node.module, sources):
                    names[local] = (target, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if target := _path_of(alias.name, sources):
                    mods[alias.asname or alias.name] = target
    return defs, names, mods


def _path_of(module: str, sources: dict[str, str]) -> str | None:
    base = module.replace(".", "/")
    return next((p for p in (f"{base}.py", f"{base}/__init__.py") if p in sources), None)
