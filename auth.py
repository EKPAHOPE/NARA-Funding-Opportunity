"""Single-user login for the Streamlit funding finder.

No plaintext password is stored in the application. Override APP_PASSWORD_HASH
with a PBKDF2-SHA256 hash in environment variables or Streamlit secrets.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

import streamlit as st


# This account alone can sign in. Matching ignores email case only.
LOGIN_EMAIL = "NARA-CAMHR@hull.ac.uk"

DEFAULT_PASSWORD_HASH = (
    "pbkdf2_sha256$480000$8OcHvKSVU7Mh2_PZuouEIg$"
    "LfW8jqvJ0_iALSu_vHzCGvJOUmFPyxpxuRe-46xPm88"
)
AUTH_KEY = "nara_authenticated"
MAX_ATTEMPTS = 5
LOCK_SECONDS = 60


def configured_password_hash() -> str:
    """Environment config takes priority over Streamlit secrets."""
    if os.getenv("APP_PASSWORD_HASH"):
        return os.environ["APP_PASSWORD_HASH"]
    try:
        secret = str(st.secrets.get("APP_PASSWORD_HASH", ""))
        if secret:
            return secret
    except Exception:
        pass
    return DEFAULT_PASSWORD_HASH


def make_password_hash(password: str, *, salt: bytes | None = None, iterations: int = 480_000) -> str:
    """Generate a PBKDF2 hash for secret rotation."""
    salt = salt if salt is not None else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    encode = lambda b: base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${iterations}${encode(salt)}${encode(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, rounds, salt_b64, digest_b64 = stored_hash.split("$")
        rounds_int = int(rounds)
        if scheme != "pbkdf2_sha256" or not (150_000 <= rounds_int <= 2_000_000):
            return False
        decode = lambda b: base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
        salt, expected = decode(salt_b64), decode(digest_b64)
        if len(salt) < 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds_int)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, OverflowError):
        return False


def check_credentials(email: str, password: str, password_hash: str | None = None) -> bool:
    if not hmac.compare_digest(email.strip().casefold(), LOGIN_EMAIL.casefold()):
        return False
    return verify_password(password, password_hash or configured_password_hash())


def require_login() -> None:
    """Stop app execution *before* rendering the sidebar, data or fetch controls."""
    if st.session_state.get(AUTH_KEY) is True:
        return

    st.markdown(
        """
        <style>
        .login-heading {text-align:center; font-size:2rem; font-weight:750; margin-top:1.5rem;}
        .login-subtitle {text-align:center; color:#667085; margin-bottom:1.7rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    _, middle, _ = st.columns([1, 1.35, 1])
    with middle:
        st.markdown('<div class="login-heading">NARA Funding Finder</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="login-subtitle">Secure access for the NARA member team</div>',
            unsafe_allow_html=True,
        )
        with st.form("nara_login_form", clear_on_submit=False):
            email = st.text_input("Email address", placeholder="name@hull.ac.uk")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

        if submitted:
            locked_until = float(st.session_state.get("nara_locked_until", 0))
            if time.monotonic() < locked_until:
                st.error("Too many failed attempts. Please try again in a minute.")
            elif check_credentials(email, password):
                st.session_state[AUTH_KEY] = True
                st.session_state.pop("nara_login_attempts", None)
                st.session_state.pop("nara_locked_until", None)
                st.rerun()
            else:
                attempts = int(st.session_state.get("nara_login_attempts", 0)) + 1
                st.session_state["nara_login_attempts"] = attempts
                if attempts >= MAX_ATTEMPTS:
                    st.session_state["nara_locked_until"] = time.monotonic() + LOCK_SECONDS
                    st.session_state["nara_login_attempts"] = 0
                    st.error("Too many failed attempts. Please try again in a minute.")
                else:
                    st.error("Incorrect email address or password.")
        st.caption("This funding finder is restricted to authorised NARA member access.")
    st.stop()


def logout() -> None:
    """Clear authenticated state and previously fetched records from this session."""
    st.session_state.clear()
    st.rerun()
