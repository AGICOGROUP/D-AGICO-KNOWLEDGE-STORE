import hashlib
from uuid import uuid4

import httpx
import pytest
from test_mcp import live_server as live_server  # noqa: PLC0414 - pytest fixture registration

from client.file_transfer import FileTransfer


def test_real_binary_docx_upload_and_download(live_server, tmp_path):
    from docx import Document

    base, _ = live_server
    source = tmp_path / (uuid4().hex + ".docx")
    doc = Document()
    doc.add_paragraph("合成企业资料：型号 AX-210，额定电压 220 V。")
    doc.save(source)
    with FileTransfer(base, "alice") as adapter:
        submitted = adapter.upload(source, {"organization_id": "baiste", "title": "传输测试"})
        target = tmp_path / "下载资料.docx"
        result = adapter.download(submitted["version_id"], target)
    assert target.read_bytes() == source.read_bytes()
    assert result["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example/v1/file",
        "http://safe.example/v1/file",
        "https://safe.example.evil/v1/file",
        "https://user@safe.example/v1/file",
        "//other.example/v1/file",
    ],
)
def test_credentials_never_sent_to_untrusted_origin(url):
    sent = []
    with (
        FileTransfer(
            "https://safe.example",
            "secret-test",
            transport=httpx.MockTransport(lambda r: sent.append(r)),
        ) as adapter,
        pytest.raises(ValueError),
    ):
        adapter.checked_url(url)
    assert sent == []


def test_redirect_is_not_followed_with_credentials(tmp_path):
    seen = []

    def serve(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://untrusted.example/stolen"})

    with (
        FileTransfer(
            "https://safe.example", "test-secret", transport=httpx.MockTransport(serve)
        ) as adapter,
        pytest.raises(httpx.HTTPStatusError),
    ):
        adapter.download(str(uuid4()), tmp_path / "new.bin")
    assert len(seen) == 1 and all(s.startswith("https://safe.example/") for s in seen)


def test_corrupt_download_never_becomes_completed_file(tmp_path):
    vid = str(uuid4())

    def serve(request):
        if request.url.path.endswith("/file"):
            return httpx.Response(
                200,
                json={"download_url": f"/v1/versions/{vid}/content", "sha256": "0" * 64, "size": 3},
            )
        return httpx.Response(200, content=b"bad")

    target = tmp_path / "received.bin"
    with (
        FileTransfer(
            "https://safe.example", "test-secret", transport=httpx.MockTransport(serve)
        ) as adapter,
        pytest.raises(ValueError),
    ):
        adapter.download(vid, target)
    assert not target.exists() and not list(tmp_path.glob("*.part"))
