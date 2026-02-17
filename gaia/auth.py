"""Local authentication helpers for GAIA.

This module stores provider tokens locally and now prefers OAuth for OpenAI.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import argparse
import base64
import os
import getpass
import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any

import requests


AUTH_DIR = Path.home() / ".gaia" / "auth"
AUTH_FILE = AUTH_DIR / "profiles.json"

PROVIDER_ENV_MAP = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

PROVIDER_LOGIN_URL = {
    "openai": "https://platform.openai.com/settings/organization/api-keys",
    "gemini": "https://aistudio.google.com/app/apikey",
}

OPENAI_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_OAUTH_REDIRECT_URI = "http://127.0.0.1:1455/auth/callback"
OPENAI_OAUTH_SCOPE = "openid profile email offline_access"
OPENAI_OAUTH_CALLBACK_HOST = "127.0.0.1"
OPENAI_OAUTH_CALLBACK_PORT = 1455
OPENAI_OAUTH_CALLBACK_PATH = "/auth/callback"
CODEX_AUTH_SERVICE_NAME = "Codex Auth"


@dataclass
class AuthProfile:
    provider: str
    token: str
    source: str
    updated_at: str
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_auth_dir() -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    try:
        AUTH_DIR.chmod(0o700)
    except OSError:
        pass


def _load_profiles() -> dict[str, dict[str, Any]]:
    if not AUTH_FILE.exists():
        return {}
    try:
        with AUTH_FILE.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _save_profiles(payload: dict[str, dict[str, Any]]) -> None:
    _ensure_auth_dir()
    with AUTH_FILE.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _now_ts() -> int:
    return int(time.time())


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _parse_code(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None

    parsed = urlparse(value)
    params = parse_qs(parsed.query or "")
    if params:
        if params.get("code"):
            return params["code"][0]
        if params.get("auth_code"):
            return params["auth_code"][0]

    fragment = parse_qs(parsed.fragment or "")
    if fragment.get("code"):
        return fragment["code"][0]

    if "code=" in value:
        match = re.search(r"code=([^&\s]+)", value)
        if match:
            return match.group(1)

    return value


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_jwt_claim(token: str, key: str) -> str | None:
    if not token or token.count(".") != 2:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        if isinstance(data, dict):
            value = data.get(key)
            if isinstance(value, str):
                return value
    except Exception:
        return None
    return None


def _openid_state_profile(provider: str) -> dict[str, Any] | None:
    return _load_profiles().get(provider)


def _is_oauth_provider(provider: str) -> bool:
    return provider == "openai"


def _is_oauth_token_expired(profile: dict[str, Any]) -> bool:
    source = profile.get("source")
    metadata = profile.get("metadata")
    if source != "oauth" or not isinstance(metadata, dict):
        return False
    expires_at = _to_int(metadata.get("expires_at"))
    if not expires_at:
        return False
    return expires_at <= _now_ts() + 30


def _post_oauth_token(payload: dict[str, str]) -> dict[str, Any]:
    try:
        response = requests.post(OPENAI_OAUTH_TOKEN_URL, data=payload, timeout=30)
    except Exception as exc:
        raise RuntimeError(f"OpenAI 토큰 교환 요청 실패: {exc}") from exc

    if response.status_code >= 400:
        raise RuntimeError(
            f"토큰 교환 실패: {response.status_code} {response.text[:200]}"
        )

    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"토큰 응답 형식 오류: {response.text[:200]}") from exc


def _save_provider_profile(provider: str, token: str, source: str = "manual", metadata: dict[str, Any] | None = None) -> None:
    payload = _load_profiles()
    payload[provider] = AuthProfile(
        provider=provider,
        token=token,
        source=source,
        updated_at=_now_iso(),
        metadata=metadata or {},
    ).to_dict()
    _save_profiles(payload)


def _refresh_openai_token(profile: dict[str, Any]) -> str | None:
    metadata = profile.get("metadata")
    if not isinstance(metadata, dict):
        return None
    refresh_token = metadata.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        return None

    response = _post_oauth_token(
        {
            "grant_type": "refresh_token",
            "client_id": OPENAI_OAUTH_CLIENT_ID,
            "refresh_token": refresh_token.strip(),
        }
    )
    access_token = response.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        return None

    new_expires = _to_int(response.get("expires_in"))
    metadata_updates: dict[str, Any] = {
        "source": "oauth",
        "oauth": True,
        "updated_at": _now_iso(),
        "issued_at": _now_iso(),
        "scope": response.get("scope", metadata.get("scope")),
        "token_type": response.get("token_type", "Bearer"),
        "account_id": _decode_jwt_claim(access_token.strip(), "sub"),
    }
    if refresh_token_value := response.get("refresh_token"):
        if isinstance(refresh_token_value, str) and refresh_token_value.strip():
            metadata_updates["refresh_token"] = refresh_token_value.strip()
    if new_expires:
        metadata_updates["expires_at"] = _now_ts() + new_expires
    if isinstance(response.get("refresh_token"), str) and response.get("refresh_token").strip():
        metadata_updates["refresh_token"] = response.get("refresh_token").strip()

    _save_provider_profile(
        provider="openai",
        token=access_token.strip(),
        source="oauth",
        metadata=metadata_updates,
    )
    return access_token.strip()


def _build_openai_authorize_url(state: str, code_verifier: str) -> str:
    code_challenge = _b64url(sha256(code_verifier.encode("utf-8")).digest())
    params = {
        "response_type": "code",
        "client_id": OPENAI_OAUTH_CLIENT_ID,
        "redirect_uri": OPENAI_OAUTH_REDIRECT_URI,
        "scope": OPENAI_OAUTH_SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    return f"{OPENAI_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


def _run_openai_callback_server(state: str, timeout: int = 90) -> dict[str, str | None] | None:
    callback_data: dict[str, str | None] = {"code": None, "error": None, "state": None}
    event = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != OPENAI_OAUTH_CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Not found")
                return

            query = parse_qs(parsed.query or "")
            got_state = (query.get("state", [""])[0] or "").strip()
            callback_data["state"] = got_state
            callback_data["error"] = (query.get("error", [""])[0] or "").strip() or None

            if got_state != state:
                callback_data["error"] = "state_mismatch"
            elif query.get("code"):
                callback_data["code"] = query["code"][0].strip()

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            response_html = """<!doctype html>
<html>
  <body>
    <h3>GAIA OpenAI 로그인</h3>
    <p>브라우저 인증이 완료되었습니다. 터미널로 돌아가세요.</p>
  </body>
