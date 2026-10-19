from conftest import headers

ORDER = ["general-group", "business-intelligence", "baiste", "baichuang", "xingyuan",
         "aijike", "jimuke", "company-common"]


def test_catalog_and_access_management_use_business_order_without_expanding_scope(kb):
    client, db, _ = kb
    with db.connection(write=True) as conn:
        conn.execute("UPDATE principals SET is_admin=true WHERE id='chief'")
        for org in ORDER:
            conn.execute("INSERT INTO memberships VALUES ('chief',%s,'publisher') ON CONFLICT DO NOTHING", (org,))
    catalog = client.get("/v1/catalog", headers=headers("chief")).json()
    assert [row["id"] for row in catalog["organizations"]] == ORDER
    assert catalog["organizations"][-1]["name"] == "公共知识库"
    admin = client.get("/v1/admin/session", headers=headers("chief")).json()
    assert [row["id"] for row in admin["organizations"]] == ORDER
    assert [row["id"] for row in client.get("/v1/catalog", headers=headers()).json()["organizations"]] == ["baiste"]
