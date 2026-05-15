# T-001-A Saxo SIM OAuth + `get_account` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the auth-only first half of Saxo SIM integration — PKCE handshake, encrypted token persistence, `SaxoBroker.get_account()` against real SIM — leaving order placement for T-001-B.

**Architecture:** Authorization Code Grant + PKCE against `https://sim.logonvalidation.net`. Tokens stored encrypted at rest in a new `oauth_tokens` SQLite table via a `Cipher` abstraction keyed by `SNAPDINVEST_ENCRYPTION_KEY`. All new persistence is scoped by `account_id` from day one (multi-user readiness). `broker.py` refactors into a `broker/` package so SaxoBroker + OAuth helpers don't push the file past CLAUDE.md's 300-line threshold.

**Tech Stack:** Python 3.12 (engine), .NET 10 (CLI), pydantic-settings, SQLAlchemy 2.x async + Alembic, FastAPI, httpx + respx, `cryptography.fernet` (new dep), Spectre.Console.Cli, Refit.

**Reference spec:** [`docs/specs/T-001A-saxo-sim-oauth-and-get-account.md`](../specs/T-001A-saxo-sim-oauth-and-get-account.md)

---

## Env var name reconciliation

The spec used short names (`SAXO_ENV`, `SNAPD_ENCRYPTION_KEY`) for readability. The actual env-var names follow `engine/src/snapd_invest/config.py`'s existing `env_prefix="SNAPDINVEST_"` pattern. Mapping:

| Spec shorthand | Actual env var |
|---|---|
| `SAXO_ENV` | `SNAPDINVEST_SAXO_ENV` |
| `SAXO_CLIENT_ID` | `SNAPDINVEST_SAXO_CLIENT_ID` |
| `SAXO_REDIRECT_URI` | `SNAPDINVEST_SAXO_REDIRECT_URI` |
| `SNAPD_ENCRYPTION_KEY` | `SNAPDINVEST_ENCRYPTION_KEY` |

Task 23 updates the spec to remove the discrepancy.

---

## File structure

**New:**
- `engine/src/snapd_invest/crypto.py` — `Cipher` protocol + `FernetCipher` impl
- `engine/src/snapd_invest/broker/__init__.py` — re-exports + `BrokerError` hierarchy
- `engine/src/snapd_invest/broker/paper.py` — `PaperBroker` (moved from `broker.py`)
- `engine/src/snapd_invest/broker/saxo.py` — `SaxoBroker` with `get_account()` only
- `engine/src/snapd_invest/broker/saxo_oauth.py` — PKCE state, token exchange, refresh, persistence
- `engine/src/snapd_invest/tools/__init__.py` — empty package marker
- `engine/src/snapd_invest/tools/init_keys.py` — generates `SNAPDINVEST_ENCRYPTION_KEY`, appends to `engine/.env`
- `engine/alembic/versions/2026_05_14_1000-0004_oauth_schema.py` — new tables
- `engine/tests/unit/test_cipher.py`
- `engine/tests/unit/test_saxo_oauth.py`
- `engine/tests/unit/test_saxo_broker.py`
- `engine/tests/unit/test_init_keys.py`
- `engine/tests/integration/__init__.py`
- `engine/tests/integration/test_saxo_live.py`
- `cli/src/SnapdInvest.Cli/Commands/AuthSaxoCommand.cs`
- `cli/src/SnapdInvest.Cli/Commands/GetAccountCommand.cs`
- `cli/tests/SnapdInvest.Cli.Tests.Unit/AuthSaxoCommandTests.cs`
- `cli/tests/SnapdInvest.Cli.Tests.Unit/GetAccountCommandTests.cs`

**Modified:**
- `engine/pyproject.toml` — add `cryptography` dep + register `saxo_live` pytest marker
- `engine/src/snapd_invest/config.py` — `SAXO_ENV` / `SAXO_CLIENT_ID` / `SAXO_REDIRECT_URI` / `ENCRYPTION_KEY` fields + validators
- `engine/src/snapd_invest/models.py` — `OAuthState` + `OAuthToken` ORM models
- `engine/src/snapd_invest/api.py` — `POST /v1/oauth/saxo/start`, `GET /v1/oauth/saxo/callback`, `GET /v1/oauth/saxo/status`, `GET /v1/accounts/{id}`
- `engine/tests/unit/test_config.py` — coverage for new fields + `SAXO_ENV=live` block
- `engine/tests/unit/test_broker.py` — adjust imports for `broker/` package
- `engine/src/snapd_invest/execution.py` — replace direct `PaperBroker` ref with `broker_for(account)` factory (added in Task 17)
- `Makefile` — add `init-keys` and `test-engine-live` targets
- `docs/architecture/decision-log.md` — ADR-005
- `docs/architecture/module-map.md` — `broker.py` → `broker/` package
- `AGENTS.md` — SIM-live test docs
- `docs/specs/T-001A-saxo-sim-oauth-and-get-account.md` — env-var name fix
- `cli/src/SnapdInvest.Client/IEngineApi.cs` — new Refit methods
- `cli/src/SnapdInvest.Cli/Program.cs` — register new commands

**Deleted:**
- `engine/src/snapd_invest/broker.py` — replaced by package (after move)

---

## Tasks

### Task 1: Add `cryptography` dependency + register `saxo_live` pytest marker

**Files:**
- Modify: `engine/pyproject.toml`

- [ ] **Step 1: Add the dep**

  Edit `engine/pyproject.toml`. In `[project] dependencies = [...]`, add `"cryptography>=43.0",` near the other security-adjacent libs (or at the end of the list). Find the `[tool.pytest.ini_options]` section and add a `markers` entry:

  ```toml
  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  testpaths = ["tests"]
  python_files = "test_*.py"
  python_classes = "Test*"
  python_functions = "test_*"
  markers = [
      "saxo_live: requires real Saxo SIM credentials and SAXO_RUN_LIVE_TESTS=1 (deselect with -m 'not saxo_live')",
  ]
  ```

  If `markers` already exists, append to the list rather than overwriting.

- [ ] **Step 2: Resolve**

  Run: `cd engine && uv sync`
  Expected: lockfile updates, no errors.

- [ ] **Step 3: Smoke**

  Run: `cd engine && uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode()[:8])"`
  Expected: prints 8 base64 chars + newline. Confirms the dep is importable.

- [ ] **Step 4: Commit**

  ```bash
  git add engine/pyproject.toml engine/uv.lock
  git commit -m "chore(engine): add cryptography dep + register saxo_live pytest marker"
  ```

---

### Task 2: ADR-005 — Saxo OAuth flow choice

Writing the ADR up-front locks the decision before any code references it.

**Files:**
- Modify: `docs/architecture/decision-log.md`

