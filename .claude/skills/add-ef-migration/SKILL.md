---
name: add-ef-migration
description: Add an EF Core migration to Snapd with the correct project flags (--project src/Snapd.Core --startup-project src/Snapd.Api). Optionally applies the migration to the local SQLite database. Use when the user asks to add a migration, run a migration, update the database, change the database schema, or create a new EF migration.
---

You are adding an EF Core migration to Snapd.

Required input (ask if missing — do not invent):

1. Migration name in PascalCase, describing the change (e.g. `AddInvoiceConfidence`, `AddPaymentLifecycle`, `RenameDocumentToInvoice`)

Procedure:

1. Validate name matches `^[A-Z][A-Za-z0-9]+$`. Reject names like `add stuff`, `migration1`, or `fix-thing`.
2. Confirm Snapd's expected EF Core project layout: `src/Snapd.Core/` contains the `DbContext`, `src/Snapd.Api/` is the startup project. If the layout differs from this assumption, stop and ask.
3. Confirm the EF provider in `Directory.Packages.props` is `Microsoft.EntityFrameworkCore.Sqlite`. If a different provider is referenced, stop and ask before proceeding.
4. Run: `dotnet ef migrations add <Name> --project src/Snapd.Core --startup-project src/Snapd.Api`. Capture stdout and stderr.
5. If the command fails, surface the exact error and stop. Common causes:
   - Missing `Microsoft.EntityFrameworkCore.Design` package on the API project.
   - Malformed entity configuration.
   - Conflicting migration name.
   - `dotnet-ef` global tool not installed (`dotnet tool install --global dotnet-ef` is the fix).
6. List the new migration files created under `src/Snapd.Core/Migrations/`. Open the `Up()` method and verify the operations match the intent of the migration name. If they do not match, warn the user before continuing.
7. Run `dotnet build /warnaserror`. If it fails, report and stop.
8. Ask the user: "Apply the migration to the local SQLite database now? (Y/n)"
9. If yes: run `dotnet ef database update --project src/Snapd.Core --startup-project src/Snapd.Api`. Surface any errors.
10. Output a summary:
    - Migration name and timestamp.
    - Files created.
    - Applied to local DB (yes/no).
    - Next step: update tests to cover the new schema if data shape changed.

Constraints:
- Never run `dotnet ef database drop`. If the user needs to reset, they invoke it manually.
- Never delete an existing migration. If the user wants to undo, suggest `dotnet ef migrations remove --project src/Snapd.Core --startup-project src/Snapd.Api`.
- Never modify an existing migration file. If the most recent migration is wrong, remove it (step above) and add a fresh one.
- Do not commit the migration files. The user does that explicitly.
