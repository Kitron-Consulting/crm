"""Microsoft 365 (Exchange Online) OAuth2 for SMTP/IMAP via MSAL device flow.

Kept out of mail.py so the auth concern is isolated and msal stays an
*optional* import: only configs with ``auth: "oauth-ms"`` ever reach this
module, mirroring how boto3 is lazy-imported for the S3 backend. Password
configs never import msal.

The XOAUTH2 SASL-string construction lives here too (build_xoauth2_string)
so the SMTP and IMAP paths share one definition — and so the unit tests
have a single target for the base64/raw asymmetry.

Nothing here prints or logs the access token or the auth string. The device
flow's user-facing message (URL + code, no token) goes to stderr.
"""

import os
import sys
from pathlib import Path

# Both scopes in one token so a single device login covers send *and* read;
# the refresh token in the cache then serves both smtplib and imaplib.
SCOPES = [
    "https://outlook.office365.com/IMAP.AccessAsUser.All",
    "https://outlook.office365.com/SMTP.Send",
]

# The MSAL refresh-token cache is a bearer credential. It lives beside the
# app config dir on a FIXED path — never in crm_data.json (which may sync to
# S3) and never derived from --data / CRM_STORAGE.
CACHE_FILE = Path.home() / ".config" / "kitron-crm" / "msal_cache.json"

MSAL_MISSING_MSG = (
    "Microsoft 365 OAuth requires msal — install with: pip install msal"
)

# `crm serve` sets this False: a device-code login must never start inside the
# web server (it would block the HTTP request and prompt in the wrong place).
ALLOW_DEVICE_FLOW = True


class OAuthError(Exception):
    """Raised when Microsoft 365 OAuth token acquisition fails."""


def validate_oauth_config(cfg):
    """Raise OAuthError if an ``oauth-ms`` block lacks a required field.

    Runs before msal is imported so a misconfiguration fails cleanly even
    when the optional dependency isn't installed.
    """
    missing = [k for k in ("user", "client_id", "tenant_id") if not cfg.get(k)]
    if missing:
        raise OAuthError(
            "Microsoft 365 OAuth config (auth: \"oauth-ms\") is missing "
            "required field(s): " + ", ".join(missing)
        )


def build_xoauth2_string(user, token):
    """Build the raw XOAUTH2 SASL string (NOT base64-encoded).

    SMTP callers must base64-encode this themselves; imaplib's authenticate()
    base64-encodes internally, so its authobject returns these raw bytes.
    """
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


def _load_cache():
    import msal

    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        try:
            cache.deserialize(CACHE_FILE.read_text())
        except Exception:
            # Corrupt/unreadable cache → start empty and re-authenticate.
            pass
    return cache


def _save_cache(cache):
    if not cache.has_state_changed:
        return
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(cache.serialize())
    try:
        os.chmod(CACHE_FILE, 0o600)
    except OSError:
        pass


def get_token(cfg):
    """Return an access token for the mailbox described by ``cfg``.

    Uses the cached refresh token silently when possible; otherwise runs the
    device-code flow, printing instructions to stderr and blocking until the
    user completes login in a browser. A revoked/expired refresh token falls
    through to a fresh device flow automatically.

    Raises OAuthError on any failure, including when login is required but the
    session is non-interactive (so cron/piped runs fail fast instead of
    hanging on device-flow polling). Never logs the token or auth string.
    """
    validate_oauth_config(cfg)

    try:
        import msal
    except ImportError:
        raise OAuthError(MSAL_MISSING_MSG)

    cache = _load_cache()
    app = msal.PublicClientApplication(
        cfg["client_id"],
        authority=f"https://login.microsoftonline.com/{cfg['tenant_id']}",
        token_cache=cache,
    )

    result = None
    accounts = app.get_accounts(username=cfg.get("user"))
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    if not result:
        # Interactive device login needed. Refuse when there's no human to
        # complete it — or when we're inside `crm serve`, where a device-code
        # prompt would print to the server terminal while the browser request
        # blocks on the poll loop and just looks stuck. Fail fast instead.
        if not ALLOW_DEVICE_FLOW or not sys.stdin.isatty():
            raise OAuthError(
                "Microsoft 365 login required (no valid cached token). Run a "
                "crm mail command in a terminal once — e.g. `crm thread "
                "<contact>` — to complete the device login, then retry."
            )
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise OAuthError(
                "Failed to start Microsoft 365 device login: "
                + flow.get("error_description", "unknown error")
            )
        # flow["message"] carries the URL + code (no token) — to stderr so it
        # never pollutes stdout used by --json / piped output.
        print(flow["message"], file=sys.stderr, flush=True)
        result = app.acquire_token_by_device_flow(flow)

    _save_cache(cache)

    if not result or "access_token" not in result:
        desc = (result or {}).get("error_description") or "unknown error"
        raise OAuthError(f"Microsoft 365 OAuth failed: {desc}{_error_hint(desc)}")

    return result["access_token"]


def _error_hint(desc):
    """Map known AADSTS failure codes to an actionable suffix, or ''."""
    # AADSTS7000218: AAD demanded a client secret — the app registration isn't
    # actually marked as a public client, so the device-code flow is rejected.
    if "AADSTS7000218" in desc:
        return (
            "\nThis app registration is not configured as a public client. In "
            "Entra ID → App registrations → your app → Authentication → Advanced "
            "settings, set \"Allow public client flows\" to Yes (or set "
            "\"allowPublicClient\": true in the manifest), then retry."
        )
    return ""