- [ ] **Step 1: Append ADR-005**

  Add to the bottom of `docs/architecture/decision-log.md`, before the "How to add an entry" section:

  ```markdown
  ## ADR-005 — Saxo OAuth: Authorization Code + PKCE

  **Date:** 2026-05-14
  **Status:** Accepted

  ### Context

  T-001-A wires Saxo SIM as the second execution venue. Saxo OpenAPI exposes four OAuth 2.0 flows: Authorization Code Grant, Authorization Code Grant + PKCE, Implicit Flow, Certificate-Based Authentication. The original `tasks/T-001-saxo-sim-integration.md` named `client_credentials`. That flow is not supported by Saxo for retail developers; the task spec was drafted before OAuth research.

  ### Options considered

  1. **Authorization Code Grant** — server-side web app. Requires `client_secret`. Works against localhost callbacks.
  2. **Authorization Code Grant + PKCE** — native / desktop app. No `client_secret`; PKCE verifier replaces it. Works against localhost callbacks. Saxo's docs explicitly endorse this for "Native applications" (RFC 7636).
  3. **Implicit Flow** — single-page app. No refresh token. Disqualifying for autonomous MicroTrader.
  4. **Certificate-Based Authentication** — "select partners upon request" only.

  ### Decision

  **Authorization Code + PKCE**, with the engine acting as a "Native application" registered against the user's SIM developer account.

  - No `client_secret` in `.env` — PKCE removes that attack surface.
  - One-time browser-based consent at first run; refresh token persists for subsequent runs.
  - `state` parameter doubles as account demux (multi-user readiness) — one redirect URI serves N accounts.
  - Tokens encrypted at rest in `oauth_tokens` table via a `Cipher` abstraction keyed by `SNAPDINVEST_ENCRYPTION_KEY`.

  ### Consequences

  **Pro:**
  - Smaller secrets footprint (no client_secret).
  - Saxo's native-app registration is the documented happy path; less likely to hit portal-side gotchas.
  - PKCE verifier per-handshake means a leaked authorization code is useless without the corresponding verifier.

  **Con:**
  - Requires running a local HTTP listener (the engine) to receive the callback. Acceptable — the engine already listens.
  - The Saxo developer portal defaults new apps to "Web application"; the user must select "Native application" at registration (documented gotcha).

  ### Notes

  - SIM endpoints: `https://sim.logonvalidation.net/{authorize,token}`.
  - SIM API base: `https://gateway.saxobank.com/sim/openapi/`.
  - Live endpoints are explicitly NOT configured; `SNAPDINVEST_SAXO_ENV=live` is blocked by `Settings` validation and by `.claude/hooks/pre_tool_bash.py`.
  - Token TTLs (access vs refresh) will be observed at first SIM exchange and appended here.
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add docs/architecture/decision-log.md
  git commit -m "docs(adr): ADR-005 Saxo OAuth Authorization Code + PKCE"
  ```

---

### Task 3: `Cipher` protocol + `FernetCipher` impl + tests

**Files:**
- Create: `engine/src/snapd_invest/crypto.py`
- Create: `engine/tests/unit/test_cipher.py`

- [ ] **Step 1: Write the failing tests first**

  Create `engine/tests/unit/test_cipher.py`:

  ```python
  """Tests for `snapd_invest.crypto`."""

  from __future__ import annotations

  import pytest
  from cryptography.fernet import Fernet, InvalidToken

  from snapd_invest.crypto import FernetCipher, generate_key


  class TestFernetCipher:
      def test_roundtrip_returns_original_plaintext(self) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          plaintext = "saxo-refresh-token-abc123"
          assert cipher.decrypt(cipher.encrypt(plaintext)) == plaintext

      def test_encrypt_produces_different_ciphertexts_for_same_plaintext(self) -> None:
          # Fernet includes a random IV; the same plaintext encrypts differently each call
          cipher = FernetCipher(Fernet.generate_key())
          assert cipher.encrypt("x") != cipher.encrypt("x")

      def test_decrypt_with_wrong_key_raises(self) -> None:
          a = FernetCipher(Fernet.generate_key())
          b = FernetCipher(Fernet.generate_key())
          ciphertext = a.encrypt("secret")
          with pytest.raises(InvalidToken):
              b.decrypt(ciphertext)

      def test_decrypt_tampered_ciphertext_raises(self) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          ciphertext = cipher.encrypt("secret")
          tampered = ciphertext[:-2] + "ZZ"
          with pytest.raises(InvalidToken):
              cipher.decrypt(tampered)

      def test_init_with_invalid_key_raises_at_construction(self) -> None:
          with pytest.raises((ValueError, TypeError)):
              FernetCipher(b"not-a-valid-fernet-key")


  class TestGenerateKey:
      def test_generates_a_fernet_compatible_key(self) -> None:
          key = generate_key()
          # Must be usable as a Fernet key
          FernetCipher(key.encode())

      def test_each_call_returns_a_different_key(self) -> None:
          assert generate_key() != generate_key()
  ```

- [ ] **Step 2: Run tests to verify they fail**

  Run: `cd engine && uv run pytest tests/unit/test_cipher.py -v`
  Expected: collection errors / `ModuleNotFoundError: No module named 'snapd_invest.crypto'`.

- [ ] **Step 3: Implement `crypto.py`**

  Create `engine/src/snapd_invest/crypto.py`:

  ```python
  """Symmetric encryption for secrets at rest.

  `Cipher` is the abstraction the rest of the engine talks to. `FernetCipher`
  is the default implementation. The single-tenant master key comes from the
  `SNAPDINVEST_ENCRYPTION_KEY` env var (Fernet-format base64) and is injected
  into one shared `FernetCipher` instance at engine startup.

  Multi-tenant evolution path: introduce a `KeyProvider` indirection so per-
  account or per-tenant keys can replace the single env-var key without
  touching schema or call sites.
  """

  from __future__ import annotations

  from typing import Protocol

  from cryptography.fernet import Fernet


  class Cipher(Protocol):
      """Symmetric cipher with authenticated encryption.

      `encrypt(plaintext) -> ciphertext` and `decrypt(ciphertext) -> plaintext`.
      Both operate on `str` (the broker uses string tokens). Implementations
      must raise on malformed/tampered input.
      """

      def encrypt(self, plaintext: str) -> str: ...
      def decrypt(self, ciphertext: str) -> str: ...


  class FernetCipher:
      """Fernet (AES-128-CBC + HMAC-SHA256) with the supplied key.

      The key must be a 32-byte URL-safe base64 string (Fernet's required
      format). Use `generate_key()` to produce one.
      """

      def __init__(self, key: bytes) -> None:
          self._fernet = Fernet(key)  # raises on bad key shape

      def encrypt(self, plaintext: str) -> str:
          return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

      def decrypt(self, ciphertext: str) -> str:
          return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")


  def generate_key() -> str:
      """Generate a fresh Fernet key as a URL-safe base64 string."""
      return Fernet.generate_key().decode("ascii")
  ```

- [ ] **Step 4: Run tests to verify they pass**

  Run: `cd engine && uv run pytest tests/unit/test_cipher.py -v`
  Expected: 7 passed.

- [ ] **Step 5: Type + lint**

  Run: `cd engine && uv run mypy src/snapd_invest/crypto.py && uv run ruff check src/snapd_invest/crypto.py`
  Expected: no issues.

- [ ] **Step 6: Commit**

  ```bash
  git add engine/src/snapd_invest/crypto.py engine/tests/unit/test_cipher.py
  git commit -m "feat(engine): add Cipher protocol + FernetCipher for at-rest encryption"
  ```

---

### Task 4: `init_keys` Python tool + Makefile target

**Files:**
- Create: `engine/src/snapd_invest/tools/__init__.py`
- Create: `engine/src/snapd_invest/tools/init_keys.py`
- Create: `engine/tests/unit/test_init_keys.py`
- Modify: `Makefile`

- [ ] **Step 1: Write failing tests**

  Create `engine/tests/unit/test_init_keys.py`:

  ```python
  """Tests for `snapd_invest.tools.init_keys`."""

  from __future__ import annotations

  import pytest

  from snapd_invest.tools.init_keys import KeyAlreadyExistsError, run


  class TestInitKeys:
      def test_creates_env_file_with_key_when_missing(self, tmp_path) -> None:
          env_file = tmp_path / ".env"
          run(env_file=env_file)
          content = env_file.read_text(encoding="utf-8")
          assert "SNAPDINVEST_ENCRYPTION_KEY=" in content
          # Key should be present and non-empty
          line = next(ln for ln in content.splitlines() if ln.startswith("SNAPDINVEST_ENCRYPTION_KEY="))
          _, _, value = line.partition("=")
          assert len(value) > 30

      def test_appends_to_existing_env_file_without_key(self, tmp_path) -> None:
          env_file = tmp_path / ".env"
          env_file.write_text("SNAPDINVEST_SAXO_ENV=sim\n", encoding="utf-8")
          run(env_file=env_file)
          content = env_file.read_text(encoding="utf-8")
          assert "SNAPDINVEST_SAXO_ENV=sim" in content
          assert "SNAPDINVEST_ENCRYPTION_KEY=" in content

      def test_refuses_when_key_already_present(self, tmp_path) -> None:
          env_file = tmp_path / ".env"
          env_file.write_text("SNAPDINVEST_ENCRYPTION_KEY=already-here\n", encoding="utf-8")
          with pytest.raises(KeyAlreadyExistsError):
              run(env_file=env_file)
          # Existing key must not be overwritten
          assert "SNAPDINVEST_ENCRYPTION_KEY=already-here" in env_file.read_text(encoding="utf-8")

      def test_generated_key_is_unique_per_invocation(self, tmp_path) -> None:
          env_a = tmp_path / "a.env"
          env_b = tmp_path / "b.env"
          run(env_file=env_a)
          run(env_file=env_b)
          a = env_a.read_text(encoding="utf-8")
          b = env_b.read_text(encoding="utf-8")
          assert a != b
  ```

- [ ] **Step 2: Run tests, verify they fail**

  Run: `cd engine && uv run pytest tests/unit/test_init_keys.py -v`
  Expected: `ModuleNotFoundError: No module named 'snapd_invest.tools'`.

- [ ] **Step 3: Create the package marker**

  Create `engine/src/snapd_invest/tools/__init__.py`:

  ```python
  """One-off CLI tools (bootstrap helpers). Not part of the engine runtime."""
  ```

- [ ] **Step 4: Implement `init_keys.py`**

  Create `engine/src/snapd_invest/tools/init_keys.py`:

  ```python
  """Generate a fresh `SNAPDINVEST_ENCRYPTION_KEY` and append it to `.env`.

  Invoked from the project Makefile (`make init-keys`). Refuses to overwrite
  an existing key — rotation is a deliberate manual step, not a side-effect
  of running this tool twice.
  """

  from __future__ import annotations

  import sys
  from pathlib import Path

  from snapd_invest.crypto import generate_key

  ENV_VAR = "SNAPDINVEST_ENCRYPTION_KEY"


  class KeyAlreadyExistsError(RuntimeError):
      """Raised when the target `.env` already declares `SNAPDINVEST_ENCRYPTION_KEY`."""


  def run(env_file: Path) -> str:
      """Generate a key and append it to `env_file`.

      Returns the generated key. Raises `KeyAlreadyExistsError` if the file
      already declares the env var.
      """
      if env_file.exists():
          for line in env_file.read_text(encoding="utf-8").splitlines():
              if line.startswith(f"{ENV_VAR}="):
                  raise KeyAlreadyExistsError(
                      f"{ENV_VAR} already set in {env_file}. To rotate, remove the line first."
                  )

      key = generate_key()
      separator = "\n" if env_file.exists() and not env_file.read_text(encoding="utf-8").endswith("\n") else ""
      with env_file.open("a", encoding="utf-8") as f:
          f.write(f"{separator}{ENV_VAR}={key}\n")
      return key


  def main() -> int:
      env_file = Path(__file__).resolve().parents[3] / ".env"  # engine/.env
      try:
          run(env_file=env_file)
      except KeyAlreadyExistsError as exc:
          print(f"refused: {exc}", file=sys.stderr)
          return 1
      print(f"wrote {ENV_VAR} to {env_file}")
      return 0


  if __name__ == "__main__":
      raise SystemExit(main())
  ```

- [ ] **Step 5: Run tests, verify they pass**

  Run: `cd engine && uv run pytest tests/unit/test_init_keys.py -v`
  Expected: 4 passed.

- [ ] **Step 6: Wire up the Makefile target**

  Edit `Makefile`. In the meta/installation block (near `install-hooks`), add:

  ```make
  init-keys:
  	cd engine && uv run python -m snapd_invest.tools.init_keys
  ```

  Also add `init-keys` to the `.PHONY` line if one exists.

- [ ] **Step 7: Smoke the Makefile target**

  Run: `make init-keys` (from repo root), then immediately run it again.
  Expected: First invocation prints `wrote SNAPDINVEST_ENCRYPTION_KEY to .../engine/.env`. Second prints `refused: SNAPDINVEST_ENCRYPTION_KEY already set ...` and exits 1.

  **Cleanup before committing:** open `engine/.env`, delete the `SNAPDINVEST_ENCRYPTION_KEY=...` line you just wrote — the real key will be regenerated when the developer runs `make init-keys` post-merge. (`engine/.env` is gitignored so it won't be committed anyway, but tidy is tidy.)

- [ ] **Step 8: Commit**

  ```bash
  git add engine/src/snapd_invest/tools/__init__.py engine/src/snapd_invest/tools/init_keys.py engine/tests/unit/test_init_keys.py Makefile
  git commit -m "feat(engine): add init-keys tool to generate SNAPDINVEST_ENCRYPTION_KEY"
  ```

---

### Task 5: Extend `Settings` with Saxo + encryption fields

**Files:**
- Modify: `engine/src/snapd_invest/config.py`
- Modify: `engine/tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

  Open `engine/tests/unit/test_config.py` and append a new test class (preserve existing tests):

  ```python
  class TestSaxoSettings:
      def test_defaults_are_none_when_env_vars_absent(self, monkeypatch) -> None:
          for var in ("SNAPDINVEST_SAXO_ENV", "SNAPDINVEST_SAXO_CLIENT_ID",
                      "SNAPDINVEST_SAXO_REDIRECT_URI", "SNAPDINVEST_ENCRYPTION_KEY"):
              monkeypatch.delenv(var, raising=False)
          settings = Settings(_env_file=None)  # type: ignore[call-arg]
          assert settings.saxo_env is None
          assert settings.saxo_client_id is None
          assert settings.saxo_redirect_uri is None
          assert settings.encryption_key is None

      def test_reads_from_env(self, monkeypatch) -> None:
          monkeypatch.setenv("SNAPDINVEST_SAXO_ENV", "sim")
          monkeypatch.setenv("SNAPDINVEST_SAXO_CLIENT_ID", "abc123")
          monkeypatch.setenv("SNAPDINVEST_SAXO_REDIRECT_URI", "http://localhost:8000/v1/oauth/saxo/callback")
          monkeypatch.setenv("SNAPDINVEST_ENCRYPTION_KEY", "k" * 44)
          settings = Settings(_env_file=None)  # type: ignore[call-arg]
          assert settings.saxo_env == "sim"
          assert settings.saxo_client_id == "abc123"
          assert settings.saxo_redirect_uri == "http://localhost:8000/v1/oauth/saxo/callback"
          assert settings.encryption_key == "k" * 44

      def test_saxo_env_live_is_rejected(self, monkeypatch) -> None:
          monkeypatch.setenv("SNAPDINVEST_SAXO_ENV", "live")
          with pytest.raises(ValidationError, match="live"):
              Settings(_env_file=None)  # type: ignore[call-arg]

      def test_saxo_env_invalid_value_rejected(self, monkeypatch) -> None:
          monkeypatch.setenv("SNAPDINVEST_SAXO_ENV", "production")
          with pytest.raises(ValidationError):
              Settings(_env_file=None)  # type: ignore[call-arg]
  ```

  Add `from pydantic import ValidationError` if not already imported.

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_config.py::TestSaxoSettings -v`
  Expected: `AttributeError: 'Settings' object has no attribute 'saxo_env'`.

- [ ] **Step 3: Extend `Settings`**

  In `engine/src/snapd_invest/config.py`, add fields + validator inside the `Settings` class (after existing fields, before any existing validators):

  ```python
      saxo_env: str | None = Field(
          default=None,
          description="Saxo environment selector. Only 'sim' is permitted at MVP; 'live' is hard-blocked.",
      )
      saxo_client_id: str | None = Field(
          default=None,
          description="Saxo OAuth app key (the 'client_id' for Authorization Code + PKCE).",
      )
      saxo_redirect_uri: str | None = Field(
          default=None,
          description="Callback URL registered with Saxo. Must match exactly, including scheme and trailing slash.",
      )
      encryption_key: str | None = Field(
          default=None,
          description="Fernet master key for at-rest encryption (oauth_tokens). Generate via `make init-keys`.",
      )

      @field_validator("saxo_env")
      @classmethod
      def _validate_saxo_env(cls, v: str | None) -> str | None:
          if v is None:
              return None
          if v == "live":
              raise ValueError(
                  "SNAPDINVEST_SAXO_ENV=live is hard-blocked at MVP. "
                  "Use 'sim' for Saxo simulation."
              )
          if v != "sim":
              raise ValueError(f"SNAPDINVEST_SAXO_ENV must be 'sim' (or unset), got {v!r}")
          return v
  ```

- [ ] **Step 4: Run tests, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_config.py -v`
  Expected: all (existing + new) pass.

- [ ] **Step 5: Type + lint**

  Run: `cd engine && uv run mypy src/snapd_invest/config.py && uv run ruff check src/snapd_invest/config.py`
  Expected: no issues.

- [ ] **Step 6: Commit**

  ```bash
  git add engine/src/snapd_invest/config.py engine/tests/unit/test_config.py
  git commit -m "feat(engine): add Saxo + encryption-key settings with live-env block"
  ```

---

### Task 6: Refactor `broker.py` to `broker/` package (move-only, no behavior change)

This is intentionally a pure refactor so the move shows up in `git log --follow` cleanly. SaxoBroker code lands in later tasks.

**Files:**
- Create: `engine/src/snapd_invest/broker/__init__.py`
- Create: `engine/src/snapd_invest/broker/paper.py`
- Delete: `engine/src/snapd_invest/broker.py`
- Modify: `engine/tests/unit/test_broker.py` (imports only)

- [ ] **Step 1: Confirm baseline is green**

  Run: `cd engine && uv run pytest tests/unit/test_broker.py -v`
  Expected: all pass.

- [ ] **Step 2: Move the file via `git mv`**

  ```bash
  cd engine/src/snapd_invest
  mkdir broker
  git mv broker.py broker/paper.py
  cd ../../..
  ```

- [ ] **Step 3: Split the contents — `paper.py` keeps PaperBroker only**

  Edit `engine/src/snapd_invest/broker/paper.py`:
  - Remove the `IBroker`, `OrderRequest`, `FillResult` definitions and the `Side` type alias.
  - Keep `PaperBroker` and its internals.
  - Update top-of-file docstring to "PaperBroker — in-memory paper-trading implementation of IBroker."
  - Change imports: `from snapd_invest.broker import IBroker, OrderRequest, FillResult, Side` (the package will re-export these).

- [ ] **Step 4: Create `broker/__init__.py` with protocols + DTOs**

  Create `engine/src/snapd_invest/broker/__init__.py`:

  ```python
  """Broker abstraction.

  `IBroker` is the contract every execution venue implements:

  - `PaperBroker` — in-memory paper-trading (`paper` accounts).
  - `SaxoBroker` — Saxo SIM (`sim` accounts). T-001-A: `get_account` only.

  This package owns ALL broker imports — neither strategies, agents, the risk
  gate, nor execution code should import broker-vendor SDKs directly.
  """

  from __future__ import annotations

  from dataclasses import dataclass
  from decimal import Decimal
  from typing import TYPE_CHECKING, Literal, Protocol

  from snapd_invest.broker.paper import PaperBroker

  if TYPE_CHECKING:
      from sqlalchemy.ext.asyncio import AsyncSession

      from snapd_invest.models import Account, Instrument, Order, Trade

  Side = Literal["buy", "sell"]


  @dataclass(slots=True, frozen=True)
  class OrderRequest:
      """Request to place an order. Validated by `risk.py` before reaching here."""

      account: Account
      instrument: Instrument
      side: Side
      quantity: Decimal
      limit_price: Decimal | None
      source: str
      idempotency_key: str
      correlation_id: str | None = None


  @dataclass(slots=True, frozen=True)
  class FillResult:
      """Outcome of placing an order."""

      order: Order
      trades: list[Trade]
      was_idempotent_replay: bool


  class IBroker(Protocol):
      """Execution venue."""

      async def place_order(self, session: AsyncSession, request: OrderRequest) -> FillResult: ...
      async def get_last_price(
          self, session: AsyncSession, *, instrument: Instrument
      ) -> Decimal | None: ...


  __all__ = [
      "FillResult",
      "IBroker",
      "OrderRequest",
      "PaperBroker",
      "Side",
  ]
  ```

- [ ] **Step 5: Update test imports**

  In `engine/tests/unit/test_broker.py`, ensure `from snapd_invest.broker import PaperBroker, OrderRequest, ...` still works. If the tests imported from `snapd_invest.broker` already (not `snapd_invest.broker.paper`), no change needed. Verify with grep:

  Run: `cd engine && grep -nE "from snapd_invest\.broker" tests/unit/test_broker.py`
  Expected: imports use the package, no `snapd_invest.broker.paper` references.

- [ ] **Step 6: Run all tests**

  Run: `cd engine && uv run pytest`
  Expected: 125 pass (same as before the refactor).

- [ ] **Step 7: Type + lint**

  Run: `cd engine && uv run mypy src && uv run ruff check && uv run ruff format --check`
  Expected: clean.

- [ ] **Step 8: Commit**

  ```bash
  git add engine/src/snapd_invest/broker/ engine/src/snapd_invest/broker.py engine/tests/unit/test_broker.py
  git commit -m "refactor(engine): move broker.py into broker/ package (no behavior change)"
  ```

  Note: `git status` will show `deleted: broker.py` + the new package files. That's correct; `git mv` already staged the rename.

---

### Task 7: Add `BrokerError` hierarchy

**Files:**
- Modify: `engine/src/snapd_invest/broker/__init__.py`
- Create: `engine/tests/unit/test_broker_errors.py`

