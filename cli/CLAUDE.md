# cli — .NET 10 CLI client

User-facing client. Calls the Python engine via HTTP. UX only — no business logic, no persistence, no broker access.

Personal preferences live in `~/.claude/CLAUDE.md`. Repo-level guidance is in `../CLAUDE.md` (and `../AGENTS.md` for the operating manual).

---

## Stack

- **.NET 10** (LTS). C# 13 language features.
- **Spectre.Console.Cli** for command tree + rendering.
- **Refit** for the HTTP client (will move to NSwag-generated when contract stabilizes).
- **System.Text.Json** for serialization (no Newtonsoft).
- **Serilog** (via `Microsoft.Extensions.Logging`) for logging.
- **xUnit + Shouldly + NSubstitute** for tests.

---

## Projects

```
src/
├── SnapdInvest.Cli/         Spectre.Console host, commands, formatting
└── SnapdInvest.Client/      Typed HTTP client for the engine

tests/
└── SnapdInvest.Cli.Tests.Unit/
```

---

## Conventions

Personal C# defaults from `~/.claude/CLAUDE.md` apply:

- Primary constructors preferred for services and DI types
- Async all the way down — no `.Result`, no `.Wait()`
- `var` when the type is obvious
- File-scoped namespaces
- Records for DTOs, value objects, immutable data
- Classes for entities with behavior

Project-specific additions:

- **Nullable reference types enabled** project-wide. No `#nullable disable`.
- **`required` members** over constructor parameters when the type is constructed by JSON or model binding.
- **Collection expressions** (`[1, 2, 3]`) over `new List<T> { ... }`.
- **No regions.** No `#region`.
- **`CancellationToken`** propagates through every async method that does I/O.
- **`TimeProvider`** for current time — never `DateTime.Now`/`DateTime.UtcNow`.
- **No comments restating the code.** Comment the why.

---

## Forbidden patterns

- **No business logic in this project.** The engine owns trading decisions. CLI formats input/output.
- **No persistence.** The engine owns state.
- **No direct broker calls.** Never. Even hypothetically. All trading goes through the engine.
- **No magic strings for command names** — use Spectre's typed command tree.
- **No string interpolation in log messages.** Use structured templates: `_logger.LogInformation("Order {OrderId} approved", id)`.

---

## Commands the CLI exposes

| Command | Description |
|---|---|
| `snapdinvest status` | Show portfolio, cash, open positions, pending recommendations |
| `snapdinvest run-once` | Manually trigger strategy / agent runs |
| `snapdinvest audit [--limit N]` | Show recent audit events |
| `snapdinvest recos` | List pending recommendations |
| `snapdinvest approve <id>` | Approve a recommendation (with optional quantity/price modification) |
| `snapdinvest reject <id>` | Reject a pending recommendation |

Each command renders output with Spectre.Console — tables for lists, panels for detail views, prompts for confirmations.

---

## Engine endpoint configuration

The engine URL is read from the `Engine:Url` configuration key. `Program.cs`
builds it via `ConfigurationBuilder` from these sources, in increasing priority
(later sources override earlier ones for the same key):

1. `appsettings.json` → `Engine:Url`
2. Environment variables prefixed `SNAPDINVEST_` — set
   `SNAPDINVEST_Engine__Url` (double underscore maps to `:` per .NET
   configuration conventions; the prefix is stripped before binding).
3. Command-line args — pass `--Engine:Url <value>` (literal colon, the form
   `AddCommandLine` understands).
4. **`--engine-url <value>`** — friendly kebab-case alias for `--Engine:Url`.
   Implemented as an arg preprocessor in `Program.cs` that pulls the value
   out of `args` and injects it as in-memory config. Highest priority of all
   command-line sources.
5. Fallback in `Program.cs`: `http://localhost:8000` when none of the above are set.

---

## Testing

- **xUnit + Shouldly + NSubstitute** — the same stack as Snapd, kept consistent.
- **One test file per class under test.**
- **Contract tests** against a running engine are kept in a separate test project (added when the contract stabilizes — week 2+).
- **Don't test Spectre.Console rendering itself.** Test command logic — what gets passed to the HTTP client.

---

## Build settings

Inherited from root `Directory.Build.props`:

- `TreatWarningsAsErrors=true`
- `Nullable=enable`
- `ImplicitUsings=enable`
- `LangVersion=latest`
- `EnforceCodeStyleInBuild=true`

---

## Commands

```bash
# Restore + build
dotnet restore
dotnet build /warnaserror

# Test
dotnet test
dotnet test --filter "FullyQualifiedName~StatusCommand"

# Format
dotnet format
dotnet format --verify-no-changes

# Run a command
dotnet run --project src/SnapdInvest.Cli -- status
dotnet run --project src/SnapdInvest.Cli -- audit --limit 20
```
