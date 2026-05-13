# T-005 — Generate the .NET client from OpenAPI via NSwag

**Status:** pending
**Created:** 2026-05-12
**Owner:** Claude Code
**Blocked by:** —

## Context

The .NET `AlgoInvest.Client` uses a hand-written Refit interface today. That works for MVP but creates contract drift — adding a new endpoint requires editing two places, and forgetting one is a silent bug.

Generate the .NET client from the engine's OpenAPI spec at build time. The hand-written Refit interface stays as a fallback for offline builds.

## Acceptance criteria

- [ ] NSwag installed as a `dotnet tool` (committed `.config/dotnet-tools.json`)
- [ ] MSBuild target: before build of `AlgoInvest.Client`, fetch `http://localhost:8000/openapi.json` if reachable; otherwise reuse the last committed `openapi.snapshot.json`
- [ ] Generated client lives at `cli/src/AlgoInvest.Client/Generated/EngineClient.cs`
- [ ] `Generated/` excluded from `dotnet format` but included in `dotnet build`
- [ ] Hand-written `IEngineApi` kept side by side for now; mark generated client preferred in `CLAUDE.md`
- [ ] CI does NOT require the engine to be running — uses the snapshot

## Files in scope

- `cli/.config/dotnet-tools.json` (new)
- `cli/src/AlgoInvest.Client/AlgoInvest.Client.csproj`
- `cli/src/AlgoInvest.Client/Generated/*.cs` (new, generated)
- `cli/src/AlgoInvest.Client/openapi.snapshot.json` (new, committed)
- `cli/CLAUDE.md`

## Out of scope

- Switching all commands over to the generated client (one PR at a time)
- gRPC

## Verify

```bash
cd cli
dotnet tool restore
dotnet build /warnaserror
```
