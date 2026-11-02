# Definitions

What the platform runs, as data (ADR-0039). Nothing here is imported by the platform: it reads
these files, and runs the Python in `functions/` in the sandbox (`app/functions/`).

```
<module>/
  module.yaml              the vertical a workspace chooses: label, pitch, runbooks it owns
  shared/                  code the module's definitions share (in every bundle as `shared`)
  <definition>/
    definition.yaml        key, label, summary, version, functions, rules
    forms/*.json           input, settings, result: JSON Schema, written by scripts/write_forms.py
    functions/             compute.py, check.py, letters.py: what the sandbox calls
    <definition>/          the definition's own code, imported by its functions
    rules/                 the rules pack (ADR-0038)
    guide.md               how to read documents into the input
    tests/                 cases and the expert's expected results
```

Delete this directory and the platform still builds and its own tests pass.