- [ ] **Step 1: Write failing tests**

  Create `engine/tests/unit/test_broker_errors.py`:

  ```python
  """Tests for the broker exception hierarchy."""

  from __future__ import annotations

  import pytest

  from snapd_invest.broker import (
      BrokerAuthError,
      BrokerError,
      BrokerHttpError,
      BrokerTimeoutError,
  )


  class TestBrokerErrorHierarchy:
      def test_all_subclasses_are_broker_errors(self) -> None:
          assert issubclass(BrokerAuthError, BrokerError)
          assert issubclass(BrokerHttpError, BrokerError)
          assert issubclass(BrokerTimeoutError, BrokerError)

      def test_http_error_carries_status_and_body(self) -> None:
          err = BrokerHttpError(status_code=503, body="upstream timeout")
          assert err.status_code == 503
          assert err.body == "upstream timeout"
          assert "503" in str(err)

      def test_auth_error_can_carry_a_reason(self) -> None:
          err = BrokerAuthError("refresh token expired")
          assert "refresh token expired" in str(err)

      def test_can_catch_any_subclass_as_broker_error(self) -> None:
          with pytest.raises(BrokerError):
              raise BrokerHttpError(status_code=400, body="bad request")
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_broker_errors.py -v`
  Expected: `ImportError: cannot import name 'BrokerAuthError' from 'snapd_invest.broker'`.

- [ ] **Step 3: Add error classes to `broker/__init__.py`**

  In `engine/src/snapd_invest/broker/__init__.py`, before the `Side` type alias, add:

  ```python
  class BrokerError(Exception):
      """Base class for all broker-layer failures."""


  class BrokerAuthError(BrokerError):
      """OAuth token problem — missing, expired, or refresh failed."""


  class BrokerHttpError(BrokerError):
      """Broker returned an HTTP error (4xx / 5xx)."""

      def __init__(self, status_code: int, body: str) -> None:
          super().__init__(f"broker HTTP {status_code}: {body[:200]}")
          self.status_code = status_code
          self.body = body


  class BrokerTimeoutError(BrokerError):
      """Broker call timed out."""
  ```

  And extend `__all__`:

  ```python
  __all__ = [
      "BrokerAuthError",
      "BrokerError",
      "BrokerHttpError",
      "BrokerTimeoutError",
      "FillResult",
      "IBroker",
      "OrderRequest",
      "PaperBroker",
      "Side",
  ]
  ```

- [ ] **Step 4: Run tests, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_broker_errors.py -v`
  Expected: 4 passed.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/broker/__init__.py engine/tests/unit/test_broker_errors.py
  git commit -m "feat(engine): add BrokerError hierarchy (Auth/Http/Timeout)"
  ```

---

### Task 8: `OAuthState` + `OAuthToken` SQLAlchemy models

**Files:**
- Modify: `engine/src/snapd_invest/models.py`

- [ ] **Step 1: Read existing model patterns**

  Skim `engine/src/snapd_invest/models.py` to mirror the style for `Account` (column types, `new_id` default, `DateTime(timezone=True)`).

- [ ] **Step 2: Add models**

  Append to `engine/src/snapd_invest/models.py` (after the last existing model class, before any `__all__`):

  ```python
  class OAuthState(Base):
      """Short-lived PKCE state for an in-flight OAuth handshake.

      Created when the engine returns an authorize URL; consumed when the
      browser callback hits. The `state` parameter doubles as CSRF token and
      account demux key. Rows older than 10 minutes are stale.
      """

      __tablename__ = "oauth_state"

      id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
      account_id: Mapped[str] = mapped_column(
          String(36), ForeignKey("accounts.id"), nullable=False, index=True
      )
      broker: Mapped[str] = mapped_column(String(16), nullable=False)
      state: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
      code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
      created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
      expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


  class OAuthToken(Base):
      """Persisted, encrypted OAuth tokens per (account, broker).

      Tokens are Fernet-encrypted in code before insertion — see
      `snapd_invest.broker.saxo_oauth`. The DB never sees plaintext.
      """

      __tablename__ = "oauth_tokens"

      id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
      account_id: Mapped[str] = mapped_column(
          String(36), ForeignKey("accounts.id"), nullable=False
      )
      broker: Mapped[str] = mapped_column(String(16), nullable=False)
      access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
      refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
      access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
      refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
      created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
      updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

      __table_args__ = (UniqueConstraint("account_id", "broker", name="uq_oauth_tokens_account_broker"),)
  ```

  Ensure `Text`, `UniqueConstraint`, `ForeignKey` are imported at the top of the file.

- [ ] **Step 3: Type-check**

  Run: `cd engine && uv run mypy src/snapd_invest/models.py`
  Expected: clean.

- [ ] **Step 4: Commit**

  ```bash
  git add engine/src/snapd_invest/models.py
  git commit -m "feat(engine): add OAuthState + OAuthToken ORM models"
  ```

---

### Task 9: Alembic migration `0004_oauth_schema`

**Files:**
- Create: `engine/alembic/versions/2026_05_14_1000-0004_oauth_schema.py`

- [ ] **Step 1: Write migration**

  Create `engine/alembic/versions/2026_05_14_1000-0004_oauth_schema.py`:

  ```python
  """oauth schema

  Revision ID: 0004
  Revises: 0003
  Create Date: 2026-05-14 10:00:00

  Adds: oauth_state, oauth_tokens.
  """

  from __future__ import annotations

  from typing import TYPE_CHECKING

  import sqlalchemy as sa
  from alembic import op

  if TYPE_CHECKING:
      from collections.abc import Sequence

  revision: str = "0004"
  down_revision: str | None = "0003"
  branch_labels: str | Sequence[str] | None = None
  depends_on: str | Sequence[str] | None = None


  def upgrade() -> None:
      op.create_table(
          "oauth_state",
          sa.Column("id", sa.String(length=36), nullable=False),
          sa.Column("account_id", sa.String(length=36), nullable=False),
          sa.Column("broker", sa.String(length=16), nullable=False),
          sa.Column("state", sa.String(length=64), nullable=False),
          sa.Column("code_verifier", sa.String(length=128), nullable=False),
          sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
          sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
          sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
          sa.PrimaryKeyConstraint("id"),
          sa.UniqueConstraint("state"),
      )
      op.create_index("ix_oauth_state_account_id", "oauth_state", ["account_id"])

      op.create_table(
          "oauth_tokens",
          sa.Column("id", sa.String(length=36), nullable=False),
          sa.Column("account_id", sa.String(length=36), nullable=False),
          sa.Column("broker", sa.String(length=16), nullable=False),
          sa.Column("access_token_encrypted", sa.Text(), nullable=False),
          sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
          sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
          sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=False),
          sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
          sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
          sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
          sa.PrimaryKeyConstraint("id"),
          sa.UniqueConstraint("account_id", "broker", name="uq_oauth_tokens_account_broker"),
      )


  def downgrade() -> None:
      op.drop_table("oauth_tokens")
      op.drop_index("ix_oauth_state_account_id", table_name="oauth_state")
      op.drop_table("oauth_state")
  ```

- [ ] **Step 2: Apply locally**

  Run: `cd engine && uv run alembic upgrade head`
  Expected: log shows `Running upgrade 0003 -> 0004, oauth schema`.

- [ ] **Step 3: Round-trip**

  Run: `cd engine && uv run alembic downgrade -1 && uv run alembic upgrade head`
  Expected: both succeed; the downgrade drops the tables, upgrade recreates them.

- [ ] **Step 4: Confirm engine tests still pass**

  Run: `cd engine && uv run pytest`
  Expected: same count as before (no test regression).

- [ ] **Step 5: Commit**

  ```bash
  git add engine/alembic/versions/2026_05_14_1000-0004_oauth_schema.py
  git commit -m "feat(engine): alembic 0004 — add oauth_state + oauth_tokens"
  ```

---

### Task 10: `saxo_oauth.py` — PKCE generator + `oauth_state` service functions

**Files:**
- Create: `engine/src/snapd_invest/broker/saxo_oauth.py`
- Create: `engine/tests/unit/test_saxo_oauth.py`

