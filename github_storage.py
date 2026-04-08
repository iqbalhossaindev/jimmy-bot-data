import base64
import csv
import io
import json
import requests
from typing import Any, Dict, List, Tuple
from config import GITHUB_BRANCH, GITHUB_OWNER, GITHUB_REPO, GITHUB_TOKEN

class GitHubStorage:
    def __init__(self) -> None:
        self.base_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents"
        self.headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        }

    def _get_file(self, path: str) -> Tuple[str, str]:
        url = f"{self.base_url}/{path}"
        res = requests.get(url, headers=self.headers, params={"ref": GITHUB_BRANCH}, timeout=30)
        res.raise_for_status()
        data = res.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        sha = data["sha"]
        return content, sha

    def _put_file(self, path: str, content: str, message: str) -> None:
        sha = None
        try:
            _, sha = self._get_file(path)
        except Exception:
            sha = None

        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        url = f"{self.base_url}/{path}"
        res = requests.put(url, headers=self.headers, json=payload, timeout=30)
        res.raise_for_status()

    def read_json(self, path: str, default: Any) -> Any:
        try:
            content, _ = self._get_file(path)
            return json.loads(content)
        except Exception:
            return default

    def write_json(self, path: str, data: Any, message: str) -> None:
        self._put_file(path, json.dumps(data, indent=2, ensure_ascii=False), message)

    def read_csv_rows(self, path: str) -> List[Dict[str, str]]:
        try:
            content, _ = self._get_file(path)
            reader = csv.DictReader(io.StringIO(content))
            return list(reader)
        except Exception:
            return []

    def write_csv_rows(self, path: str, fieldnames: List[str], rows: List[Dict[str, Any]], message: str) -> None:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        self._put_file(path, output.getvalue(), message)
