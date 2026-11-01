"""Functions: the code an agent's definition carries, run in a sandbox (ADR-0039 D5).

`bundle.build_bundle` assembles what a function may see; `sandbox.sandbox().run` calls it. The
`guest/` directory is what runs inside: the entry point and the helper library.
"""
