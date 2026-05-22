@echo off
rem -- snapd-invest engine launcher for cmd.exe ---------------------------
rem
rem  Starts the FastAPI engine on http://127.0.0.1:8000 with the scheduler
rem  enabled. Configuration is loaded from `engine\.env` (see
rem  `engine\.env.example` for the full list of supported variables).
rem
rem  Required env for autonomous MicroTrader on Saxo SIM:
rem    SNAPDINVEST_DEFAULT_ACCOUNT_NAME   name of your SIM account
rem    SNAPDINVEST_WATCHLIST              e.g. EURDKK@FX,EURUSD@FX
rem    SNAPDINVEST_SAXO_CLIENT_ID         from Saxo developer portal
rem    SNAPDINVEST_SAXO_REDIRECT_URI      e.g. http://127.0.0.1:8000/v1/oauth/saxo/callback
rem    SNAPDINVEST_ENCRYPTION_KEY         32-byte Fernet key (run `make init-keys`)
rem
rem  Sanity-check from another cmd window:
rem    cd cli
rem    dotnet run --project src\SnapdInvest.Cli -- status --account-name sim
rem ----------------------------------------------------------------------
setlocal
cd /d "%~dp0engine"
echo Starting snapd-invest engine on http://127.0.0.1:8000 ...
echo (Ctrl+C to stop.)
echo.
uv run uvicorn snapd_invest.api:app --host 127.0.0.1 --port 8000
endlocal
