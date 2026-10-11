"""GitHub-only storage. Network objects live in RAM; this module never writes files."""
import base64
import hashlib
import json
import re
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler


class AtlasError(Exception):
    pass


class APIError(AtlasError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class UnknownOutcome(AtlasError):
    def __init__(self, commit, message):
        self.commit = commit
        super().__init__(message)


def require(condition, message):
    if not condition:
        raise AtlasError(message)


def blob_sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def validate_config(config):
    require(config.get("schema_version") == 2 and config.get("provider") == "github",
            "Requires schema_version=2 and provider=github; local storage is unsupported")
    require(not set(config) - {"schema_version", "provider", "remote", "branch", "credentials"},
            "Unsupported config fields; checkout, sync and local storage are not supported")
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?",
                         config.get("remote", ""))
    require(match and match[2] not in {".", ".."}, "Use a clean HTTPS github.com repository URL")
    branch = config.get("branch")
    require(isinstance(branch, str) and branch and
            not re.search(r"[\x00-\x20\x7f ~^:?*\[\\]", branch) and
            not any(s in branch for s in ("..", "@{", "//")) and
            not branch.startswith(("/", "-")) and not branch.endswith(("/", ".")) and
            all(not part.startswith(".") and not part.endswith(".lock") for part in branch.split("/")),
            "Invalid branch name")
    credentials = config.get("credentials", {})
    require(set(credentials) == {"type", "token"} and credentials.get("type") == "pat" and
            isinstance(credentials.get("token"), str) and credentials["token"].strip() and
            not re.search(r"\s", credentials["token"]), "credentials requires a plaintext PAT token")
    return match[1], match[2]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward Authorization to a redirect target.


class GitHub:
    def __init__(self, config):
        self.owner, self.repo = validate_config(config)
        self.branch = config["branch"]
        self.token = config["credentials"]["token"]
        self.base = "https://api.github.com/repos/" + self.owner + "/" + self.repo
        self.opener = build_opener(NoRedirect())

    def request(self, method, endpoint, payload=None):
        require(endpoint.startswith("/") or not endpoint, "Invalid API endpoint")
        request = Request(self.base + endpoint,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": "Bearer " + self.token,
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28",
                                   "User-Agent": "personal-atlas-remote",
                                   "Content-Type": "application/json"}, method=method)
        try:
            with self.opener.open(request, timeout=45) as response:
                return json.load(response)
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace").replace(self.token, "[REDACTED]")
            raise APIError(error.code, "GitHub HTTP {}: {}".format(error.code, detail[:4000])) from None
        except (URLError, TimeoutError, OSError) as error:
            raise AtlasError("GitHub network error: " + str(error).replace(self.token, "[REDACTED]")) from None
        except ValueError:
            raise AtlasError("GitHub returned an invalid JSON response") from None

    def head(self, allow_missing=False):
        try:
            return self.request("GET", "/git/ref/heads/" + quote(self.branch, safe="/"))["object"]["sha"]
        except APIError as error:
            if allow_missing and error.status in (404, 409):
                return None
            raise

    def snapshot(self, expected=None):
        commit = self.head()
        require(not expected or commit == expected, "Snapshot changed; restart the affected query")
        return RemoteFiles(self, commit)

    def publish(self, files, proposed, message):
        require(self.head() == files.commit, "Stale base_commit; inspect remote changes and rebuild plan")
        changes = []
        for path in sorted(set(files) | set(proposed)):
            if path not in proposed:
                changes.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                continue
            content = proposed[path]
            require(isinstance(content, bytes), "Remote writes require in-memory bytes")
            if path in files and files.entries[path]["sha"] == blob_sha(content):
                continue
            blob = self.request("POST", "/git/blobs", {
                "content": base64.b64encode(content).decode(), "encoding": "base64"})
            require(blob["sha"] == blob_sha(content), "Uploaded blob hash mismatch")
            changes.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if not changes:
            return {"status": "unchanged", "commit": files.commit, "remote_confirmed": True}
        tree = self.request("POST", "/git/trees", {"base_tree": files.tree_sha, "tree": changes})
        target = self.request("POST", "/git/commits", {
            "message": message, "tree": tree["sha"], "parents": [files.commit]})["sha"]
        require(self.head() == files.commit, "Concurrent remote edit; rebuild the operation from latest state")
        try:
            self.request("PATCH", "/git/refs/heads/" + quote(self.branch, safe="/"),
                         {"sha": target, "force": False})
        except AtlasError as error:
            # Never retry a mutation automatically after a lost response.
            try:
                if self.head() == target:
                    return {"status": "remote_saved", "commit": target, "remote_confirmed": True,
                            "changed_files": len(changes)}
            except AtlasError:
                pass
            raise UnknownOutcome(target, "Remote publication not confirmed; inspect operation on remote. " + str(error)) from None
        try:
            confirmed = self.head() == target
        except AtlasError:
            confirmed = False
        if not confirmed:
            raise UnknownOutcome(target, "Remote HEAD confirmation unavailable or branch advanced; inspect operation on remote")
        return {"status": "remote_saved", "commit": target, "remote_confirmed": True,
                "changed_files": len(changes)}


class RemoteFiles(Mapping):
    """Fixed-commit tree inventory and lazily fetched, hash-verified blobs in RAM."""
    def __init__(self, api, commit):
        self.api, self.commit, self.cache = api, commit, {}
        self.tree_sha = api.request("GET", "/git/commits/" + commit)["tree"]["sha"]
        self.entries = {}
        tree = api.request("GET", "/git/trees/" + self.tree_sha + "?recursive=1")
        if tree.get("truncated"):
            self._walk(self.tree_sha)
        else:
            for item in tree["tree"]:
                if item["type"] != "tree":
                    self._add(item["path"], item)

    def _add(self, path, item):
        require(path and not path.startswith("/") and
                all(p not in {"", ".", "..", ".git"} for p in path.split("/")), "Unsafe remote path")
        require(item["type"] == "blob" and item["mode"] in {"100644", "100755"},
                "Remote symlinks and submodules are unsupported")
        self.entries[path] = item

    def _walk(self, sha, prefix=""):
        tree = self.api.request("GET", "/git/trees/" + sha)
        require(not tree.get("truncated"), "Non-recursive tree truncated; cannot prove inventory completeness")
        for item in tree["tree"]:
            path = prefix + item["path"]
            if item["type"] == "tree":
                self._walk(item["sha"], path + "/")
            else:
                self._add(path, item)

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __contains__(self, key):
        return key in self.entries

    def __getitem__(self, path):
        item = self.entries[path]
        if path not in self.cache:
            blob = self.api.request("GET", "/git/blobs/" + item["sha"])
            require(blob.get("encoding") == "base64", "Unsupported GitHub blob encoding")
            value = base64.b64decode(blob["content"])
            require(blob_sha(value) == item["sha"], "Remote blob hash mismatch")
            self.cache[path] = value
        return self.cache[path]

    def url(self, path):
        return "https://github.com/{}/{}/blob/{}/{}".format(
            self.api.owner, self.api.repo, self.commit, quote(path, safe="/"))