- [ ] **Step 1: Write the failing tests for the PKCE generator**

  Create `engine/tests/unit/test_saxo_oauth.py`:

  ```python
  """Tests for `snapd_invest.broker.saxo_oauth`."""

  from __future__ import annotations

  import re
  from datetime import UTC, datetime, timedelta
  from typing import TYPE_CHECKING

  import pytest

  from snapd_invest.broker.saxo_oauth import (
      PkceChallenge,
      consume_oauth_state,
      generate_pkce,
      persist_oauth_state,
  )
  from snapd_invest.portfolio import create_account

  if TYPE_CHECKING:
      from sqlalchemy.ext.asyncio import AsyncSession

      from snapd_invest.clock import FakeClock


  class TestGeneratePkce:
      def test_verifier_is_43_to_128_url_safe_chars(self) -> None:
          ch = generate_pkce()
          assert isinstance(ch, PkceChallenge)
          assert 43 <= len(ch.verifier) <= 128
          assert re.fullmatch(r"[A-Za-z0-9_\-]+", ch.verifier)

      def test_challenge_is_s256_of_verifier(self) -> None:
          import base64
          import hashlib

          ch = generate_pkce()
          digest = hashlib.sha256(ch.verifier.encode("ascii")).digest()
          expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
          assert ch.challenge == expected
          assert ch.method == "S256"

      def test_each_call_produces_a_new_verifier(self) -> None:
          assert generate_pkce().verifier != generate_pkce().verifier


  class TestOAuthStatePersistence:
      async def test_persist_and_consume_roundtrip(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          ch = generate_pkce()
          await persist_oauth_state(
              db_session,
              fake_clock,
              account_id=account.id,
              broker="saxo",
              state="state-value-xyz",
              code_verifier=ch.verifier,
              ttl=timedelta(minutes=10),
          )

          consumed = await consume_oauth_state(db_session, state="state-value-xyz")
          assert consumed is not None
          assert consumed.account_id == account.id
          assert consumed.code_verifier == ch.verifier

          # consumed once = gone
          assert await consume_oauth_state(db_session, state="state-value-xyz") is None

      async def test_consume_rejects_expired_state(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          await persist_oauth_state(
              db_session,
              fake_clock,
              account_id=account.id,
              broker="saxo",
              state="exp",
              code_verifier="v",
              ttl=timedelta(minutes=1),
          )
          fake_clock.advance(hours=1)
          # Expired -> treated as absent
          assert await consume_oauth_state(db_session, state="exp") is None
  ```

  > Note: `create_account` currently takes `name` only. Task 8/9 add `account_type` to the schema (it already exists per the Explore report — string with values `paper|sim|live`). If `create_account` doesn't accept `account_type`, extend its signature in this task (one-line change), keeping a default of `paper` for backward compat. Mirror the existing signature.

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_oauth.py -v`
  Expected: import error / `ModuleNotFoundError`.

- [ ] **Step 3: Implement the PKCE generator + state persistence**

  Create `engine/src/snapd_invest/broker/saxo_oauth.py`:

  ```python
  """Saxo OAuth — PKCE state machine, token exchange, refresh, persistence.

  Public surface used by `saxo.py` and the FastAPI routes in `api.py`:

  - `generate_pkce()` — fresh verifier/challenge pair.
  - `persist_oauth_state(...)` / `consume_oauth_state(...)` — the
    server-side state store for in-flight handshakes.
  - `exchange_code_for_tokens(...)` — POSTs to Saxo's /token, encrypts + persists.
  - `get_active_access_token(...)` — returns a valid access token, refreshing
    proactively if it expires within 60 seconds.
  - Endpoint constants: `SIM_AUTHORIZE_URL`, `SIM_TOKEN_URL`.

  All tokens are encrypted at rest via `Cipher`. Plaintext never persists.
  """

  from __future__ import annotations

  import base64
  import hashlib
  import secrets
  from dataclasses import dataclass
  from datetime import timedelta
  from typing import TYPE_CHECKING

  from sqlalchemy import select

  from snapd_invest.models import OAuthState, new_id

  if TYPE_CHECKING:
      from sqlalchemy.ext.asyncio import AsyncSession

      from snapd_invest.clock import Clock

  SIM_AUTHORIZE_URL = "https://sim.logonvalidation.net/authorize"
  SIM_TOKEN_URL = "https://sim.logonvalidation.net/token"


  @dataclass(slots=True, frozen=True)
  class PkceChallenge:
      verifier: str
      challenge: str
      method: str = "S256"


  def generate_pkce() -> PkceChallenge:
      """Generate a verifier (RFC 7636 §4.1) and its S256 challenge."""
      verifier = secrets.token_urlsafe(64)  # 64 bytes -> 86 chars; within [43, 128]
      digest = hashlib.sha256(verifier.encode("ascii")).digest()
      challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
      return PkceChallenge(verifier=verifier, challenge=challenge)


  async def persist_oauth_state(
      session: AsyncSession,
      clock: Clock,
      *,
      account_id: str,
      broker: str,
      state: str,
      code_verifier: str,
      ttl: timedelta = timedelta(minutes=10),
  ) -> None:
      """Persist an in-flight handshake. The `state` must be unique."""
      now = clock.now()
      row = OAuthState(
          id=new_id(),
          account_id=account_id,
          broker=broker,
          state=state,
          code_verifier=code_verifier,
          created_at=now,
          expires_at=now + ttl,
      )
      session.add(row)
      await session.flush()


  async def consume_oauth_state(session: AsyncSession, *, state: str) -> OAuthState | None:
      """Look up an OAuthState by `state` and delete it.

      Returns the row if found and unexpired (by `expires_at`); otherwise
      `None`. Single-use: a second call with the same state returns `None`.
      """
      from snapd_invest.clock import SystemClock  # local import; clock injection refactor in T-001-B

      row = (
          await session.execute(select(OAuthState).where(OAuthState.state == state))
      ).scalar_one_or_none()
      if row is None:
          return None
      # Delete first, then check expiry; either way the row is consumed.
      await session.delete(row)
      await session.flush()
      if row.expires_at < SystemClock().now():
          return None
      return row
  ```

  > **Open detail:** `consume_oauth_state` reads `SystemClock` inline because it doesn't accept a `clock` parameter. The test passes a `FakeClock`; that test will fail at the expiry check until the function takes a clock. Add a `clock` parameter to `consume_oauth_state` and pass it through from the test. Adjust the implementation to:
  >
  > ```python
  > async def consume_oauth_state(
  >     session: AsyncSession, clock: Clock, *, state: str
  > ) -> OAuthState | None:
  >     ...
  >     if row.expires_at < clock.now():
  >         return None
  >     return row
  > ```
  >
  > Update the test calls accordingly.

- [ ] **Step 4: Run tests, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_oauth.py -v`
  Expected: 5 passed.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/broker/saxo_oauth.py engine/tests/unit/test_saxo_oauth.py engine/src/snapd_invest/portfolio.py
  git commit -m "feat(engine): PKCE generator + oauth_state persistence in saxo_oauth"
  ```

  (`portfolio.py` only included if `create_account` needed the `account_type` parameter; otherwise omit.)

---

### Task 11: `saxo_oauth.py` — token exchange (POST /token, happy path)

**Files:**
- Modify: `engine/src/snapd_invest/broker/saxo_oauth.py`
- Modify: `engine/tests/unit/test_saxo_oauth.py`

- [ ] **Step 1: Write the failing test**

  Append to `engine/tests/unit/test_saxo_oauth.py`:

  ```python
  import httpx
  import respx

  from snapd_invest.broker.saxo_oauth import (
      SIM_TOKEN_URL,
      TokenSet,
      exchange_code_for_tokens,
  )


  class TestExchangeCodeForTokens:
      @respx.mock
      async def test_happy_path_returns_tokens(self, fake_clock) -> None:
          route = respx.post(SIM_TOKEN_URL).mock(
              return_value=httpx.Response(
                  200,
                  json={
                      "access_token": "access-abc",
                      "refresh_token": "refresh-xyz",
                      "expires_in": 1200,                      # 20 minutes
                      "refresh_token_expires_in": 86400,       # 24 hours
                      "token_type": "Bearer",
                  },
              )
          )

          async with httpx.AsyncClient() as client:
              tokens = await exchange_code_for_tokens(
                  client,
                  fake_clock,
                  client_id="client-123",
                  redirect_uri="http://localhost:8000/cb",
                  code="auth-code-abc",
                  code_verifier="v" * 64,
              )

          assert route.called
          body = dict(route.calls.last.request.read().decode().split("&"))  # naive parse for assertions
          # The body should contain the required PKCE fields
          raw_body = route.calls.last.request.read().decode()
          assert "grant_type=authorization_code" in raw_body
          assert "code=auth-code-abc" in raw_body
          assert f"code_verifier={'v' * 64}" in raw_body
          assert "client_id=client-123" in raw_body
          assert "redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fcb" in raw_body

          assert isinstance(tokens, TokenSet)
          assert tokens.access_token == "access-abc"
          assert tokens.refresh_token == "refresh-xyz"
          # 20 minutes from FakeClock.now()
          assert (tokens.access_expires_at - fake_clock.now()).total_seconds() == 1200
          assert (tokens.refresh_expires_at - fake_clock.now()).total_seconds() == 86400

      @respx.mock
      async def test_raises_auth_error_on_4xx(self, fake_clock) -> None:
          from snapd_invest.broker import BrokerAuthError

          respx.post(SIM_TOKEN_URL).mock(
              return_value=httpx.Response(400, json={"error": "invalid_grant"})
          )

          async with httpx.AsyncClient() as client:
              with pytest.raises(BrokerAuthError, match="invalid_grant"):
                  await exchange_code_for_tokens(
                      client,
                      fake_clock,
                      client_id="x",
                      redirect_uri="http://localhost/cb",
                      code="bad",
                      code_verifier="v" * 64,
                  )
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_oauth.py::TestExchangeCodeForTokens -v`
  Expected: `ImportError: cannot import name 'TokenSet' from 'snapd_invest.broker.saxo_oauth'`.

- [ ] **Step 3: Implement `exchange_code_for_tokens`**

  Append to `engine/src/snapd_invest/broker/saxo_oauth.py`:

  ```python
  from datetime import datetime  # add to imports at the top of the file

  import httpx  # add to imports

  from snapd_invest.broker import BrokerAuthError  # add to imports


  @dataclass(slots=True, frozen=True)
  class TokenSet:
      access_token: str
      refresh_token: str
      access_expires_at: datetime
      refresh_expires_at: datetime


  async def exchange_code_for_tokens(
      client: httpx.AsyncClient,
      clock: Clock,
      *,
      client_id: str,
      redirect_uri: str,
      code: str,
      code_verifier: str,
  ) -> TokenSet:
      """Exchange an authorization code for an access + refresh token pair.

      Raises `BrokerAuthError` on any non-2xx response from Saxo.
      """
      response = await client.post(
          SIM_TOKEN_URL,
          data={
              "grant_type": "authorization_code",
              "code": code,
              "code_verifier": code_verifier,
              "client_id": client_id,
              "redirect_uri": redirect_uri,
          },
          headers={"Content-Type": "application/x-www-form-urlencoded"},
      )
      if response.status_code >= 400:
          raise BrokerAuthError(
              f"saxo token exchange failed: {response.status_code} {response.text[:200]}"
          )
      payload = response.json()
      now = clock.now()
      return TokenSet(
          access_token=payload["access_token"],
          refresh_token=payload["refresh_token"],
          access_expires_at=now + timedelta(seconds=int(payload["expires_in"])),
          refresh_expires_at=now + timedelta(seconds=int(payload["refresh_token_expires_in"])),
      )
  ```

- [ ] **Step 4: Run tests, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_oauth.py::TestExchangeCodeForTokens -v`
  Expected: 2 passed.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/broker/saxo_oauth.py engine/tests/unit/test_saxo_oauth.py
  git commit -m "feat(engine): saxo_oauth.exchange_code_for_tokens (POST /token)"
  ```

---

### Task 12: `saxo_oauth.py` — encrypted token persistence

**Files:**
- Modify: `engine/src/snapd_invest/broker/saxo_oauth.py`
- Modify: `engine/tests/unit/test_saxo_oauth.py`

- [ ] **Step 1: Write the failing test**

  Append to `engine/tests/unit/test_saxo_oauth.py`:

  ```python
  from cryptography.fernet import Fernet

  from snapd_invest.broker.saxo_oauth import load_tokens, store_tokens
  from snapd_invest.crypto import FernetCipher
  from snapd_invest.models import OAuthToken


  class TestTokenStore:
      async def test_store_then_load_roundtrip(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          tokens = TokenSet(
              access_token="access-abc",
              refresh_token="refresh-xyz",
              access_expires_at=fake_clock.now() + timedelta(seconds=1200),
              refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
          )

          await store_tokens(
              db_session, fake_clock, cipher,
              account_id=account.id, broker="saxo", tokens=tokens,
          )

          loaded = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
          assert loaded is not None
          assert loaded.access_token == "access-abc"
          assert loaded.refresh_token == "refresh-xyz"
          assert loaded.access_expires_at == tokens.access_expires_at

          # The DB row must contain ciphertext, not plaintext
          row = (
              await db_session.execute(
                  select(OAuthToken).where(OAuthToken.account_id == account.id)
              )
          ).scalar_one()
          assert "access-abc" not in row.access_token_encrypted
          assert "refresh-xyz" not in row.refresh_token_encrypted

      async def test_store_overwrites_existing_tokens_for_same_account_and_broker(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          first = TokenSet("a1", "r1", fake_clock.now() + timedelta(seconds=600), fake_clock.now() + timedelta(seconds=86400))
          second = TokenSet("a2", "r2", fake_clock.now() + timedelta(seconds=1200), fake_clock.now() + timedelta(seconds=86400))

          await store_tokens(db_session, fake_clock, cipher, account_id=account.id, broker="saxo", tokens=first)
          await store_tokens(db_session, fake_clock, cipher, account_id=account.id, broker="saxo", tokens=second)

          loaded = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
          assert loaded is not None
          assert loaded.access_token == "a2"

      async def test_load_returns_none_when_no_tokens(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          assert await load_tokens(db_session, cipher, account_id=account.id, broker="saxo") is None
  ```

  Add `from sqlalchemy import select` to test imports if not already present.

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_oauth.py::TestTokenStore -v`
  Expected: `ImportError: cannot import name 'load_tokens' from 'snapd_invest.broker.saxo_oauth'`.

- [ ] **Step 3: Implement `store_tokens` + `load_tokens`**

  Append to `engine/src/snapd_invest/broker/saxo_oauth.py`:

  ```python
  from snapd_invest.crypto import Cipher  # add to imports
  from snapd_invest.models import OAuthToken  # add to imports


  async def store_tokens(
      session: AsyncSession,
      clock: Clock,
      cipher: Cipher,
      *,
      account_id: str,
      broker: str,
      tokens: TokenSet,
  ) -> None:
      """Upsert encrypted tokens for `(account_id, broker)`."""
      now = clock.now()
      existing = (
          await session.execute(
              select(OAuthToken).where(
                  OAuthToken.account_id == account_id,
                  OAuthToken.broker == broker,
              )
          )
      ).scalar_one_or_none()

      if existing is None:
          row = OAuthToken(
              id=new_id(),
              account_id=account_id,
              broker=broker,
              access_token_encrypted=cipher.encrypt(tokens.access_token),
              refresh_token_encrypted=cipher.encrypt(tokens.refresh_token),
              access_expires_at=tokens.access_expires_at,
              refresh_expires_at=tokens.refresh_expires_at,
              created_at=now,
              updated_at=now,
          )
          session.add(row)
      else:
          existing.access_token_encrypted = cipher.encrypt(tokens.access_token)
          existing.refresh_token_encrypted = cipher.encrypt(tokens.refresh_token)
          existing.access_expires_at = tokens.access_expires_at
          existing.refresh_expires_at = tokens.refresh_expires_at
          existing.updated_at = now
      await session.flush()


  async def load_tokens(
      session: AsyncSession,
      cipher: Cipher,
      *,
      account_id: str,
      broker: str,
  ) -> TokenSet | None:
      """Load and decrypt tokens for `(account_id, broker)`, or `None`."""
      row = (
          await session.execute(
              select(OAuthToken).where(
                  OAuthToken.account_id == account_id,
                  OAuthToken.broker == broker,
              )
          )
      ).scalar_one_or_none()
      if row is None:
          return None
      return TokenSet(
          access_token=cipher.decrypt(row.access_token_encrypted),
          refresh_token=cipher.decrypt(row.refresh_token_encrypted),
          access_expires_at=row.access_expires_at,
          refresh_expires_at=row.refresh_expires_at,
      )
  ```

- [ ] **Step 4: Run tests, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_oauth.py::TestTokenStore -v`
  Expected: 3 passed.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/broker/saxo_oauth.py engine/tests/unit/test_saxo_oauth.py
  git commit -m "feat(engine): saxo_oauth.store_tokens + load_tokens with at-rest encryption"
  ```

---

### Task 13: `saxo_oauth.py` — refresh logic

**Files:**
- Modify: `engine/src/snapd_invest/broker/saxo_oauth.py`
- Modify: `engine/tests/unit/test_saxo_oauth.py`

- [ ] **Step 1: Write the failing tests**

  Append to `engine/tests/unit/test_saxo_oauth.py`:

  ```python
  from snapd_invest.broker.saxo_oauth import get_active_access_token, refresh_tokens


  class TestRefreshTokens:
      @respx.mock
      async def test_refresh_returns_new_tokens(self, fake_clock) -> None:
          respx.post(SIM_TOKEN_URL).mock(
              return_value=httpx.Response(
                  200,
                  json={
                      "access_token": "new-access",
                      "refresh_token": "new-refresh",
                      "expires_in": 1200,
                      "refresh_token_expires_in": 86400,
                      "token_type": "Bearer",
                  },
              )
          )

          async with httpx.AsyncClient() as client:
              new = await refresh_tokens(
                  client, fake_clock,
                  client_id="client-123",
                  refresh_token="old-refresh",
              )

          assert new.access_token == "new-access"
          assert new.refresh_token == "new-refresh"

      @respx.mock
      async def test_refresh_raises_auth_error_on_failure(self, fake_clock) -> None:
          from snapd_invest.broker import BrokerAuthError

          respx.post(SIM_TOKEN_URL).mock(
              return_value=httpx.Response(400, json={"error": "invalid_grant"})
          )
          async with httpx.AsyncClient() as client:
              with pytest.raises(BrokerAuthError, match="invalid_grant"):
                  await refresh_tokens(client, fake_clock, client_id="x", refresh_token="bad")


  class TestGetActiveAccessToken:
      @respx.mock
      async def test_returns_stored_token_when_not_near_expiry(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          await store_tokens(
              db_session, fake_clock, cipher,
              account_id=account.id, broker="saxo",
              tokens=TokenSet(
                  access_token="still-good",
                  refresh_token="r1",
                  access_expires_at=fake_clock.now() + timedelta(seconds=600),  # > 60s buffer
                  refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
              ),
          )

          async with httpx.AsyncClient() as client:
              token = await get_active_access_token(
                  db_session, fake_clock, client, cipher,
                  client_id="client-123", account_id=account.id, broker="saxo",
              )

          assert token == "still-good"

      @respx.mock
      async def test_refreshes_proactively_when_within_buffer(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          await store_tokens(
              db_session, fake_clock, cipher,
              account_id=account.id, broker="saxo",
              tokens=TokenSet(
                  access_token="almost-expired",
                  refresh_token="r1",
                  access_expires_at=fake_clock.now() + timedelta(seconds=30),  # < 60s buffer
                  refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
              ),
          )

          respx.post(SIM_TOKEN_URL).mock(
              return_value=httpx.Response(
                  200,
                  json={
                      "access_token": "fresh",
                      "refresh_token": "r2",
                      "expires_in": 1200,
                      "refresh_token_expires_in": 86400,
                      "token_type": "Bearer",
                  },
              )
          )

          async with httpx.AsyncClient() as client:
              token = await get_active_access_token(
                  db_session, fake_clock, client, cipher,
                  client_id="client-123", account_id=account.id, broker="saxo",
              )

          assert token == "fresh"
          # The new tokens must have been persisted
          loaded = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
          assert loaded is not None
          assert loaded.access_token == "fresh"

      async def test_raises_auth_error_when_no_tokens_stored(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          from snapd_invest.broker import BrokerAuthError

          cipher = FernetCipher(Fernet.generate_key())
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          async with httpx.AsyncClient() as client:
              with pytest.raises(BrokerAuthError, match="no stored tokens"):
                  await get_active_access_token(
                      db_session, fake_clock, client, cipher,
                      client_id="client-123", account_id=account.id, broker="saxo",
                  )
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_oauth.py::TestRefreshTokens tests/unit/test_saxo_oauth.py::TestGetActiveAccessToken -v`
  Expected: ImportError.

- [ ] **Step 3: Implement `refresh_tokens` + `get_active_access_token`**

  Append to `engine/src/snapd_invest/broker/saxo_oauth.py`:

  ```python
  REFRESH_BUFFER_SECONDS = 60


  async def refresh_tokens(
      client: httpx.AsyncClient,
      clock: Clock,
      *,
      client_id: str,
      refresh_token: str,
  ) -> TokenSet:
      """Use a refresh token to obtain a fresh `TokenSet`."""
      response = await client.post(
          SIM_TOKEN_URL,
          data={
              "grant_type": "refresh_token",
              "refresh_token": refresh_token,
              "client_id": client_id,
          },
          headers={"Content-Type": "application/x-www-form-urlencoded"},
      )
      if response.status_code >= 400:
          raise BrokerAuthError(
              f"saxo token refresh failed: {response.status_code} {response.text[:200]}"
          )
      payload = response.json()
      now = clock.now()
      return TokenSet(
          access_token=payload["access_token"],
          refresh_token=payload["refresh_token"],
          access_expires_at=now + timedelta(seconds=int(payload["expires_in"])),
          refresh_expires_at=now + timedelta(seconds=int(payload["refresh_token_expires_in"])),
      )


  async def get_active_access_token(
      session: AsyncSession,
      clock: Clock,
      client: httpx.AsyncClient,
      cipher: Cipher,
      *,
      client_id: str,
      account_id: str,
      broker: str,
  ) -> str:
      """Return a usable access token for `(account_id, broker)`.

      Refreshes proactively if the stored token expires within
      `REFRESH_BUFFER_SECONDS`. Raises `BrokerAuthError` if no tokens are
      stored, or if a refresh attempt fails.
      """
      stored = await load_tokens(session, cipher, account_id=account_id, broker=broker)
      if stored is None:
          raise BrokerAuthError(
              f"no stored tokens for account_id={account_id} broker={broker}; "
              f"complete OAuth first via /v1/oauth/saxo/start"
          )

      if (stored.access_expires_at - clock.now()).total_seconds() > REFRESH_BUFFER_SECONDS:
          return stored.access_token

      fresh = await refresh_tokens(
          client, clock, client_id=client_id, refresh_token=stored.refresh_token,
      )
      await store_tokens(
          session, clock, cipher,
          account_id=account_id, broker=broker, tokens=fresh,
      )
      return fresh.access_token
  ```

- [ ] **Step 4: Run tests, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_oauth.py -v`
  Expected: all `TestRefreshTokens` + `TestGetActiveAccessToken` tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/broker/saxo_oauth.py engine/tests/unit/test_saxo_oauth.py
  git commit -m "feat(engine): saxo_oauth.get_active_access_token with proactive refresh"
  ```

---

### Task 14: `SaxoBroker.get_account()` + reactive 401 refresh

**Files:**
- Create: `engine/src/snapd_invest/broker/saxo.py`
- Create: `engine/tests/unit/test_saxo_broker.py`
- Modify: `engine/src/snapd_invest/broker/__init__.py` (re-export SaxoBroker)

- [ ] **Step 1: Write the failing test**

  Create `engine/tests/unit/test_saxo_broker.py`:

  ```python
  """Tests for `snapd_invest.broker.saxo.SaxoBroker.get_account`."""

  from __future__ import annotations

  from datetime import timedelta
  from decimal import Decimal
  from typing import TYPE_CHECKING

  import httpx
  import pytest
  import respx
  from cryptography.fernet import Fernet

  from snapd_invest.broker import BrokerAuthError, BrokerHttpError
  from snapd_invest.broker.saxo import SAXO_SIM_API_BASE, SaxoBroker
  from snapd_invest.broker.saxo_oauth import SIM_TOKEN_URL, TokenSet, store_tokens
  from snapd_invest.crypto import FernetCipher
  from snapd_invest.portfolio import create_account

  if TYPE_CHECKING:
      from sqlalchemy.ext.asyncio import AsyncSession

      from snapd_invest.clock import FakeClock


  ACCOUNTS_ME_URL = f"{SAXO_SIM_API_BASE}/port/v1/users/me"


  async def _seed_tokens(db_session, fake_clock, cipher) -> str:
      account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
      await store_tokens(
          db_session, fake_clock, cipher,
          account_id=account.id, broker="saxo",
          tokens=TokenSet(
              access_token="good-token",
              refresh_token="refresh-1",
              access_expires_at=fake_clock.now() + timedelta(seconds=600),
              refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
          ),
      )
      return account.id


  class TestSaxoBrokerGetAccount:
      @respx.mock
      async def test_happy_path(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account_id = await _seed_tokens(db_session, fake_clock, cipher)
          respx.get(ACCOUNTS_ME_URL).mock(
              return_value=httpx.Response(
                  200,
                  json={
                      "ClientKey": "client-abc",
                      "UserKey": "user-xyz",
                      "Name": "Torben",
                  },
              )
          )

          async with httpx.AsyncClient() as client:
              broker = SaxoBroker(
                  client=client, clock=fake_clock, cipher=cipher,
                  client_id="client-123", account_id=account_id,
              )
              info = await broker.get_account(db_session)

          assert info.client_key == "client-abc"
          assert info.user_key == "user-xyz"
          assert info.name == "Torben"

      @respx.mock
      async def test_reactive_refresh_on_401_then_succeeds(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account_id = await _seed_tokens(db_session, fake_clock, cipher)

          # First call returns 401, second (after refresh) returns 200
          respx.get(ACCOUNTS_ME_URL).mock(
              side_effect=[
                  httpx.Response(401, json={"error": "expired"}),
                  httpx.Response(200, json={"ClientKey": "ck", "UserKey": "uk", "Name": "x"}),
              ]
          )
          respx.post(SIM_TOKEN_URL).mock(
              return_value=httpx.Response(
                  200,
                  json={
                      "access_token": "refreshed",
                      "refresh_token": "r2",
                      "expires_in": 1200,
                      "refresh_token_expires_in": 86400,
                      "token_type": "Bearer",
                  },
              )
          )

          async with httpx.AsyncClient() as client:
              broker = SaxoBroker(
                  client=client, clock=fake_clock, cipher=cipher,
                  client_id="client-123", account_id=account_id,
              )
              info = await broker.get_account(db_session)

          assert info.client_key == "ck"
          # The refreshed token must be the one stored now
          from snapd_invest.broker.saxo_oauth import load_tokens

          stored = await load_tokens(db_session, cipher, account_id=account_id, broker="saxo")
          assert stored is not None
          assert stored.access_token == "refreshed"

      @respx.mock
      async def test_401_then_refresh_fails_raises_auth_error(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account_id = await _seed_tokens(db_session, fake_clock, cipher)
          respx.get(ACCOUNTS_ME_URL).mock(return_value=httpx.Response(401))
          respx.post(SIM_TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))

          async with httpx.AsyncClient() as client:
              broker = SaxoBroker(
                  client=client, clock=fake_clock, cipher=cipher,
                  client_id="client-123", account_id=account_id,
              )
              with pytest.raises(BrokerAuthError):
                  await broker.get_account(db_session)

      @respx.mock
      async def test_500_raises_http_error(
          self, db_session: AsyncSession, fake_clock: FakeClock
      ) -> None:
          cipher = FernetCipher(Fernet.generate_key())
          account_id = await _seed_tokens(db_session, fake_clock, cipher)
          respx.get(ACCOUNTS_ME_URL).mock(return_value=httpx.Response(503, text="upstream"))

          async with httpx.AsyncClient() as client:
              broker = SaxoBroker(
                  client=client, clock=fake_clock, cipher=cipher,
                  client_id="client-123", account_id=account_id,
              )
              with pytest.raises(BrokerHttpError) as exc_info:
                  await broker.get_account(db_session)
          assert exc_info.value.status_code == 503
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_broker.py -v`
  Expected: `ModuleNotFoundError: No module named 'snapd_invest.broker.saxo'`.

- [ ] **Step 3: Implement `SaxoBroker`**

  Create `engine/src/snapd_invest/broker/saxo.py`:

  ```python
  """SaxoBroker — IBroker implementation against Saxo SIM.

  T-001-A scope: `get_account()` only. Other methods (place_order, etc.)
  arrive in T-001-B.

  The broker accepts an `httpx.AsyncClient` (shared at the engine level so
  connection pooling works), a `Clock`, a `Cipher`, the OAuth `client_id`,
  and the `account_id` it represents. Tokens are loaded from `oauth_tokens`
  on every call; this keeps the broker stateless and concurrency-safe.
  """

  from __future__ import annotations

  from dataclasses import dataclass
  from typing import TYPE_CHECKING

  from snapd_invest.broker import BrokerAuthError, BrokerHttpError, BrokerTimeoutError
  from snapd_invest.broker.saxo_oauth import get_active_access_token, refresh_tokens, store_tokens

  if TYPE_CHECKING:
      import httpx
      from sqlalchemy.ext.asyncio import AsyncSession

      from snapd_invest.clock import Clock
      from snapd_invest.crypto import Cipher

  SAXO_SIM_API_BASE = "https://gateway.saxobank.com/sim/openapi"


  @dataclass(slots=True, frozen=True)
  class SaxoAccountInfo:
      """Minimal Saxo `/port/v1/users/me` response (we ignore fields we don't yet use)."""

      client_key: str
      user_key: str
      name: str


  class SaxoBroker:
      """SaxoBroker — IBroker against Saxo SIM. T-001-A: get_account only."""

      venue_name = "saxo-sim"

      def __init__(
          self,
          *,
          client: httpx.AsyncClient,
          clock: Clock,
          cipher: Cipher,
          client_id: str,
          account_id: str,
      ) -> None:
          self._client = client
          self._clock = clock
          self._cipher = cipher
          self._client_id = client_id
          self._account_id = account_id

      async def get_account(self, session: AsyncSession) -> SaxoAccountInfo:
          """Fetch `/port/v1/users/me` for the SaxoBroker's account.

          Performs a reactive refresh once if the call comes back 401.
          """
          payload = await self._authed_get(session, "/port/v1/users/me")
          return SaxoAccountInfo(
              client_key=payload["ClientKey"],
              user_key=payload["UserKey"],
              name=payload.get("Name", ""),
          )

      async def _authed_get(self, session: AsyncSession, path: str) -> dict:  # type: ignore[type-arg]
          token = await get_active_access_token(
              session, self._clock, self._client, self._cipher,
              client_id=self._client_id, account_id=self._account_id, broker="saxo",
          )
          try:
              response = await self._client.get(
                  f"{SAXO_SIM_API_BASE}{path}",
                  headers={"Authorization": f"Bearer {token}"},
              )
          except (TimeoutError, httpx.TimeoutException) as exc:
              raise BrokerTimeoutError(str(exc)) from exc

          if response.status_code == 401:
              # Reactive refresh attempt
              from snapd_invest.broker.saxo_oauth import load_tokens

              stored = await load_tokens(
                  session, self._cipher, account_id=self._account_id, broker="saxo",
              )
              if stored is None:
                  raise BrokerAuthError("401 from Saxo and no stored tokens to refresh")
              fresh = await refresh_tokens(
                  self._client, self._clock,
                  client_id=self._client_id, refresh_token=stored.refresh_token,
              )
              await store_tokens(
                  session, self._clock, self._cipher,
                  account_id=self._account_id, broker="saxo", tokens=fresh,
              )
              # Retry once with the new token
              response = await self._client.get(
                  f"{SAXO_SIM_API_BASE}{path}",
                  headers={"Authorization": f"Bearer {fresh.access_token}"},
              )
              if response.status_code == 401:
                  raise BrokerAuthError("401 persisted after refresh")

          if response.status_code >= 400:
              raise BrokerHttpError(status_code=response.status_code, body=response.text)

          return response.json()
  ```

  Add `import httpx` to the runtime imports (not just TYPE_CHECKING, because `httpx.TimeoutException` is used in `except`).

- [ ] **Step 4: Re-export `SaxoBroker` from the package**

  Update `engine/src/snapd_invest/broker/__init__.py`:
  - Add `from snapd_invest.broker.saxo import SaxoAccountInfo, SaxoBroker` after the `PaperBroker` import.
  - Add `"SaxoAccountInfo"` and `"SaxoBroker"` to `__all__`.

- [ ] **Step 5: Run tests, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_saxo_broker.py -v`
  Expected: 4 passed.

- [ ] **Step 6: Type + lint**

  Run: `cd engine && uv run mypy src && uv run ruff check`
  Expected: clean.

- [ ] **Step 7: Commit**

  ```bash
  git add engine/src/snapd_invest/broker/saxo.py engine/src/snapd_invest/broker/__init__.py engine/tests/unit/test_saxo_broker.py
  git commit -m "feat(engine): SaxoBroker.get_account with reactive 401 refresh"
  ```

---

### Task 15: Broker selection by `Account.account_type` (`broker_for`)

**Files:**
- Modify: `engine/src/snapd_invest/broker/__init__.py`
- Modify: `engine/src/snapd_invest/execution.py`
- Modify: `engine/src/snapd_invest/api.py` (lifespan wires `SaxoBroker` factory)
- Modify: `engine/tests/unit/test_execution.py` (if any test wires a hard-coded `PaperBroker`)
- Modify: `engine/tests/unit/test_pipeline.py` (same)

- [ ] **Step 1: Define a `broker_for` callable in `broker/__init__.py`**

  Append:

  ```python
  from collections.abc import Callable

  BrokerFactory = Callable[["Account"], IBroker]
  ```

  Move the `from snapd_invest.models import Account` import out of `TYPE_CHECKING` (or add `Account` quoted in the type alias).

- [ ] **Step 2: Refactor `execution.py` to accept a factory rather than a broker**

  Change `execute_signal` / `execute_signals` signatures from `broker: IBroker` to `broker_factory: BrokerFactory`. Inside, build the broker via `broker = broker_factory(account)` after loading the account.

  Update all callers:
  - `pipeline.run_microtrader_once` — accepts `broker_factory` instead of `broker`, passes it through.
  - `recommendation.approve_and_execute` — same.
  - API routes and scheduler closures — pass the factory.

- [ ] **Step 3: Wire the factory in `api.py`'s lifespan**

  In the lifespan, after constructing `PaperBroker` and the shared `httpx.AsyncClient`:

  ```python
  def _broker_factory(account: Account) -> IBroker:
      if account.account_type == "paper":
          return paper_broker
      if account.account_type == "sim":
          if settings.saxo_client_id is None or settings.encryption_key is None:
              raise BrokerAuthError(
                  "SIM account requires SNAPDINVEST_SAXO_CLIENT_ID and SNAPDINVEST_ENCRYPTION_KEY"
              )
          return SaxoBroker(
              client=saxo_http_client,
              clock=clock,
              cipher=FernetCipher(settings.encryption_key.encode("ascii")),
              client_id=settings.saxo_client_id,
              account_id=account.id,
          )
      raise BrokerAuthError(f"unsupported account_type: {account.account_type}")

  app.state.broker_factory = _broker_factory
  ```

  Add a `broker_factory_dep` dependency function mirroring the existing `broker_dep` pattern. Replace `broker_dep` uses with `broker_factory_dep` in all route handlers and scheduler closures.

- [ ] **Step 4: Update unit tests that wire `PaperBroker` directly**

  In `test_execution.py`, `test_pipeline.py`, `test_recommendation.py`: change `execute_signal(..., broker, ...)` to `execute_signal(..., lambda _account: broker, ...)`. Tests don't need real broker selection; a constant lambda is enough.

- [ ] **Step 5: Run all tests**

  Run: `cd engine && uv run pytest`
  Expected: all pass (count slightly higher than baseline due to test changes).

- [ ] **Step 6: Type + lint**

  Run: `cd engine && uv run mypy src && uv run ruff check`
  Expected: clean.

- [ ] **Step 7: Commit**

  ```bash
  git add engine/src/snapd_invest/broker/__init__.py engine/src/snapd_invest/execution.py engine/src/snapd_invest/pipeline.py engine/src/snapd_invest/recommendation.py engine/src/snapd_invest/api.py engine/tests/
  git commit -m "feat(engine): broker selection by Account.account_type via broker_factory"
  ```

---

### Task 16: Engine route `POST /v1/oauth/saxo/start`

**Files:**
- Modify: `engine/src/snapd_invest/api.py`
- Modify: `engine/tests/unit/test_api_health.py` (or a new `test_api_oauth.py`)
- Create: `engine/tests/unit/test_api_oauth.py` (preferred — keep health tests separate)

- [ ] **Step 1: Write the failing test**

  Create `engine/tests/unit/test_api_oauth.py`:

  ```python
  """Tests for /v1/oauth/saxo/* routes."""

  from __future__ import annotations

  from typing import TYPE_CHECKING
  from urllib.parse import parse_qs, urlparse

  import pytest
  from httpx import ASGITransport, AsyncClient
  from sqlalchemy import select

  from snapd_invest.api import build_app
  from snapd_invest.models import OAuthState
  from snapd_invest.portfolio import create_account

  if TYPE_CHECKING:
      from sqlalchemy.ext.asyncio import AsyncSession

      from snapd_invest.clock import FakeClock
      from snapd_invest.config import Settings


  @pytest.fixture
  def sim_settings(test_settings: Settings) -> Settings:
      return test_settings.model_copy(update={
          "saxo_env": "sim",
          "saxo_client_id": "client-123",
          "saxo_redirect_uri": "http://localhost:8000/v1/oauth/saxo/callback",
          "encryption_key": "k" * 44,
      })


  class TestOAuthStart:
      async def test_returns_authorize_url_and_persists_state(
          self,
          db_session: AsyncSession,
          fake_clock: FakeClock,
          sim_settings: Settings,
      ) -> None:
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          app = build_app(settings=sim_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.post(f"/v1/oauth/saxo/start?account_id={account.id}")
          assert resp.status_code == 200
          body = resp.json()
          url = urlparse(body["authorize_url"])
          assert url.netloc == "sim.logonvalidation.net"
          assert url.path == "/authorize"
          q = parse_qs(url.query)
          assert q["client_id"] == ["client-123"]
          assert q["redirect_uri"] == ["http://localhost:8000/v1/oauth/saxo/callback"]
          assert q["response_type"] == ["code"]
          assert q["code_challenge_method"] == ["S256"]
          assert "code_challenge" in q
          assert "state" in q

          # State persisted, verifier matches what the challenge derives from
          row = (
              await db_session.execute(select(OAuthState).where(OAuthState.state == q["state"][0]))
          ).scalar_one()
          assert row.account_id == account.id
          assert row.broker == "saxo"

      async def test_rejects_unknown_account(
          self,
          fake_clock: FakeClock,
          sim_settings: Settings,
      ) -> None:
          app = build_app(settings=sim_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.post("/v1/oauth/saxo/start?account_id=does-not-exist")
          assert resp.status_code == 404

      async def test_requires_saxo_client_id_configured(
          self,
          db_session: AsyncSession,
          fake_clock: FakeClock,
          test_settings: Settings,  # without saxo_client_id
      ) -> None:
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          app = build_app(settings=test_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.post(f"/v1/oauth/saxo/start?account_id={account.id}")
          assert resp.status_code == 503  # configuration not ready
  ```

  > **Fixture note:** `test_settings` should already exist in `conftest.py` as the standard Settings. If `build_app` doesn't accept `settings` and `clock` as parameters, mirror the pattern used in `test_api_health.py`.

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_api_oauth.py::TestOAuthStart -v`
  Expected: 404 or route-not-found errors.

- [ ] **Step 3: Implement the route**

  In `engine/src/snapd_invest/api.py`, add a new route inside the route-registration block (alongside existing routes):

  ```python
  from secrets import token_urlsafe

  from snapd_invest.broker.saxo_oauth import (
      SIM_AUTHORIZE_URL,
      generate_pkce,
      persist_oauth_state,
  )


  class AuthorizeUrlResponse(BaseModel):
      authorize_url: str
      state: str


  @app.post("/v1/oauth/saxo/start", response_model=AuthorizeUrlResponse, tags=["oauth"])
  async def start_saxo_oauth(
      account_id: str,
      session: Annotated[AsyncSession, Depends(session_dep)],
      clock: Annotated[Clock, Depends(clock_dep)],
      settings: Annotated[Settings, Depends(settings_dep)],
  ) -> AuthorizeUrlResponse:
      if settings.saxo_client_id is None or settings.saxo_redirect_uri is None:
          raise HTTPException(status_code=503, detail="SAXO_CLIENT_ID/SAXO_REDIRECT_URI not configured")

      account = (
          await session.execute(select(Account).where(Account.id == account_id))
      ).scalar_one_or_none()
      if account is None:
          raise HTTPException(status_code=404, detail=f"account_id={account_id} not found")

      pkce = generate_pkce()
      state = token_urlsafe(32)
      await persist_oauth_state(
          session, clock,
          account_id=account.id, broker="saxo",
          state=state, code_verifier=pkce.verifier,
      )

      url = (
          f"{SIM_AUTHORIZE_URL}"
          f"?response_type=code"
          f"&client_id={settings.saxo_client_id}"
          f"&redirect_uri={settings.saxo_redirect_uri}"
          f"&state={state}"
          f"&code_challenge={pkce.challenge}"
          f"&code_challenge_method=S256"
      )
      # Strict URL encoding for the redirect_uri (httpx-style)
      from urllib.parse import urlencode
      query = urlencode({
          "response_type": "code",
          "client_id": settings.saxo_client_id,
          "redirect_uri": settings.saxo_redirect_uri,
          "state": state,
          "code_challenge": pkce.challenge,
          "code_challenge_method": "S256",
      })
      url = f"{SIM_AUTHORIZE_URL}?{query}"

      return AuthorizeUrlResponse(authorize_url=url, state=state)
  ```

  Make sure `settings_dep` exists; if not, follow the pattern used for `clock_dep` / `session_dep` to add it.

- [ ] **Step 4: Run, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_api_oauth.py::TestOAuthStart -v`
  Expected: 3 passed.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/api.py engine/tests/unit/test_api_oauth.py
  git commit -m "feat(engine): POST /v1/oauth/saxo/start (PKCE + state persistence)"
  ```

---

### Task 17: Engine route `GET /v1/oauth/saxo/callback`

**Files:**
- Modify: `engine/src/snapd_invest/api.py`
- Modify: `engine/tests/unit/test_api_oauth.py`

- [ ] **Step 1: Write failing tests**

  Append to `test_api_oauth.py`:

  ```python
  import respx
  import httpx
  from snapd_invest.broker.saxo_oauth import SIM_TOKEN_URL, load_tokens, persist_oauth_state, generate_pkce
  from snapd_invest.crypto import FernetCipher


  class TestOAuthCallback:
      @respx.mock
      async def test_happy_path_persists_tokens(
          self, db_session, fake_clock, sim_settings,
      ) -> None:
          # Pre-seed an oauth_state row so the callback has something to consume
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          pkce = generate_pkce()
          await persist_oauth_state(
              db_session, fake_clock,
              account_id=account.id, broker="saxo",
              state="state-1", code_verifier=pkce.verifier,
          )
          await db_session.commit()

          respx.post(SIM_TOKEN_URL).mock(return_value=httpx.Response(
              200,
              json={
                  "access_token": "a1",
                  "refresh_token": "r1",
                  "expires_in": 1200,
                  "refresh_token_expires_in": 86400,
                  "token_type": "Bearer",
              },
          ))

          app = build_app(settings=sim_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.get("/v1/oauth/saxo/callback?code=auth-code&state=state-1")
          assert resp.status_code == 200
          assert "auth complete" in resp.text.lower()

          # Tokens persisted, encrypted
          cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
          loaded = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
          assert loaded is not None
          assert loaded.access_token == "a1"

      async def test_unknown_state_returns_400(
          self, fake_clock, sim_settings,
      ) -> None:
          app = build_app(settings=sim_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.get("/v1/oauth/saxo/callback?code=x&state=nonexistent")
          assert resp.status_code == 400
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_api_oauth.py::TestOAuthCallback -v`
  Expected: 404 (route not registered) or similar.

- [ ] **Step 3: Implement the callback route**

  In `api.py`, add:

  ```python
  from fastapi.responses import HTMLResponse

  from snapd_invest.broker.saxo_oauth import (
      consume_oauth_state,
      exchange_code_for_tokens,
      store_tokens,
  )
  from snapd_invest.crypto import FernetCipher


  _CALLBACK_HTML = """<!doctype html>
  <html><body style="font-family: system-ui">
  <h1>Auth complete</h1>
  <p>You can close this tab.</p>
  </body></html>"""


  @app.get("/v1/oauth/saxo/callback", response_class=HTMLResponse, tags=["oauth"])
  async def saxo_oauth_callback(
      code: str,
      state: str,
      session: Annotated[AsyncSession, Depends(session_dep)],
      clock: Annotated[Clock, Depends(clock_dep)],
      settings: Annotated[Settings, Depends(settings_dep)],
      saxo_http_client: Annotated[httpx.AsyncClient, Depends(saxo_http_client_dep)],
  ) -> HTMLResponse:
      if settings.saxo_client_id is None or settings.saxo_redirect_uri is None or settings.encryption_key is None:
          raise HTTPException(status_code=503, detail="saxo configuration incomplete")

      consumed = await consume_oauth_state(session, clock, state=state)
      if consumed is None:
          raise HTTPException(status_code=400, detail="unknown or expired state")

      tokens = await exchange_code_for_tokens(
          saxo_http_client, clock,
          client_id=settings.saxo_client_id,
          redirect_uri=settings.saxo_redirect_uri,
          code=code, code_verifier=consumed.code_verifier,
      )
      cipher = FernetCipher(settings.encryption_key.encode("ascii"))
      await store_tokens(
          session, clock, cipher,
          account_id=consumed.account_id, broker=consumed.broker, tokens=tokens,
      )
      return HTMLResponse(_CALLBACK_HTML)
  ```

  In the lifespan, add a shared `saxo_http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))` and a `saxo_http_client_dep` dependency function. Close the client in lifespan teardown.

- [ ] **Step 4: Run tests, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_api_oauth.py -v`
  Expected: all in `TestOAuthCallback` pass; previous `TestOAuthStart` still pass.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/api.py engine/tests/unit/test_api_oauth.py
  git commit -m "feat(engine): GET /v1/oauth/saxo/callback exchanges code + persists tokens"
  ```

---

### Task 18: Engine route `GET /v1/oauth/saxo/status`

**Files:**
- Modify: `engine/src/snapd_invest/api.py`
- Modify: `engine/tests/unit/test_api_oauth.py`

- [ ] **Step 1: Write failing tests**

  Append to `test_api_oauth.py`:

  ```python
  class TestOAuthStatus:
      async def test_reports_unauthenticated_when_no_tokens(
          self, db_session, fake_clock, sim_settings,
      ) -> None:
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          await db_session.commit()
          app = build_app(settings=sim_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.get(f"/v1/oauth/saxo/status?account_id={account.id}")
          assert resp.status_code == 200
          assert resp.json() == {"account_id": account.id, "broker": "saxo", "authenticated": False}

      async def test_reports_authenticated_when_tokens_present(
          self, db_session, fake_clock, sim_settings,
      ) -> None:
          cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          from datetime import timedelta
          from snapd_invest.broker.saxo_oauth import TokenSet, store_tokens
          await store_tokens(
              db_session, fake_clock, cipher,
              account_id=account.id, broker="saxo",
              tokens=TokenSet(
                  access_token="a", refresh_token="r",
                  access_expires_at=fake_clock.now() + timedelta(seconds=1200),
                  refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
              ),
          )
          await db_session.commit()

          app = build_app(settings=sim_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.get(f"/v1/oauth/saxo/status?account_id={account.id}")
          assert resp.status_code == 200
          body = resp.json()
          assert body["authenticated"] is True
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_api_oauth.py::TestOAuthStatus -v`
  Expected: 404 / route not found.

- [ ] **Step 3: Implement the route**

  In `api.py`, add:

  ```python
  class OAuthStatusResponse(BaseModel):
      account_id: str
      broker: str
      authenticated: bool


  @app.get("/v1/oauth/saxo/status", response_model=OAuthStatusResponse, tags=["oauth"])
  async def saxo_oauth_status(
      account_id: str,
      session: Annotated[AsyncSession, Depends(session_dep)],
      settings: Annotated[Settings, Depends(settings_dep)],
  ) -> OAuthStatusResponse:
      from snapd_invest.broker.saxo_oauth import load_tokens
      from snapd_invest.crypto import FernetCipher

      if settings.encryption_key is None:
          # Without a key we cannot even attempt to load — report unauthenticated.
          return OAuthStatusResponse(account_id=account_id, broker="saxo", authenticated=False)

      cipher = FernetCipher(settings.encryption_key.encode("ascii"))
      loaded = await load_tokens(session, cipher, account_id=account_id, broker="saxo")
      return OAuthStatusResponse(
          account_id=account_id, broker="saxo", authenticated=loaded is not None,
      )
  ```

- [ ] **Step 4: Run, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_api_oauth.py -v`
  Expected: all tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/api.py engine/tests/unit/test_api_oauth.py
  git commit -m "feat(engine): GET /v1/oauth/saxo/status — auth liveness check"
  ```

---

### Task 19: Engine route `GET /v1/accounts/{id}` (delegates to `broker_factory(account).get_account()`)

**Files:**
- Modify: `engine/src/snapd_invest/api.py`
- Modify: `engine/tests/unit/test_api_oauth.py` (or new test file)

- [ ] **Step 1: Write the failing test**

  Append to `test_api_oauth.py`:

  ```python
  class TestGetAccount:
      @respx.mock
      async def test_proxies_through_broker_for_sim_accounts(
          self, db_session, fake_clock, sim_settings,
      ) -> None:
          from datetime import timedelta
          from snapd_invest.broker.saxo_oauth import TokenSet, store_tokens
          from snapd_invest.broker.saxo import SAXO_SIM_API_BASE

          cipher = FernetCipher(sim_settings.encryption_key.encode("ascii"))
          account = await create_account(db_session, fake_clock, name="sim", account_type="sim")
          await store_tokens(
              db_session, fake_clock, cipher,
              account_id=account.id, broker="saxo",
              tokens=TokenSet(
                  access_token="t", refresh_token="r",
                  access_expires_at=fake_clock.now() + timedelta(seconds=600),
                  refresh_expires_at=fake_clock.now() + timedelta(seconds=86400),
              ),
          )
          await db_session.commit()

          respx.get(f"{SAXO_SIM_API_BASE}/port/v1/users/me").mock(
              return_value=httpx.Response(
                  200, json={"ClientKey": "ck", "UserKey": "uk", "Name": "Torben"},
              )
          )

          app = build_app(settings=sim_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.get(f"/v1/accounts/{account.id}")
          assert resp.status_code == 200
          body = resp.json()
          assert body["client_key"] == "ck"
          assert body["name"] == "Torben"

      async def test_returns_404_for_unknown_account(
          self, fake_clock, sim_settings,
      ) -> None:
          app = build_app(settings=sim_settings, clock=fake_clock)
          async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
              resp = await ac.get("/v1/accounts/does-not-exist")
          assert resp.status_code == 404
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd engine && uv run pytest tests/unit/test_api_oauth.py::TestGetAccount -v`
  Expected: 404 / route not found.

- [ ] **Step 3: Implement the route**

  In `api.py`:

  ```python
  class AccountInfoDto(BaseModel):
      account_id: str
      account_type: str
      client_key: str | None = None
      user_key: str | None = None
      name: str | None = None


  @app.get("/v1/accounts/{account_id}", response_model=AccountInfoDto, tags=["accounts"])
  async def get_account(
      account_id: str,
      session: Annotated[AsyncSession, Depends(session_dep)],
      broker_factory: Annotated[BrokerFactory, Depends(broker_factory_dep)],
  ) -> AccountInfoDto:
      account = (
          await session.execute(select(Account).where(Account.id == account_id))
      ).scalar_one_or_none()
      if account is None:
          raise HTTPException(status_code=404, detail="account not found")

      broker = broker_factory(account)
      # SaxoBroker exposes get_account(); PaperBroker does not. Either dispatch on
      # type or use hasattr — simple and explicit.
      if hasattr(broker, "get_account"):
          info = await broker.get_account(session)
          return AccountInfoDto(
              account_id=account.id,
              account_type=account.account_type,
              client_key=info.client_key,
              user_key=info.user_key,
              name=info.name,
          )
      return AccountInfoDto(account_id=account.id, account_type=account.account_type)
  ```

- [ ] **Step 4: Run, verify pass**

  Run: `cd engine && uv run pytest tests/unit/test_api_oauth.py::TestGetAccount -v`
  Expected: 2 passed.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/src/snapd_invest/api.py engine/tests/unit/test_api_oauth.py
  git commit -m "feat(engine): GET /v1/accounts/{id} (broker-delegated for sim accounts)"
  ```

---

### Task 20: .NET CLI — extend `IEngineApi` for the new endpoints

**Files:**
- Modify: `cli/src/SnapdInvest.Client/IEngineApi.cs`
- Create or modify: `cli/src/SnapdInvest.Client/Contracts/` DTOs (mirror engine response shapes)

- [ ] **Step 1: Add DTO records**

  In `cli/src/SnapdInvest.Client/Contracts/` (create the folder if absent), add:

  ```csharp
  namespace SnapdInvest.Client.Contracts;

  public sealed record AuthorizeUrlResponse(
      [property: System.Text.Json.Serialization.JsonPropertyName("authorize_url")] string AuthorizeUrl,
      string State);

  public sealed record OAuthStatusResponse(
      [property: System.Text.Json.Serialization.JsonPropertyName("account_id")] string AccountId,
      string Broker,
      bool Authenticated);

  public sealed record AccountInfoResponse(
      [property: System.Text.Json.Serialization.JsonPropertyName("account_id")] string AccountId,
      [property: System.Text.Json.Serialization.JsonPropertyName("account_type")] string AccountType,
      [property: System.Text.Json.Serialization.JsonPropertyName("client_key")] string? ClientKey,
      [property: System.Text.Json.Serialization.JsonPropertyName("user_key")] string? UserKey,
      string? Name);
  ```

- [ ] **Step 2: Extend the Refit interface**

  In `cli/src/SnapdInvest.Client/IEngineApi.cs`, add:

  ```csharp
  [Post("/v1/oauth/saxo/start")]
  Task<AuthorizeUrlResponse> StartSaxoOAuthAsync(
      [Query, AliasAs("account_id")] string accountId,
      CancellationToken ct = default);

  [Get("/v1/oauth/saxo/status")]
  Task<OAuthStatusResponse> GetSaxoOAuthStatusAsync(
      [Query, AliasAs("account_id")] string accountId,
      CancellationToken ct = default);

  [Get("/v1/accounts/{id}")]
  Task<AccountInfoResponse> GetAccountAsync(string id, CancellationToken ct = default);
  ```

  Add `using SnapdInvest.Client.Contracts;` to the file if not present.

- [ ] **Step 3: Build**

  Run: `cd cli && dotnet build /warnaserror`
  Expected: clean build.

- [ ] **Step 4: Commit**

  ```bash
  git add cli/src/SnapdInvest.Client/
  git commit -m "feat(cli): IEngineApi adds oauth/start, oauth/status, get-account"
  ```

---

### Task 21: .NET CLI command `snapdinvest auth saxo`

**Files:**
- Create: `cli/src/SnapdInvest.Cli/Commands/AuthSaxoCommand.cs`
- Modify: `cli/src/SnapdInvest.Cli/Program.cs`
- Create: `cli/tests/SnapdInvest.Cli.Tests.Unit/AuthSaxoCommandTests.cs`

- [ ] **Step 1: Write the failing test**

  Create `cli/tests/SnapdInvest.Cli.Tests.Unit/AuthSaxoCommandTests.cs`:

  ```csharp
  using NSubstitute;
  using Shouldly;
  using SnapdInvest.Cli.Commands;
  using SnapdInvest.Client;
  using SnapdInvest.Client.Contracts;
  using Spectre.Console.Cli;
  using Xunit;

  namespace SnapdInvest.Cli.Tests.Unit;

  public class AuthSaxoCommandTests
  {
      [Fact]
      public async Task ExecuteAsync_CallsStartEndpoint_ThenPollsUntilAuthenticated()
      {
          var api = Substitute.For<IEngineApi>();
          api.StartSaxoOAuthAsync("sim-account", Arg.Any<CancellationToken>())
              .Returns(new AuthorizeUrlResponse("https://sim.logonvalidation.net/authorize?...", "state-1"));
          api.GetSaxoOAuthStatusAsync("sim-account", Arg.Any<CancellationToken>())
              .Returns(
                  new OAuthStatusResponse("sim-account", "saxo", false),
                  new OAuthStatusResponse("sim-account", "saxo", true));

          var browserOpener = Substitute.For<IBrowserOpener>();
          var cmd = new AuthSaxoCommand(api, browserOpener);
          var settings = new AuthSaxoCommand.Settings { AccountId = "sim-account", PollIntervalMs = 1 };
          var context = new CommandContext(Array.Empty<string>(), Substitute.For<IRemainingArguments>(), "auth saxo", null);

          var exitCode = await cmd.ExecuteAsync(context, settings);

          exitCode.ShouldBe(0);
          await browserOpener.Received(1).OpenAsync("https://sim.logonvalidation.net/authorize?...", Arg.Any<CancellationToken>());
          await api.Received(2).GetSaxoOAuthStatusAsync("sim-account", Arg.Any<CancellationToken>());
      }

      [Fact]
      public async Task ExecuteAsync_TimesOutAfterMaxAttempts()
      {
          var api = Substitute.For<IEngineApi>();
          api.StartSaxoOAuthAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
              .Returns(new AuthorizeUrlResponse("https://sim.logonvalidation.net/authorize?...", "state-1"));
          api.GetSaxoOAuthStatusAsync(Arg.Any<string>(), Arg.Any<CancellationToken>())
              .Returns(new OAuthStatusResponse("sim-account", "saxo", false));

          var browserOpener = Substitute.For<IBrowserOpener>();
          var cmd = new AuthSaxoCommand(api, browserOpener);
          var settings = new AuthSaxoCommand.Settings
          {
              AccountId = "sim-account",
              PollIntervalMs = 1,
              MaxAttempts = 3,
          };
          var context = new CommandContext(Array.Empty<string>(), Substitute.For<IRemainingArguments>(), "auth saxo", null);

          var exitCode = await cmd.ExecuteAsync(context, settings);

          exitCode.ShouldBe(1);
      }
  }
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd cli && dotnet test --filter "FullyQualifiedName~AuthSaxoCommandTests"`
  Expected: compilation errors (AuthSaxoCommand, IBrowserOpener don't exist).

- [ ] **Step 3: Implement `IBrowserOpener` + default**

  Create `cli/src/SnapdInvest.Cli/IBrowserOpener.cs`:

  ```csharp
  using System.Diagnostics;

  namespace SnapdInvest.Cli;

  public interface IBrowserOpener
  {
      Task OpenAsync(string url, CancellationToken ct = default);
  }

  public sealed class DefaultBrowserOpener : IBrowserOpener
  {
      public Task OpenAsync(string url, CancellationToken ct = default)
      {
          var psi = new ProcessStartInfo
          {
              FileName = url,
              UseShellExecute = true,
          };
          Process.Start(psi);
          return Task.CompletedTask;
      }
  }
  ```

- [ ] **Step 4: Implement `AuthSaxoCommand`**

  Create `cli/src/SnapdInvest.Cli/Commands/AuthSaxoCommand.cs`:

  ```csharp
  using Spectre.Console;
  using Spectre.Console.Cli;
  using SnapdInvest.Client;
  using System.ComponentModel;

  namespace SnapdInvest.Cli.Commands;

  public sealed class AuthSaxoCommand(IEngineApi api, IBrowserOpener browser)
      : AsyncCommand<AuthSaxoCommand.Settings>
  {
      public sealed class Settings : CommandSettings
      {
          [CommandOption("--account")]
          [Description("Account id to authenticate against Saxo SIM")]
          public string AccountId { get; init; } = string.Empty;

          [CommandOption("--poll-interval-ms")]
          [Description("How often to poll for token presence")]
          public int PollIntervalMs { get; init; } = 1500;

          [CommandOption("--max-attempts")]
          [Description("Give up after this many polls")]
          public int MaxAttempts { get; init; } = 120;  // ~3 minutes
      }

      public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
      {
          if (string.IsNullOrWhiteSpace(settings.AccountId))
          {
              AnsiConsole.MarkupLine("[red]--account is required[/]");
              return 1;
          }

          var start = await api.StartSaxoOAuthAsync(settings.AccountId);
          AnsiConsole.MarkupLine($"[grey]Opening browser to:[/] {start.AuthorizeUrl}");
          await browser.OpenAsync(start.AuthorizeUrl);

          for (var i = 0; i < settings.MaxAttempts; i++)
          {
              var status = await api.GetSaxoOAuthStatusAsync(settings.AccountId);
              if (status.Authenticated)
              {
                  AnsiConsole.MarkupLine("[green]Tokens stored.[/]");
                  return 0;
              }
              await Task.Delay(settings.PollIntervalMs);
          }

          AnsiConsole.MarkupLine("[red]Timed out waiting for OAuth consent.[/]");
          return 1;
      }
  }
  ```

- [ ] **Step 5: Register in `Program.cs`**

  In `cli/src/SnapdInvest.Cli/Program.cs`:
  - Add `services.AddTransient<IBrowserOpener, DefaultBrowserOpener>();` near the other DI registrations.
  - Add a nested branch `config.AddBranch("auth", auth => auth.AddCommand<AuthSaxoCommand>("saxo").WithDescription("Authenticate against Saxo SIM via OAuth."));`

- [ ] **Step 6: Build + test**

  Run: `cd cli && dotnet build /warnaserror && dotnet test`
  Expected: clean build, all tests pass.

- [ ] **Step 7: Commit**

  ```bash
  git add cli/src/SnapdInvest.Cli/ cli/tests/
  git commit -m "feat(cli): snapdinvest auth saxo (Spectre command, opens browser, polls status)"
  ```

---

### Task 22: .NET CLI command `snapdinvest get-account`

**Files:**
- Create: `cli/src/SnapdInvest.Cli/Commands/GetAccountCommand.cs`
- Modify: `cli/src/SnapdInvest.Cli/Program.cs`
- Create: `cli/tests/SnapdInvest.Cli.Tests.Unit/GetAccountCommandTests.cs`

- [ ] **Step 1: Write failing test**

  Create `cli/tests/SnapdInvest.Cli.Tests.Unit/GetAccountCommandTests.cs`:

  ```csharp
  using NSubstitute;
  using Shouldly;
  using SnapdInvest.Cli.Commands;
  using SnapdInvest.Client;
  using SnapdInvest.Client.Contracts;
  using Spectre.Console.Cli;
  using Xunit;

  namespace SnapdInvest.Cli.Tests.Unit;

  public class GetAccountCommandTests
  {
      [Fact]
      public async Task ExecuteAsync_PrintsAccountInfo()
      {
          var api = Substitute.For<IEngineApi>();
          api.GetAccountAsync("acc-1", Arg.Any<CancellationToken>()).Returns(
              new AccountInfoResponse("acc-1", "sim", "client-key", "user-key", "Torben"));

          var cmd = new GetAccountCommand(api);
          var settings = new GetAccountCommand.Settings { AccountId = "acc-1" };
          var context = new CommandContext(Array.Empty<string>(), Substitute.For<IRemainingArguments>(), "get-account", null);

          var exitCode = await cmd.ExecuteAsync(context, settings);
          exitCode.ShouldBe(0);
      }
  }
  ```

- [ ] **Step 2: Run, verify failure**

  Run: `cd cli && dotnet test --filter "FullyQualifiedName~GetAccountCommandTests"`
  Expected: GetAccountCommand undefined.

- [ ] **Step 3: Implement command**

  Create `cli/src/SnapdInvest.Cli/Commands/GetAccountCommand.cs`:

  ```csharp
  using Spectre.Console;
  using Spectre.Console.Cli;
  using SnapdInvest.Client;
  using System.ComponentModel;

  namespace SnapdInvest.Cli.Commands;

  public sealed class GetAccountCommand(IEngineApi api) : AsyncCommand<GetAccountCommand.Settings>
  {
      public sealed class Settings : CommandSettings
      {
          [CommandOption("--account")]
          [Description("Account id")]
          public string AccountId { get; init; } = string.Empty;
      }

      public override async Task<int> ExecuteAsync(CommandContext context, Settings settings)
      {
          if (string.IsNullOrWhiteSpace(settings.AccountId))
          {
              AnsiConsole.MarkupLine("[red]--account is required[/]");
              return 1;
          }

          var info = await api.GetAccountAsync(settings.AccountId);
          var table = new Table().AddColumn("Field").AddColumn("Value");
          table.AddRow("account_id", info.AccountId);
          table.AddRow("account_type", info.AccountType);
          table.AddRow("client_key", info.ClientKey ?? "—");
          table.AddRow("user_key", info.UserKey ?? "—");
          table.AddRow("name", info.Name ?? "—");
          AnsiConsole.Write(table);
          return 0;
      }
  }
  ```

- [ ] **Step 4: Register in `Program.cs`**

  ```csharp
  config.AddCommand<GetAccountCommand>("get-account")
      .WithDescription("Show account details (delegates through the engine to the configured broker).");
  ```

- [ ] **Step 5: Build + test**

  Run: `cd cli && dotnet build /warnaserror && dotnet test`
  Expected: clean.

- [ ] **Step 6: Commit**

  ```bash
  git add cli/src/SnapdInvest.Cli/ cli/tests/
  git commit -m "feat(cli): snapdinvest get-account (calls /v1/accounts/{id})"
  ```

---

### Task 23: Doc updates — spec env-var names, module-map, AGENTS

**Files:**
- Modify: `docs/specs/T-001A-saxo-sim-oauth-and-get-account.md`
- Modify: `docs/architecture/module-map.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Fix env-var names in the spec**

  In `docs/specs/T-001A-saxo-sim-oauth-and-get-account.md`:
  - Section 3 step 4: replace the `.env` block with:

    ```
    SNAPDINVEST_SAXO_ENV=sim
    SNAPDINVEST_SAXO_CLIENT_ID=<app key>
    SNAPDINVEST_SAXO_REDIRECT_URI=http://localhost:8000/v1/oauth/saxo/callback
    ```

  - Section 3 step 5: replace `snapd-invest init-keys` with `make init-keys`, and update the env var name from `SNAPD_ENCRYPTION_KEY` → `SNAPDINVEST_ENCRYPTION_KEY`.
  - Section 4.5: rename the constant `SNAPD_ENCRYPTION_KEY` → `SNAPDINVEST_ENCRYPTION_KEY` throughout. Replace the `init-keys CLI command` description with `make init-keys` (and note: the underlying tool is `python -m snapd_invest.tools.init_keys`).

- [ ] **Step 2: Update `module-map.md`**

  In `docs/architecture/module-map.md`:
  - In the Python engine modules table, replace the `broker.py` row with:

    | Module | Owns | Depends on |
    |---|---|---|
    | `broker/` | `IBroker` protocol, `PaperBroker`, `SaxoBroker`, OAuth helpers, `BrokerError` hierarchy | `persistence`, `clock`, `audit`, `crypto`, `models` |
    | `crypto.py` | `Cipher` protocol + `FernetCipher` | (none) |
    | `tools/init_keys.py` | One-off bootstrap helper to generate `SNAPDINVEST_ENCRYPTION_KEY` | `crypto` |

  - Under "Boundary discipline", replace the bullet `broker.py is the only module that imports Saxo SDK or HTTP clients to brokers.` with `the broker/ package is the only place that imports Saxo or HTTP clients to brokers.`

- [ ] **Step 3: Update `AGENTS.md`**

  Append a new subsection under the existing testing section:

  ```markdown
  ### Running SIM-live tests

  Most tests run as part of `make test`. The Saxo SIM-live test is excluded
  by the `saxo_live` pytest marker and never runs in CI. To run it locally
  after completing the OAuth setup (see `docs/specs/T-001A-saxo-sim-oauth-and-get-account.md`):

  ```bash
  cd engine
  SAXO_RUN_LIVE_TESTS=1 uv run pytest -m saxo_live -v
  ```

  This hits real Saxo SIM. Never set `SAXO_RUN_LIVE_TESTS=1` in CI or any
  shared environment. The `make test-engine-live` target wraps this for
  convenience.
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add docs/ AGENTS.md
  git commit -m "docs: reconcile env-var names + module-map + AGENTS SIM-live notes"
  ```

---

### Task 24: SIM-live integration test

**Files:**
- Create: `engine/tests/integration/__init__.py`
- Create: `engine/tests/integration/test_saxo_live.py`
- Modify: `Makefile` (add `test-engine-live` target)

- [ ] **Step 1: Create the empty package marker**

  Create `engine/tests/integration/__init__.py` (empty file with a single comment):

  ```python
  """Integration tests that hit external services. Opt-in via env vars."""
  ```

- [ ] **Step 2: Write the live test**

  Create `engine/tests/integration/test_saxo_live.py`:

  ```python
  """End-to-end Saxo SIM test. Skipped unless SAXO_RUN_LIVE_TESTS=1."""

  from __future__ import annotations

  import os
  from datetime import datetime

  import httpx
  import pytest

  from snapd_invest.broker.saxo import SaxoBroker
  from snapd_invest.broker.saxo_oauth import load_tokens
  from snapd_invest.config import Settings
  from snapd_invest.crypto import FernetCipher

  pytestmark = pytest.mark.saxo_live


  @pytest.mark.skipif(
      os.environ.get("SAXO_RUN_LIVE_TESTS") != "1",
      reason="SAXO_RUN_LIVE_TESTS=1 not set",
  )
  class TestSaxoLiveGetAccount:
      async def test_get_account_returns_real_user(self, db_session, fake_clock) -> None:
          settings = Settings()  # reads .env
          assert settings.saxo_env == "sim"
          assert settings.saxo_client_id is not None
          assert settings.encryption_key is not None

          cipher = FernetCipher(settings.encryption_key.encode("ascii"))

          # Find the SIM account
          from sqlalchemy import select

          from snapd_invest.models import Account

          account = (
              await db_session.execute(
                  select(Account).where(Account.account_type == "sim")
              )
          ).scalar_one_or_none()
          assert account is not None, "No SIM account configured. Create one and run `make init-keys`, then `snapdinvest auth saxo --account <id>`."

          stored = await load_tokens(db_session, cipher, account_id=account.id, broker="saxo")
          assert stored is not None, "No tokens stored. Run `snapdinvest auth saxo --account <id>` first."

          async with httpx.AsyncClient(timeout=30.0) as client:
              broker = SaxoBroker(
                  client=client, clock=_RealClockShim(), cipher=cipher,
                  client_id=settings.saxo_client_id, account_id=account.id,
              )
              info = await broker.get_account(db_session)

          # Sanity assertions that the response is well-formed
          assert info.client_key, "Saxo response missing ClientKey"
          assert info.user_key, "Saxo response missing UserKey"


  class _RealClockShim:
      """Wall-clock for the live test; FakeClock would break refresh timing."""

      def now(self) -> datetime:
          from datetime import UTC

          return datetime.now(UTC)
  ```

- [ ] **Step 3: Verify the test is properly skipped without the env var**

  Run: `cd engine && uv run pytest tests/integration/ -v`
  Expected: 1 skipped.

- [ ] **Step 4: Add the Makefile target**

  In `Makefile`:

  ```make
  test-engine-live:
  	cd engine && SAXO_RUN_LIVE_TESTS=1 uv run pytest -m saxo_live -v
  ```

  Add to `.PHONY` if present.

- [ ] **Step 5: Commit**

  ```bash
  git add engine/tests/integration/ Makefile
  git commit -m "test(engine): SIM-live get_account integration test (gated by SAXO_RUN_LIVE_TESTS)"
  ```

---

### Task 25: Final verification + cleanup

- [ ] **Step 1: Full engine suite**

  Run: `cd engine && uv run pytest && uv run mypy src && uv run ruff check && uv run ruff format --check`
  Expected: all clean.

- [ ] **Step 2: Full CLI suite**

  Run: `cd cli && dotnet build /warnaserror && dotnet test && dotnet format --verify-no-changes`
  Expected: all clean.

- [ ] **Step 3: Local manual smoke (only if the developer has Saxo SIM credentials in .env)**

  ```bash
  # Generate the master key (one-time)
  make init-keys

  # Start the engine
  make dev-engine
  ```

  In another terminal:

  ```bash
  # Create a SIM account first via the existing CLI flow (or directly via SQL)
  # then:
  cd cli
  dotnet run --project src/SnapdInvest.Cli -- auth saxo --account <sim-account-id>
  # complete consent in browser, expect "Tokens stored."

  dotnet run --project src/SnapdInvest.Cli -- get-account --account <sim-account-id>
  # expect a table with ClientKey / UserKey / Name
  ```

  Optional:

  ```bash
  make test-engine-live
  ```

- [ ] **Step 4: Confirm `tasks/T-001-saxo-sim-integration.md`'s status**

  The original task spec lives at `tasks/T-001-saxo-sim-integration.md` and still references `client_credentials`. T-001-A only fixes a subset. Edit that file: add a top-line `**Superseded by:** T-001-A (this PR) + T-001-B (deferred)` and leave the rest as-is for T-001-B's reference. Commit:

  ```bash
  git add tasks/T-001-saxo-sim-integration.md
  git commit -m "docs(tasks): mark T-001 as superseded by T-001-A + T-001-B"
  ```

- [ ] **Step 5: Open PR**

  ```bash
  git push -u origin feature/T-001A-saxo-sim-oauth
  gh pr create --base main --head feature/T-001A-saxo-sim-oauth \
    --title "feat(engine,cli): T-001-A — Saxo SIM OAuth + get_account" \
    --body "$(cat <<'EOF'
  Implements [T-001-A](../blob/feature/T-001A-saxo-sim-oauth/docs/specs/T-001A-saxo-sim-oauth-and-get-account.md):
  the auth-only first half of Saxo SIM integration.

  ## What's in
  - Authorization Code + PKCE handshake against `https://sim.logonvalidation.net`
  - Encrypted token persistence (`Cipher` abstraction + Fernet default)
  - `SaxoBroker.get_account()` with reactive 401 refresh
  - Broker selection by `Account.account_type` via `broker_factory`
  - CLI: `snapdinvest auth saxo` (opens browser) + `snapdinvest get-account`
  - ADR-005

  ## What's out (T-001-B)
  - `place_order` / `cancel` / `get_positions` / `get_last_price`
  - Idempotency on top of Saxo `ExternalReference`
  - MicroTrader scheduled-job wiring for SIM

  ## Test plan
  - [x] `make test` — clean
  - [x] `make lint` — clean
  - [x] `cd engine && uv run mypy src` — clean
  - [ ] Manual: `make init-keys` then `snapdinvest auth saxo --account <id>` then `snapdinvest get-account --account <id>`
  - [ ] Optional: `make test-engine-live` against real SIM
  EOF
  )"
  ```

---

## Self-review

### Spec coverage

| Spec section | Covered by tasks |
|---|---|
| §2 In scope — PKCE handshake | Tasks 10, 11, 16, 17 |
| §2 In scope — refresh + storage | Tasks 12, 13 |
| §2 In scope — Cipher + FernetCipher | Task 3 |
| §2 In scope — `make init-keys` | Task 4 |
| §2 In scope — `SaxoBroker.get_account` | Task 14 |
| §2 In scope — broker selection | Task 15 |
| §2 In scope — `Settings` extensions | Task 5 |
| §2 In scope — engine routes | Tasks 16, 17, 18, 19 |
| §2 In scope — CLI commands | Tasks 21, 22 |
| §2 In scope — `broker/` package refactor | Task 6 |
| §2 In scope — module-map update | Task 23 |
| §2 In scope — unit tests | Tasks 3, 4, 5, 7, 10–14, 16–19, 21, 22 |
| §2 In scope — SIM-live test + marker | Tasks 1, 24 |
| §2 In scope — ADR-005 | Task 2 |
| §3 User actions | Captured in §3 of the spec; the spec itself is updated by Task 23 |
| §4.2 Schema | Tasks 8, 9 |
| §4.3 Code structure | Tasks 6, 14, plus 3, 4, 10–13 |
| §4.4 Error types | Task 7 |
| §4.5 Cipher abstraction | Task 3 |
| §4.6 Multi-user readiness | Schema in Tasks 8, 9 + Cipher abstraction in Task 3 |
| §5 Test strategy | Tasks 3, 10–14, 16–19, 24 |
| §6 Acceptance criteria | All achievable by end of Task 25 |
| §7 Open questions | None blocking; token TTLs observed at Task 24 / 25 |

### Type / name consistency

- `BrokerError`, `BrokerAuthError`, `BrokerHttpError`, `BrokerTimeoutError` — defined in Task 7, used in Tasks 11, 13, 14.
- `Cipher` / `FernetCipher` / `generate_key` — Task 3, used in 4, 12, 13, 14, 17, 18, 19, 24.
- `TokenSet` — Task 11, used in 12, 13, 14, 17.
- `PkceChallenge`, `generate_pkce`, `persist_oauth_state`, `consume_oauth_state` — Task 10, used in 11, 16, 17.
- `store_tokens`, `load_tokens` — Task 12, used in 13, 14, 17, 18, 24.
- `refresh_tokens`, `get_active_access_token` — Task 13, used in 14.
- `SaxoBroker`, `SaxoAccountInfo`, `SAXO_SIM_API_BASE` — Task 14, used in 19, 24.
- `BrokerFactory`, `broker_factory_dep` — Task 15, used in 19.
- Pydantic response models (`AuthorizeUrlResponse`, `OAuthStatusResponse`, `AccountInfoDto`) — Tasks 16, 18, 19; C# DTO counterparts in Task 20.
- All env-var names use the `SNAPDINVEST_` prefix consistently.

No placeholders detected. Every step shows the code or the exact command.
