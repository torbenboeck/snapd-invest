# cli — .NET 10 CLI client

User-facing client. Calls the Python engine via HTTP. UX only.

See [`CLAUDE.md`](CLAUDE.md) for full guidance.

## Quick reference

```bash
# Restore
dotnet restore

# Build (warnings as errors)
dotnet build /warnaserror

# Test
dotnet test

# Format
dotnet format

# Run commands
dotnet run --project src/SnapdInvest.Cli -- status
dotnet run --project src/SnapdInvest.Cli -- run-once --symbol AAPL --exchange NASDAQ
dotnet run --project src/SnapdInvest.Cli -- run-agent
dotnet run --project src/SnapdInvest.Cli -- recos
dotnet run --project src/SnapdInvest.Cli -- approve <id> --modify AAPL@NASDAQ=2.0
dotnet run --project src/SnapdInvest.Cli -- reject <id> --reason "not now"
dotnet run --project src/SnapdInvest.Cli -- audit --limit 20
```

## Configuration

Engine URL resolution order:

1. CLI option `--Engine:Url=...`
2. Environment variable `SNAPDINVEST_Engine__Url`
3. `appsettings.json`
4. Default `http://localhost:8000`

## Projects

```
src/
├── SnapdInvest.Cli/         Spectre.Console host, commands, formatting
└── SnapdInvest.Client/      Refit-based HTTP client + DTOs

tests/
└── SnapdInvest.Cli.Tests.Unit/
```