</html>"""
            self.wfile.write(response_html.encode("utf-8"))
            event.set()

        def log_message(self, format: str, *args: Any) -> None:
            return

    try:
        server = HTTPServer((OPENAI_OAUTH_CALLBACK_HOST, OPENAI_OAUTH_CALLBACK_PORT), CallbackHandler)
    except OSError:
        return None

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    event.wait(timeout)
    server.shutdown()
    server.server_close()
    if thread.is_alive():
        thread.join(1.0)

    return callback_data


def _prompt_redirect_input() -> str:
    if not sys.stdin.isatty():
        return ""
    print("로그인 완료 후 브라우저 주소창의 전체 URL 또는 code 값을 붙여넣어 주세요.")
    return input("입력: ").strip()


def _exchange_openai_code(code: str, code_verifier: str) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "client_id": OPENAI_OAUTH_CLIENT_ID,
        "code": code,
        "redirect_uri": OPENAI_OAUTH_REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    data = _post_oauth_token(payload)
    if not data.get("access_token"):
        raise RuntimeError(f"토큰 응답에 access_token이 없습니다: {data}")
    return data


def get_stored_token(provider: str) -> str | None:
    profile = _openid_state_profile(provider)
    if not isinstance(profile, dict):
        return None

    token = profile.get("token")
    if not isinstance(token, str) or not token.strip():
        return None

    if _is_oauth_provider(provider) and profile.get("source") == "oauth":
        if _is_oauth_token_expired(profile):
            refreshed = _refresh_openai_token(profile)
            if refreshed:
                return refreshed
        return token.strip()

    return token.strip()


def get_token_source(provider: str) -> tuple[str | None, str | None]:
    env_key = PROVIDER_ENV_MAP.get(provider, "")
    env_token = os.getenv(env_key, "") if env_key else ""
    if env_token:
        return env_token, f"env:{env_key}"

    token = get_stored_token(provider)
    if token:
        return token, "stored"
    return None, None


def delete_token(provider: str) -> bool:
    data = _load_profiles()
    if provider == "all":
        if not data:
            return False
        data.clear()
        _save_profiles(data)
        return True
    if provider not in data:
        return False
    del data[provider]
    _save_profiles(data)
    return True


def mask_token(token: str, show: int = 6) -> str:
    token = token.strip()
    if not token:
        return ""
    if len(token) <= show:
        return "*" * len(token)
    return f"{token[:4]}{'*' * max(4, len(token) - 10)}{token[-4:]}"


def provider_login_url(provider: str) -> str:
    return PROVIDER_LOGIN_URL.get(provider, "")


def _resolve_codex_home() -> Path:
    raw_home = os.getenv("CODEX_HOME", str(Path.home() / ".codex"))
    home = Path(raw_home).expanduser()
    try:
        return home.resolve()
    except Exception:
        return home


def _resolve_codex_auth_path() -> Path:
    return _resolve_codex_home() / "auth.json"


def _compute_codex_keychain_account(codex_home: Path) -> str:
    digest = sha256(str(codex_home).encode("utf-8")).hexdigest()
    return f"cli|{digest[:16]}"


def _parse_epoch_like(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            parsed = int(text)
        else:
            try:
                parsed = int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
            except Exception:
                return None
    else:
        return None
    if parsed > 10_000_000_000:
        return parsed // 1000
    return parsed


def _extract_codex_tokens(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None

    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not isinstance(access, str) or not access.strip():
        return None
    if not isinstance(refresh, str) or not refresh.strip():
        return None

    return {
        "access_token": access.strip(),
        "refresh_token": refresh.strip(),
        "account_id": str(tokens.get("account_id")).strip() if tokens.get("account_id") else None,
        "expires_at": _parse_epoch_like(tokens.get("expires_at")),
        "last_refresh": _parse_epoch_like(payload.get("last_refresh")),
    }


def _read_codex_tokens_from_keychain() -> dict[str, Any] | None:
    if sys.platform != "darwin":
        return None

    codex_home = _resolve_codex_home()
    account = _compute_codex_keychain_account(codex_home)
    try:
        completed = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                CODEX_AUTH_SERVICE_NAME,
                "-a",
                account,
                "-w",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None

    if completed.returncode != 0:
        return None

    try:
        payload = json.loads((completed.stdout or "").strip())
    except Exception:
        return None

    token_payload = _extract_codex_tokens(payload)
    if not token_payload:
        return None
    if token_payload.get("expires_at") is None and token_payload.get("last_refresh"):
        token_payload["expires_at"] = int(token_payload["last_refresh"]) + 3600
    token_payload["source"] = "keychain"
    token_payload["codex_home"] = str(codex_home)
    return token_payload


def _read_codex_tokens_from_auth_file() -> dict[str, Any] | None:
    auth_path = _resolve_codex_auth_path()
    if not auth_path.exists():
        return None

    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    token_payload = _extract_codex_tokens(payload)
    if not token_payload:
        return None
    if token_payload.get("expires_at") is None and token_payload.get("last_refresh"):
        token_payload["expires_at"] = int(token_payload["last_refresh"]) + 3600
    token_payload["source"] = "file"
    token_payload["auth_path"] = str(auth_path)
    return token_payload


def _read_codex_tokens() -> dict[str, Any] | None:
    keychain_tokens = _read_codex_tokens_from_keychain()
    if keychain_tokens:
        return keychain_tokens
    return _read_codex_tokens_from_auth_file()


def _persist_codex_openai_profile(token_payload: dict[str, Any]) -> str | None:
    access = token_payload.get("access_token")
    if not isinstance(access, str) or not access.strip():
        return None

    metadata: dict[str, Any] = {
        "source": "oauth_codex_cli",
        "oauth": True,
        "issued_at": _now_iso(),
        "token_type": "Bearer",
        "account_id": token_payload.get("account_id"),
        "refresh_token": token_payload.get("refresh_token"),
        "codex_source": token_payload.get("source"),
        "codex_home": token_payload.get("codex_home"),
        "codex_auth_path": token_payload.get("auth_path"),
    }
    expires_at = _to_int(token_payload.get("expires_at"))
    if expires_at:
        metadata["expires_at"] = expires_at
    _save_provider_profile("openai", access.strip(), source="oauth_codex_cli", metadata=metadata)
    return access.strip()


def _launch_codex_login(open_browser: bool = True) -> bool:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        print("Codex CLI가 필요합니다. `npm install -g @openai/codex` 설치 후 다시 시도하세요.")
        return False
    if not open_browser:
        print("Codex CLI 로그인은 내부 브라우저/URL 플로우를 사용합니다.")
    print("OpenAI OAuth를 위해 Codex CLI 로그인(`codex login`)을 시작합니다.")
    try:
        completed = subprocess.run([codex_bin, "login"], check=False)
    except Exception as exc:
        print(f"`codex login` 실행 실패: {exc}")
        return False
    if completed.returncode != 0:
        print(f"`codex login`이 실패했습니다 (exit={completed.returncode}).")
        return False
    return True


def _interactive_login_openai(open_browser: bool = True, force_reauth: bool = False) -> str | None:
    if not force_reauth:
        existing = _read_codex_tokens()
        if existing:
            token = _persist_codex_openai_profile(existing)
            if token:
                print("Codex CLI OAuth 토큰을 재사용합니다.")
                return token

    if not sys.stdin.isatty():
        return None

    if not _launch_codex_login(open_browser=open_browser):
        return None

    token_payload = _read_codex_tokens()
    if not token_payload:
        print("`codex login` 완료 후 토큰을 찾지 못했습니다. `codex login`을 직접 다시 실행해 주세요.")
        return None

    token = _persist_codex_openai_profile(token_payload)
    if token:
        print("OpenAI OAuth(Codex) 인증이 완료되었습니다.")
    return token


def interactive_login(
    provider: str,
    open_browser: bool = True,
    token: str | None = None,
    use_oauth: bool | None = None,
    force_reauth: bool = False,
) -> str | None:
    provider = provider.lower().strip()
    if provider not in PROVIDER_ENV_MAP:
        return None

    if token and token.strip():
        _save_provider_profile(provider, token.strip(), source="manual")
        return token.strip()

    if use_oauth is None:
        use_oauth = provider == "openai"

    if not sys.stdin.isatty():
        return None

    if provider == "openai" and use_oauth:
        return _interactive_login_openai(open_browser=open_browser, force_reauth=force_reauth)

    if open_browser:
        url = provider_login_url(provider)
        if url:
            print(f"브라우저에서 {provider} 인증 페이지를 엽니다: {url}")
            try:
                webbrowser.open(url)
            except Exception:
                print("브라우저 자동 실행을 실패했습니다. 위 주소를 직접 열어주세요.")

    prompt_map = {"openai": "OpenAI API 키", "gemini": "Gemini API 키"}
    value = getpass.getpass(f"{prompt_map.get(provider, 'API 토큰')} 입력 (빈 값이면 취소): ").strip()
    if not value:
        return None

    _save_provider_profile(provider, value, source="manual")
    return value


def resolve_auth(
    provider: str,
    strategy: str = "reuse",
    method: str = "auto",
    *,
    open_browser: bool = True,
) -> tuple[str | None, str | None]:
    provider = provider.lower().strip()
    strategy = strategy.lower().strip()
    method = method.lower().strip()
    if provider not in PROVIDER_ENV_MAP:
        return None, None
    if strategy not in {"reuse", "fresh"}:
        strategy = "reuse"
    if method not in {"auto", "oauth", "manual"}:
        method = "auto"

    use_oauth: bool | None = None
    if provider == "openai":
        if method == "oauth":
            use_oauth = True
        elif method == "manual":
            use_oauth = False
        else:
            use_oauth = True

    def _matches_selected_method(
        token_value: str | None,
        source_value: str | None,
        provider_value: str,
        method_value: str,
    ) -> bool:
        if not token_value:
            return False
        if provider_value != "openai" or method_value == "auto":
            return True

        # Environment token source cannot carry explicit metadata.
        # Treat JWT-like tokens as oauth candidates, otherwise manual.
        if source_value and source_value.startswith("env:"):
            if method_value == "oauth":
                return token_value.count(".") == 2
            return True

        profile = _openid_state_profile(provider_value)
        if not isinstance(profile, dict):
            return False
        profile_source = str(profile.get("source", "")).strip().lower()
        if method_value == "oauth":
            return profile_source.startswith("oauth")
        return not profile_source.startswith("oauth")

    if strategy == "fresh":
        token = interactive_login(
            provider=provider,
            open_browser=open_browser,
            use_oauth=use_oauth,
            force_reauth=(provider == "openai" and use_oauth is True),
        )
        if not token:
            return None, None
        write_env_if_set(provider, token)
        profile = _openid_state_profile(provider)
        if isinstance(profile, dict):
            profile_source = str(profile.get("source", "")).strip()
            if profile_source:
                return token, profile_source
        return token, "fresh"

    token, source = get_token_source(provider)
    if token and _matches_selected_method(token, source, provider, method):
        write_env_if_set(provider, token)
        return token, source or "stored"

    token = interactive_login(
        provider=provider,
        open_browser=open_browser,
        use_oauth=use_oauth,
        force_reauth=False,
    )
    if not token:
        return None, None
    write_env_if_set(provider, token)
    profile = _openid_state_profile(provider)
    if isinstance(profile, dict):
        profile_source = str(profile.get("source", "")).strip()
        if profile_source:
            return token, profile_source
    return token, "fresh"


def write_env_if_set(provider: str, token: str | None) -> None:
    env_key = PROVIDER_ENV_MAP.get(provider)
    if not env_key:
        return
    if token:
        os.environ[env_key] = token
    else:
        os.environ.pop(env_key, None)


def list_status() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for provider, env_key in PROVIDER_ENV_MAP.items():
        token, source = get_token_source(provider)
        if token:
            result[provider] = {
                "status": "configured",
                "mask": mask_token(token),
                "source": source or "unknown",
                "env_key": env_key,
            }
        else:
            result[provider] = {
                "status": "not-configured",
                "mask": "",
                "source": "none",
                "env_key": env_key,
            }
    return result


def build_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaia auth",
        description="GAIA authentication helpers",
    )
    subparsers = parser.add_subparsers(dest="auth_command", required=False)

    login_parser = subparsers.add_parser("login", help="Store provider credentials for GAIA")
    login_parser.add_argument("--provider", choices=tuple(PROVIDER_ENV_MAP.keys()), required=True)
    login_parser.add_argument("--token", help="Token/API key to store directly")
    login_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open provider login page in browser",
    )
    login_parser.add_argument(
        "--method",
        choices=("auto", "oauth", "manual"),
        default="auto",
        help="인증 방식 선택: auto(기본), oauth(브라우저 로그인), manual(API 키 입력)",
    )

    subparsers.add_parser("status", help="Show configured auth providers")

    logout_parser = subparsers.add_parser("logout", help="Clear stored token")
    logout_parser.add_argument("--provider", choices=("openai", "gemini", "all"), required=True)

    return parser


def run_auth(argv: list[str] | None = None) -> int:
    parser = build_auth_parser()
    args = parser.parse_args(list(argv or []))

    if args.auth_command == "login":
        provider = str(args.provider)
        use_oauth = None
        if args.method == "oauth":
            use_oauth = True
        elif args.method == "manual":
            use_oauth = False

        token = interactive_login(
            provider=provider,
            open_browser=not args.no_browser,
            token=args.token,
            use_oauth=use_oauth,
            force_reauth=(provider == "openai" and use_oauth is True),
        )
        if not token:
            print("토큰이 입력되지 않아 로그인에 실패했습니다.")
            return 1
        write_env_if_set(provider, token)
        print(f"{provider} 인증 정보가 저장되었습니다.")
        return 0

    if args.auth_command == "status":
        for provider, value in list_status().items():
            if value["status"] == "configured":
                print(
                    f"- {provider}: {value['status']} "
                    f"({value['source']}) [{value['mask']}]"
                )
            else:
                print(f"- {provider}: {value['status']}")
        return 0

    if args.auth_command == "logout":
        provider = str(args.provider)
        if delete_token(provider):
            print(f"{provider} 저장 토큰이 삭제되었습니다.")
            return 0
        print(f"{provider} 저장 토큰이 없습니다.")
        return 1

    parser.print_help()
    return 0
