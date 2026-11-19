import base64
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_SEND_ENDPOINT = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_SCOPES = (
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
)


class GmailServiceError(Exception):
    pass


class GmailConfigurationError(GmailServiceError):
    pass


class GmailCredentialError(GmailServiceError):
    pass


class GmailUpstreamError(GmailServiceError):
    def __init__(self, message: str, *, category: str, uncertain: bool = False) -> None:
        super().__init__(message)
        self.category = category
        self.uncertain = uncertain


@dataclass(frozen=True)
class OAuthGrant:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: frozenset[str]
    google_subject: str
    email: str


@dataclass(frozen=True)
class RefreshedAccessToken:
    access_token: str
    expires_at: datetime
    refresh_token: str | None


@dataclass(frozen=True)
class GmailMessageResult:
    message_id: str
    thread_id: str


def build_plain_text_message(
    *,
    sender_email: str,
    recipient_email: str,
    subject: str,
    body: str,
    attachment: tuple[str, bytes] | None = None,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> str:
    if "\r" in subject or "\n" in subject:
        raise ValueError("Subject must be one line.")
    try:
        sender = Address(addr_spec=sender_email)
        recipient = Address(addr_spec=recipient_email)
    except ValueError as exc:
        raise ValueError("Email address is invalid.") from exc

    message = EmailMessage(policy=SMTP)
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    if message_id is not None:
        message["Message-ID"] = message_id
    if in_reply_to is not None:
        message["In-Reply-To"] = in_reply_to
    if references is not None:
        message["References"] = references
    message.set_content(body, subtype="plain", charset="utf-8")
    if attachment is not None:
        filename, content = attachment
        message.add_attachment(
            content,
            maintype="application",
            subtype="pdf",
            filename=filename,
        )
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


class GmailService:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self._client_id = settings.gmail_oauth_client_id
        self._client_secret = (
            settings.gmail_oauth_client_secret.get_secret_value()
            if settings.gmail_oauth_client_secret
            else None
        )
        self._redirect_uri = str(settings.gmail_oauth_redirect_uri)
        self._encryption_key = (
            settings.gmail_token_encryption_key.get_secret_value()
            if settings.gmail_token_encryption_key
            else None
        )
        self._timeout = settings.gmail_http_timeout_seconds
        self._client = client

    def validate_configuration(self) -> None:
        if not self._client_id or not self._client_secret or not self._encryption_key:
            raise GmailConfigurationError("Gmail OAuth is not configured.")
        try:
            Fernet(self._encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise GmailConfigurationError(
                "GMAIL_TOKEN_ENCRYPTION_KEY must be a valid Fernet key."
            ) from exc

    def authorization_url(self, state: str) -> str:
        self.validate_configuration()
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": " ".join(GMAIL_SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
                "state": state,
            }
        )
        return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}"

    def encrypt_token(self, token: str) -> str:
        self.validate_configuration()
        return Fernet(self._encryption_key.encode("ascii")).encrypt(
            token.encode("utf-8")
        ).decode("ascii")

    def decrypt_token(self, encrypted_token: str) -> str:
        self.validate_configuration()
        try:
            return Fernet(self._encryption_key.encode("ascii")).decrypt(
                encrypted_token.encode("ascii")
            ).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise GmailCredentialError("Stored Gmail credentials cannot be decrypted.") from exc

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(method, url, **kwargs)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.request(method, url, **kwargs)

    async def complete_oauth(self, code: str) -> OAuthGrant:
        self.validate_configuration()
        try:
            token_response = await self._request(
                "POST",
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        except httpx.TimeoutException as exc:
            raise GmailUpstreamError(
                "Google OAuth timed out.", category="oauth_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise GmailUpstreamError(
                "Google OAuth is unavailable.", category="oauth_transport"
            ) from exc
        if not token_response.is_success:
            raise GmailCredentialError("Google rejected the OAuth authorization code.")
        try:
            tokens = token_response.json()
        except json.JSONDecodeError as exc:
            raise GmailUpstreamError(
                "Google returned an unreadable token response.",
                category="oauth_malformed",
            ) from exc
        access_token = tokens.get("access_token") if isinstance(tokens, dict) else None
        refresh_token = tokens.get("refresh_token") if isinstance(tokens, dict) else None
        expires_in = tokens.get("expires_in") if isinstance(tokens, dict) else None
        scope_value = tokens.get("scope") if isinstance(tokens, dict) else None
        if not isinstance(access_token, str) or not isinstance(expires_in, int):
            raise GmailUpstreamError(
                "Google returned incomplete credentials.", category="oauth_malformed"
            )
        scopes = frozenset(scope_value.split()) if isinstance(scope_value, str) else frozenset()
        if not set(GMAIL_SCOPES).issubset(scopes):
            raise GmailCredentialError("Google did not grant all required Gmail scopes.")

        try:
            userinfo_response = await self._request(
                "GET",
                GOOGLE_USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.TimeoutException as exc:
            raise GmailUpstreamError(
                "Google identity verification timed out.", category="userinfo_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise GmailUpstreamError(
                "Google identity verification is unavailable.",
                category="userinfo_transport",
            ) from exc
        if not userinfo_response.is_success:
            raise GmailCredentialError("Google could not verify the connected identity.")
        try:
            userinfo = userinfo_response.json()
        except json.JSONDecodeError as exc:
            raise GmailUpstreamError(
                "Google returned an unreadable identity response.",
                category="userinfo_malformed",
            ) from exc
        subject = userinfo.get("sub") if isinstance(userinfo, dict) else None
        email = userinfo.get("email") if isinstance(userinfo, dict) else None
        email_verified = userinfo.get("email_verified") if isinstance(userinfo, dict) else None
        if (
            not isinstance(subject, str)
            or not isinstance(email, str)
            or email_verified is not True
        ):
            raise GmailCredentialError("Google did not return a verified email identity.")
        return OAuthGrant(
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            scopes=scopes,
            google_subject=subject,
            email=email,
        )

    async def refresh_access_token(self, refresh_token: str) -> RefreshedAccessToken:
        self.validate_configuration()
        try:
            response = await self._request(
                "POST",
                GOOGLE_TOKEN_ENDPOINT,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.TimeoutException as exc:
            raise GmailUpstreamError(
                "Refreshing Gmail credentials timed out.", category="refresh_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise GmailUpstreamError(
                "Refreshing Gmail credentials failed.", category="refresh_transport"
            ) from exc
        if response.status_code in {400, 401}:
            raise GmailCredentialError("The Gmail connection has expired or been revoked.")
        if not response.is_success:
            raise GmailUpstreamError(
                "Google could not refresh Gmail credentials.", category="refresh_upstream"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise GmailUpstreamError(
                "Google returned an unreadable refresh response.",
                category="refresh_malformed",
            ) from exc
        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
        new_refresh = payload.get("refresh_token") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not isinstance(expires_in, int):
            raise GmailUpstreamError(
                "Google returned incomplete refreshed credentials.",
                category="refresh_malformed",
            )
        return RefreshedAccessToken(
            access_token=access_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            refresh_token=new_refresh if isinstance(new_refresh, str) else None,
        )

    async def revoke(self, token: str) -> None:
        try:
            response = await self._request(
                "POST", GOOGLE_REVOCATION_ENDPOINT, data={"token": token}
            )
        except httpx.TimeoutException as exc:
            raise GmailUpstreamError(
                "Google revocation timed out.", category="revoke_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            raise GmailUpstreamError(
                "Google revocation is unavailable.", category="revoke_transport"
            ) from exc
        if response.status_code not in {200, 400}:
            raise GmailUpstreamError(
                "Google could not revoke the Gmail connection.",
                category="revoke_upstream",
            )

    async def send_message(
        self, access_token: str, raw_message: str, thread_id: str | None = None
    ) -> GmailMessageResult:
        message_resource = {"raw": raw_message}
        if thread_id is not None:
            message_resource["threadId"] = thread_id
        try:
            response = await self._request(
                "POST",
                GMAIL_SEND_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                json=message_resource,
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout) as exc:
            raise GmailUpstreamError(
                "Gmail did not confirm whether the message was accepted.",
                category="dispatch_timeout",
                uncertain=True,
            ) from exc
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            raise GmailUpstreamError(
                "Gmail could not be reached.", category="dispatch_connect"
            ) from exc
        except httpx.TimeoutException as exc:
            raise GmailUpstreamError(
                "Gmail did not confirm whether the message was accepted.",
                category="dispatch_timeout",
                uncertain=True,
            ) from exc
        except (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError) as exc:
            raise GmailUpstreamError(
                "Gmail did not confirm whether the message was accepted.",
                category="dispatch_ambiguous_transport",
                uncertain=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise GmailUpstreamError(
                "Gmail could not send the message.", category="dispatch_transport"
            ) from exc
        if response.status_code in {401, 403}:
            raise GmailCredentialError("The Gmail connection has expired or been revoked.")
        if not response.is_success:
            raise GmailUpstreamError(
                "Gmail rejected the message.",
                category=f"dispatch_http_{response.status_code}",
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise GmailUpstreamError(
                "Gmail accepted the request but returned no confirmation.",
                category="dispatch_malformed_success",
                uncertain=True,
            ) from exc
        message_id = payload.get("id") if isinstance(payload, dict) else None
        thread_id = payload.get("threadId") if isinstance(payload, dict) else None
        if not isinstance(message_id, str) or not isinstance(thread_id, str):
            raise GmailUpstreamError(
                "Gmail accepted the request but returned incomplete confirmation.",
                category="dispatch_malformed_success",
                uncertain=True,
            )
        return GmailMessageResult(message_id=message_id, thread_id=thread_id)


def get_gmail_service() -> GmailService:
    return GmailService()
