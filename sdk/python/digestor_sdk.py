from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


class DigestorApiError(Exception):
    pass


@dataclass
class DigestorClient:
    base_url: str
    timeout: int = 30
    token: str | None = None

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _raise_for_status(self, response: requests.Response) -> None:
        if response.ok:
            return
        detail = response.text
        try:
            detail = response.json()
        except Exception:
            pass
        raise DigestorApiError(f"HTTP {response.status_code}: {detail}")

    def sandbox_signup(self, username: str | None = None, password: str | None = None) -> dict[str, Any]:
        payload = {"username": username, "password": password}
        response = requests.post(
            f"{self.base_url}/auth/sandbox/signup",
            json=payload,
            timeout=self.timeout,
            headers=self._headers(),
        )
        self._raise_for_status(response)
        data = response.json()
        self.token = data["access_token"]
        return data

    def login(self, username: str, password: str) -> dict[str, Any]:
        response = requests.post(
            f"{self.base_url}/auth/login",
            json={"username": username, "password": password},
            timeout=self.timeout,
            headers=self._headers(),
        )
        self._raise_for_status(response)
        data = response.json()
        self.token = data["access_token"]
        return data

    def upload_document(self, file_path: str | Path, document_type: str = "csf") -> dict[str, Any]:
        path = Path(file_path)
        with path.open("rb") as file_obj:
            response = requests.post(
                f"{self.base_url}/v1/documents",
                data={"document_type": document_type},
                files={"file": (path.name, file_obj, "application/pdf")},
                timeout=self.timeout,
                headers=self._headers(),
            )
        self._raise_for_status(response)
        return response.json()

    def get_document(self, document_id: str) -> dict[str, Any]:
        response = requests.get(
            f"{self.base_url}/v1/documents/{document_id}",
            timeout=self.timeout,
            headers=self._headers(),
        )
        self._raise_for_status(response)
        return response.json()
