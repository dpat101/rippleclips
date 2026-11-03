"""One-off Google sign-in, run on the droplet (SETUP.md, step 4).

It prints a link. Dave opens it on his PC (through the ssh tunnel on port 8085), signs in with the
Google account that owns The Ripple Effect folder, and the droplet saves a token in
~/.rippleclips-google-token.json (chmod 600). The token is never printed.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

from . import config


def main() -> None:
    if not config.GOOGLE_CLIENT_FILE.exists():
        raise SystemExit(f"Missing {config.GOOGLE_CLIENT_FILE}: copy the OAuth client file there first (SETUP.md).")
    flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CLIENT_FILE), config.GOOGLE_SCOPES)
    creds = flow.run_local_server(
        host="localhost", port=8085, open_browser=False,
        authorization_prompt_message="Open this link in the browser on your PC:\n\n{url}\n",
        success_message="Signed in. You can close this tab and go back to PowerShell.",
        access_type="offline", prompt="consent",
    )
    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())
    config.GOOGLE_TOKEN_FILE.chmod(0o600)
    print(f"Saved. Token file is {config.GOOGLE_TOKEN_FILE.stat().st_size} bytes.")


if __name__ == "__main__":
    main()
