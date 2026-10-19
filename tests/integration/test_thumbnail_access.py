import shutil
import subprocess
import time
from io import BytesIO
from uuid import uuid4

import pytest
from conftest import headers
from PIL import Image


def test_thumbnail_requires_original_file_visibility(kb):
    client, _, _ = kb
    buffer = BytesIO()
    Image.new("RGBA", (24, 12), (255, 255, 255, 0)).save(buffer, format="PNG")
    content = buffer.getvalue()
    upload = client.post(
        "/v1/uploads",
        headers=headers("alice", uuid4().hex),
        json={"filename": "fresh-logo.png", "size": len(content)},
    ).json()
    assert (
        client.put(
            f"/v1/uploads/{upload['upload_id']}/content", headers=headers(), content=content
        ).status_code
        == 200
    )
    submitted = client.post(
        "/v1/submissions",
        headers=headers(),
        json={
            "upload_id": upload["upload_id"],
            "organization_id": "baiste",
            "category_id": "technical",
            "title": "Fresh logo",
            "visibility": "department",
            "base_revision": 0,
        },
    )
    assert submitted.status_code == 201
    path = f"/v1/versions/{submitted.json()['version_id']}/thumbnail"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=headers("bob")).status_code == 404
    assert client.get(path, headers=headers("peer")).status_code == 404
    for person in ["alice", "chief"]:
        response = client.get(path, headers=headers(person))
        deadline = time.monotonic() + 10
        while response.status_code == 202 and time.monotonic() < deadline:
            time.sleep(0.05)
            response = client.get(path, headers=headers(person))
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "no-store"
        with Image.open(BytesIO(response.content)) as preview:
            assert preview.size == (24, 12)
    # A cached preview must not bypass visibility checks.
    assert client.get(path, headers=headers("bob")).status_code == 404
    assert client.get(path).status_code == 401
    assert client.get(path.replace("/thumbnail", "/content"), headers=headers()).content == content


@pytest.mark.parametrize("suffix", [".dxf", ".mp4", ".pdf", ".txt"])
def test_cad_and_video_background_previews_through_api(kb_large, tmp_path, suffix):
    client, _, _ = kb_large
    source = tmp_path / (uuid4().hex + suffix)
    if suffix == ".pdf":
        import pymupdf

        doc = pymupdf.open()
        doc.new_page().insert_text((30, 40), "Fresh PDF preview")
        doc.save(source)
        doc.close()
    elif suffix == ".txt":
        source.write_text("Fresh original text preview AX-100", encoding="utf-8")
    elif suffix == ".dxf":
        import ezdxf

        doc = ezdxf.new()
        doc.modelspace().add_circle((0, 0), 10)
        doc.saveas(source)
    else:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("FFmpeg not installed")
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=320x180:d=1",
                "-c:v",
                "mpeg4",
                "-y",
                str(source),
            ],
            check=True,
            timeout=15,
        )
    content = source.read_bytes()
    upload = client.post(
        "/v1/uploads",
        headers=headers("alice", uuid4().hex),
        json={"filename": source.name, "size": len(content)},
    ).json()
    assert (
        client.put(
            f"/v1/uploads/{upload['upload_id']}/content", headers=headers(), content=content
        ).status_code
        == 200
    )
    submitted = client.post(
        "/v1/submissions",
        headers=headers(),
        json={
            "upload_id": upload["upload_id"],
            "organization_id": "baiste",
            "category_id": "technical",
            "title": "Fresh media",
            "visibility": "department",
            "base_revision": 0,
        },
    )
    assert submitted.status_code == 201
    path = f"/v1/versions/{submitted.json()['version_id']}/thumbnail"
    response = client.get(path, headers=headers())
    assert response.status_code == 202
    deadline = time.monotonic() + 15
    while response.status_code == 202 and time.monotonic() < deadline:
        time.sleep(0.05)
        response = client.get(path, headers=headers())
    assert response.status_code == 200
    with Image.open(BytesIO(response.content)) as image:
        assert max(image.size) <= 512
        assert image.convert("L").getextrema()[0] < 100
    assert client.get(path, headers=headers("bob")).status_code == 404
    assert source.read_bytes() == content
    assert client.get(path.replace("/thumbnail", "/content"), headers=headers()).content == content
