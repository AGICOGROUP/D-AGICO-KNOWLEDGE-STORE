from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from conftest import headers, submit

from agico_kb.worker import Worker


def test_crashed_worker_slot_expires_quickly_and_heartbeat_is_fenced(kb):
    client, db, _ = kb
    submit(client, title='Crash recovery PDF slot')
    worker = Worker(db, client.app.state.settings)
    claim = worker.claim()
    with db.connection() as conn:
        left = conn.execute('SELECT extract(epoch FROM lease_until-now()) AS seconds FROM jobs WHERE id=%s', (claim['id'],)).fetchone()['seconds']
    assert 0 < left <= 120
    assert not worker.renew_lease(claim['id'], uuid4())
    assert worker.renew_lease(claim['id'], claim['generation'])
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (claim['id'],))
    assert not worker.renew_lease(claim['id'], claim['generation'])
    resumed = worker.claim()
    assert resumed['id'] == claim['id']
    assert resumed['generation'] != claim['generation']
    assert not worker.renew_lease(claim['id'], claim['generation'])


def test_claim_fifo_global_five_limit_and_fenced_restart(kb):
    client, db, _ = kb
    versions = [submit(client, title=f"Fresh slot {i}")[0].json()["version_id"] for i in range(6)]
    workers = [Worker(db, client.app.state.settings) for _ in range(6)]
    claims = [workers[i].claim() for i in range(5)]
    assert [str(claim["version_id"]) for claim in claims] == versions[:5]
    assert workers[5].claim() is None
    first = claims[0]
    assert not workers[0].release_interrupted(first["id"], uuid4())
    assert workers[5].claim() is None
    assert workers[0].release_interrupted(first["id"], first["generation"])
    resumed = workers[5].claim()
    assert resumed["version_id"] == first["version_id"]
    assert resumed["generation"] != first["generation"]
    assert resumed["attempts"] == 1
    assert not workers[0].release_interrupted(first["id"], first["generation"])
    pending = client.get("/v1/submissions?limit=20", headers=headers("chief")).json()
    assert [item["version_id"] for item in pending["items"]] == versions


def test_parallel_claimers_cannot_exceed_five_running_jobs(kb):
    client, db, _ = kb
    versions = [submit(client, title=f"Parallel slot {i}")[0].json()["version_id"] for i in range(8)]
    workers = [Worker(db, client.app.state.settings) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        claims = list(executor.map(lambda worker: worker.claim(), workers))
    accepted = [claim for claim in claims if claim is not None]
    assert len(accepted) == 5
    assert {str(claim["version_id"]) for claim in accepted} == set(versions[:5])


def test_heavy_pdfs_leave_execution_capacity_for_small_office_uploads(kb):
    client, db, _ = kb

    def enqueue(filename):
        content = ('Fresh scheduler fixture ' + uuid4().hex).encode()
        upload = client.post('/v1/uploads', headers=headers(key=uuid4().hex),
                             json={'filename': filename, 'size': len(content)}).json()['upload_id']
        assert client.put(f'/v1/uploads/{upload}/content', headers=headers(), content=content).status_code == 200
        response = client.post('/v1/submissions', headers=headers(), json={
            'upload_id': upload, 'organization_id': 'baiste', 'category_id': 'technical',
            'title': filename, 'visibility': 'department'})
        assert response.status_code == 201
        return response.json()['version_id']

    pdfs = [enqueue(f'Long-brochure-{i}.pdf') for i in range(3)]
    office = enqueue('Short-schedule.xlsx')
    worker = Worker(db, client.app.state.settings)
    claims = [worker.claim() for _ in range(3)]
    assert [str(item['version_id']) for item in claims] == [*pdfs[:2], office]
    assert worker.claim() is None  # Third PDF waits instead of occupying every slot.
    assert worker.release_interrupted(claims[0]['id'], claims[0]['generation'])
    resumed = worker.claim()
    assert str(resumed['version_id']) == pdfs[0]
