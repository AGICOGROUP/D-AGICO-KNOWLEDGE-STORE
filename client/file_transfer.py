"""Binary transfer runs beside the actual file, outside the model context."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx


class FileTransfer:
    def __init__(self, base_url, token, transport=None):
        self.base = base_url.rstrip("/")
        parsed = urlsplit(self.base)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or "\\" in self.base
        ):
            raise ValueError("服务地址不能包含凭证、查询参数或片段")
        if not parsed.hostname or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            )
        ):
            raise ValueError("公司服务必须使用 HTTPS；HTTP 只用于本机测试")
        self.origin = (
            parsed.scheme,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        self.prefix = parsed.path.rstrip("/") + "/v1/"
        self.http = httpx.Client(
            headers={"Authorization": "Bearer " + token},
            follow_redirects=False,
            timeout=httpx.Timeout(120, connect=10),
            transport=transport,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.http.close()

    def checked_url(self, reference):
        if reference.startswith("//") or "\\" in reference:
            raise ValueError("无效文件引用")
        parsed = urlsplit(reference)
        if not parsed.scheme:
            reference = self.base + "/" + reference.lstrip("/")
            parsed = urlsplit(reference)
        origin = (
            parsed.scheme,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        if (
            origin != self.origin
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not parsed.path.startswith(self.prefix)
        ):
            raise ValueError("拒绝向非配置服务发送凭证")
        return reference

    def _request(self, method, path, **kwargs):
        response = self.http.request(method, self.checked_url(path), **kwargs)
        response.raise_for_status()
        return response

    def upload(self, path, metadata, idempotency_key=None):
        path = Path(path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for data in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(data)
        size = path.stat().st_size
        prepared = self._request(
            "POST",
            "/v1/uploads",
            headers={"Idempotency-Key": idempotency_key or uuid4().hex},
            json={"filename": path.name, "size": size, "sha256": digest.hexdigest()},
        ).json()
        with path.open("rb") as stream:
            self._request(
                "PUT", prepared["upload_url"], content=stream, headers={"Content-Length": str(size)}
            )
        return self._request(
            "POST", "/v1/submissions", json={**metadata, "upload_id": prepared["upload_id"]}
        ).json()

    def download(self, version_id, target):
        version_id = str(UUID(str(version_id)))
        reference = self._request("GET", f"/v1/versions/{version_id}/file").json()
        url = self.checked_url(reference["download_url"])
        target = Path(target)
        if target.exists():
            raise FileExistsError("目标文件已存在，请指定新路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + "." + uuid4().hex + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            with temp.open("xb") as output:
                with self.http.stream("GET", url) as response:
                    response.raise_for_status()
                    for data in response.iter_bytes(1024 * 1024):
                        size += len(data)
                        if size > reference["size"]:
                            raise ValueError("下载内容超过声明大小")
                        digest.update(data)
                        output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if size != reference["size"] or digest.hexdigest() != reference["sha256"]:
                raise ValueError("下载大小或 SHA-256 校验失败")
            # Windows rename and POSIX link both refuse to overwrite an existing target.
            # The destination becomes visible only after the entire file passed validation.
            if os.name == "nt":
                temp.rename(target)
            else:
                os.link(temp, target)
            return {"path": str(target.resolve()), "sha256": digest.hexdigest(), "size": size}
        finally:
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="现有入口的知识库文件上传／下载适配")
    commands = parser.add_subparsers(dest="command", required=True)
    upload = commands.add_parser("upload")
    upload.add_argument("path", type=Path)
    upload.add_argument("--organization", required=True)
    upload.add_argument("--title", required=True)
    upload.add_argument("--category", default="other")
    upload.add_argument("--idempotency-key")
    download = commands.add_parser("download")
    download.add_argument("version_id")
    download.add_argument("target", type=Path)
    args = parser.parse_args()
    with FileTransfer(os.environ["AGICO_KB_URL"], os.environ["AGICO_KB_TOKEN"]) as adapter:
        if args.command == "upload":
            result = adapter.upload(
                args.path,
                {
                    "organization_id": args.organization,
                    "title": args.title,
                    "category_id": args.category,
                },
                args.idempotency_key,
            )
        else:
            result = adapter.download(args.version_id, args.target)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
