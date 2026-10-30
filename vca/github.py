"""PR del artículo en GitHub: abrir, consultar CI, fusionar y cerrar.

Usa un token fine-grained limitado al repositorio de la web, con permisos de
Contents y Pull requests en escritura y Checks y Commit statuses en lectura.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import http

API = "https://api.github.com"
OK_CONCLUSIONS = {"success", "neutral", "skipped"}


class GitHub:
    def __init__(self, token: str, repo: str):
        self.token = token
        self.repo = repo
        self.owner = repo.split("/", 1)[0]

    def _call(self, method: str, path: str, body: Any = None, params: Optional[Dict[str, Any]] = None) -> Any:
        return http.request_json(
            method,
            f"{API}/repos/{self.repo}{path}",
            json_body=body,
            params=params,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def find_pr(self, branch: str) -> Optional[Dict[str, Any]]:
        prs = self._call("GET", "/pulls", params={"head": f"{self.owner}:{branch}", "state": "open"})
        return prs[0] if prs else None

    def open_pr(self, branch: str, base: str, title: str, body: str) -> Tuple[int, str]:
        existing = self.find_pr(branch)
        if existing:
            self._call("PATCH", f"/pulls/{existing['number']}", {"title": title, "body": body})
            return existing["number"], existing["html_url"]
        pr = self._call("POST", "/pulls", {"title": title, "head": branch, "base": base, "body": body})
        return pr["number"], pr["html_url"]

    def checks(self, sha: str) -> Dict[str, Any]:
        """Estado de CI de un commit: total, pendientes, fallidos y si está todo verde."""
        runs = self._call("GET", f"/commits/{sha}/check-runs", params={"per_page": 100}).get("check_runs", [])
        pending = [r["name"] for r in runs if r.get("status") != "completed"]
        failed = [
            r["name"] for r in runs
            if r.get("status") == "completed" and r.get("conclusion") not in OK_CONCLUSIONS
        ]
        status = self._call("GET", f"/commits/{sha}/status")
        if status.get("total_count"):
            for s in status.get("statuses", []):
                if s.get("state") == "pending":
                    pending.append(s.get("context", "status"))
                elif s.get("state") in ("failure", "error"):
                    failed.append(s.get("context", "status"))
        total = len(runs) + int(status.get("total_count") or 0)
        return {"total": total, "pending": pending, "failed": failed, "ok": total > 0 and not pending and not failed}

    def merge(self, number: int, title: str) -> str:
        result = self._call("PUT", f"/pulls/{number}/merge", {"merge_method": "merge", "commit_title": title})
        return result.get("sha", "")

    def close(self, number: int) -> None:
        self._call("PATCH", f"/pulls/{number}", {"state": "closed"})

    def delete_branch(self, branch: str) -> None:
        try:
            self._call("DELETE", f"/git/refs/heads/{branch}")
        except http.HttpError as exc:
            if exc.status not in (404, 422):
                raise

    def repo_info(self) -> Dict[str, Any]:
        return http.request_json(
            "GET", f"{API}/repos/{self.repo}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"},
        )


def failed_summary(names: List[str]) -> str:
    return ", ".join(sorted(set(names))) or "sin detalle"
