# GitHub Copilot Terminal & Environment Rules

## Chat language

- Always respond in English, regardless of the language used in the code or documentation. 
- In code, use mainly English for identifiers and comments, but dataclasses and fields can remain in Hungarian if that is the established convention. 
Due to the project's domain language, Hungarian, it is acceptable to use Hungarian for domain-specific areas like LLM prompt engineering.


## Shell Environment
- **Primary Shell**: We are using PowerShell Core (`pwsh`). Do not provide `bash` or `sh` commands.
- **Syntax**: Use PowerShell-specific syntax (e.g., `$env:VAR = "value"`, `Test-Path`, `Remove-Item -Recurse`).

## Terminal Management (Preventing Multiple/Background Terminals)
- **Reuse Terminals**: Always attempt to run commands in the existing active terminal. Do not spawn new terminal instances for sequential steps.
- **Visibility**: Set `isBackground: false` for all terminal tasks to ensure I can see the output immediately.
- **Chaining**: Combine multiple steps into a single command block using `;` or `&&` to minimize terminal calls. 
  - *Example*: `cd ./src; python -m pytest`

## Python Environment Handling
- **Activation**: Before running any Python scripts, check for a virtual environment in the current directory (`.venv` or `venv`).
- **PowerShell Activation Logic**: Use the following logic to activate:
  ```powershell
  if (Test-Path ".\.venv\Scripts\Activate.ps1") { .\.venv\Scripts\Activate.ps1 }
  ```
- **Execution**: Prefer running modules via `python -m <module>` to ensure the venv-linked binary is used even if activation is bypassed.
- **Pip**: Always use `python -m pip` instead of just `pip`.

## Command Confirmation
- Always present the full command to me for review before execution.
- If a command is expected to run long (like a server), do not mark it as a background task; let it occupy the terminal so I can see logs.
- Activate the environment and reuse the terminal for subsequent commands to maintain context and visibility.

## Related documents

- See `docs/swe.md` for the overall software engineering principles and development cycle guidelines that should inform how you generate code and commands.
