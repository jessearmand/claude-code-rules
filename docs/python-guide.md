- Prefer `uv` for package management
- Prefer httpx over requests for making http requests.
- Use types everywhere possible.

After a new implementation is written:
- Use `ruff` as a linter and code formatter, it can be run with `uvx` if it's installed globally
- Use `ty` as a type checker

`uv` manages project dependencies and environments, with support for lockfiles, workspaces, and more, similar to rye or poetry:

```
$ uv init example
Initialized project `example` at `/home/user/example`

$ cd example

# virtual environment is created at .venv
$ uv add ruff

$ uv run ruff check
All checks passed!

$ uv lock
Resolved 2 packages in 0.33ms

$ uv sync
Resolved 2 packages in 0.70ms
Audited 1 package in 0.02ms
```

When a script requires other packages, they must be installed into the environment that the script runs in. 
uv prefers to create these environments on-demand instead of using a long-lived virtual environment with manually managed dependencies. 
This requires explicit declaration of dependencies that are required for the script. 
Generally, it's recommended to use a project or inline metadata to declare dependencies, but uv supports requesting dependencies per invocation as well.

Request `rich` dependency using the --with option:
```
$ uv run --with rich example.py
```

Constraints can be added to the requested dependency if specific versions are needed:

```
$ uv run --with 'rich>12,<13' example.py
```

Multiple dependencies can be requested by repeating with --with option.

Note that if uv run is used in a project, these dependencies will be included in addition to the project's dependencies. To opt-out of this behavior, use the --no-project flag.

Use the check command to run the type checker:

```
uvx ty check
```

ty will run on all Python files in the working directory and or subdirectories. If used from a project, ty will run on all Python files in the project (starting in the directory with the pyproject.toml)

You can also provide specific paths to check:

```
uvx ty check example.py
```
