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
dotnet run --project src/AlgoInvest.Cli -- status
dotnet run --project src/AlgoInvest.Cli -- run-once --symbol AAPL --exchange NASDAQ
dotnet run --project src/AlgoInvest.Cli -- run-agent
dotnet run --project src/AlgoInvest.Cli -- recos
dotnet run --project src/AlgoInvest.Cli -- approve <id> --modify AAPL@NASDAQ=2.0
dotnet run --project src/AlgoInvest.Cli -- reject <id> --reason "not now"
dotnet run --project src/AlgoInvest.Cli -- audit --limit 20
```

## Configuration

Engine URL resolution order:

1. CLI option `--Engine:Url=...`
2. Environment variable `ALGOINVEST_Engine__Url`
3. `appsettings.json`
4. Default `http://localhost:8000`

## Projects

```
src/
├── AlgoInvest.Cli/         Spectre.Console host, commands, formatting
└── AlgoInvest.Client/      Refit-based HTTP client + DTOs

tests/
└── AlgoInvest.Cli.Tests.Unit/
```
