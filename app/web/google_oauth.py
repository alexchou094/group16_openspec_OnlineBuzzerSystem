from datetime import datetime

from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from requests import RequestException


SCOPE = "https://www.googleapis.com/auth/drive.file"
OAUTH_WINDOW = 600


def start_flow(client_file: str, redirect_uri: str) -> tuple[Flow, str, str]:
    flow = Flow.from_client_secrets_file(
        client_file, scopes=[SCOPE], redirect_uri=redirect_uri, autogenerate_code_verifier=True,
    )
    if flow.client_type != "web":
        raise ValueError("web client required")
    url, state = flow.authorization_url(access_type="online", prompt="consent")
    return flow, url, state


def exchange_code(flow: Flow, code: str) -> tuple[str, datetime] | None:
    try:
        flow.fetch_token(code=code, timeout=15)
        credentials = flow.credentials
        if credentials.token and credentials.expiry is not None:
            return credentials.token, credentials.expiry
    except (OAuth2Error, RequestException, ValueError, Warning):
        pass
    return None
