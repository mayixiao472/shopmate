import uuid

import pytest

from app.kb import milvus_client as mc

URI = "http://localhost:19530"


@pytest.fixture()
def coll():
    try:
        client = mc.get_client(uri=URI)      # 内部等待 channel 就绪,连不上会超时抛错
        client.list_collections()            # 再补一发真实连接:CI 无 Milvus 服务时跳过而非失败
    except Exception as e:                   # noqa: BLE001 - 连接类异常种类多,统一按不可用处理
        pytest.skip(f"Milvus 不可用,跳过本测试({type(e).__name__}: {e})")
    name = f"test_kb_{uuid.uuid4().hex[:8]}"
    mc.ensure_collection(client, collection=name)
    rows = [
        {"id": 1, "dense": [0.1] * 1024, "text": "运费 满99元包邮 不满10元",
         "question": "运费怎么算", "answer": "满99包邮,否则10元",
         "section_path": "运费政策", "content_type": "faq", "category": "运费"},
        {"id": 2, "dense": [0.2] * 1024, "text": "智能猫砂盆 Pro 型号 自动清理",
         "question": "Pro 型号功能", "answer": "自动清理除臭",
         "section_path": "商品手册 / 猫砂盆", "content_type": "manual", "category": "商品手册"},
    ]
    mc.upsert_vectors(client, rows, collection=name)
    mc.flush(client, collection=name)          # 新数据须 flush 才可被检索
    client.load_collection(name)
    import time; time.sleep(2)                  # 等 sparse 索引对新段生效
    yield client, name
    mc.drop(client, name)


def test_bm25_hits_model_number(coll):
    client, name = coll
    hits = mc.bm25_search(client, "猫砂盆 Pro 型号", top_k=2, collection=name)
    assert hits[0]["id"] == 2
    assert hits[0]["section_path"] == "商品手册 / 猫砂盆"


def test_hybrid_returns_scored_hits(coll):
    client, name = coll
    hits = mc.hybrid_search(client, [0.1] * 1024, "运费", top_k=2, recall=10, collection=name)
    assert hits
    assert {"id", "score", "question", "answer", "section_path", "content_type", "category"} <= hits[0].keys()


def test_category_filter(coll):
    client, name = coll
    hits = mc.bm25_search(client, "型号 运费", top_k=5, category="运费", collection=name)
    assert all(h["category"] == "运费" for h in hits)
    assert all(h["id"] == 1 for h in hits)
