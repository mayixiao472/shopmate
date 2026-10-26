# 检索链路:切块 → 双写 → 混合检索 → 重排 → 改写

> 决策文档。写的是「为什么这么做」,实现细节看代码与测试。

## 要解决的问题

知识库要同时服务两类问题:精确的政策条款(「运费多少」)和口语化、跨文档的复杂问题(「猫砂盆坏了能退吗」)。单靠向量检索对精确数字不敏感,单靠关键词检索对口语表达不友好。

## 方案

### 1. 切块(`app/kb/chunking.py`)

Markdown 按 h1-h4 分节,`section_path = " / ".join(标题路径)`(`chunking.py:6,11-13`)。表格块按行切(每块最多 10 行数据行,每块重贴表头+分隔行,表头前散文只留首块);非表格走 RecursiveCharacterTextSplitter,`chunk_size=400`、CJK 分隔符,再用 `apply_sentence_overlap` 把前一块结尾**完整句**拼进本块头部(重叠 60 字)——「不留半截话」(`chunking.py:31-51`)。

### 2. MySQL / Milvus 双写(`app/kb/dualwrite.py`)

chunks 先写 MySQL `knowledge_chunks`(权威存储、审核、审计、prev/next 邻接指针都在库表),再以 pending 游标幂等地向量化:`vectorize_pending` 取 pending → 拼 `category\nquestions\nanswer` → 批量(64)嵌入 → Milvus upsert(PK=MySQL id)→ 回填 vector_id、status=done → **flush 刷盘**(否则新数据 BM25 检索不可见,实证踩过坑)。MySQL 是权威源,Milvus 只是检索索引。

### 3. 混合检索 + RRF(`app/kb/milvus_client.py`)

Milvus collection 同时建:1024 维 dense 向量(COSINE,AUTOINDEX)+ text 字段(chinese analyzer)+ BM25 Function 生成的 sparse 索引。`hybrid_search` 两条 AnnSearchRequest 各召回 `recall=50`,`RRFRanker()`(默认 k=60)融合。BM25 与 dense 用**不同 query 文本**:扩展词只进 BM25,不污染 dense(`retrieval.py:59-61`)。

### 4. 重排(`app/core/rerank.py`)

直连上游 `/rerank` 端点(Jina/Cohere 形状,非 OpenAI 协议,手写 httpx);{429, 500-504}+连接层错误退避重试 3 次、1.5s 翻倍。检索结果精排出 `rerank_top_k=10`,`rerank_score < 0.3` 视为证据弱。`arrange_head_tail` 把最相关放首、次相关放尾,缓解 lost-in-the-middle。

### 5. Query 改写与子句拆分(`app/core/query_understanding.py`)

一次 LLM 调用输出 `{standard 标准问法, expanded 3-6 个同义扩展词}`;**改写失败回落原问题**——「改写只是让检索更好命中,不是检索的前提条件」。一问多意图按标点拆子句(短于 4 字不成子句),各查一遍后轮转合并、按 chunk id 去重、同一小节只留最靠前一条。

## 关键参数与实测

| 项 | 值 |
| - | - |
| 切块 | chunk_size=400、句子重叠 60 字、表格块 ≤10 行 |
| 召回 | dense/BM25 各 Top-50、RRF k=60 |
| 精排 | Top-10、min_score=0.3、重试 3 次退避 1.5s |
| 嵌入模型 | text-embedding-v3(上游真实名,`.env` 配) |

**300 题四桶评估实测**(`data/ch04/reports/rag_eval.txt`):

| 策略 | Recall@5 | MRR |
| - | - | - |
| 纯向量 | 0.97 | 0.84 |
| 纯 BM25 | 0.89 | 0.77 |
| 混合 | 0.94 | 0.82 |
| **混合+重排** | **0.98** | **0.89** |

口语桶(C)BM25 只有 0.77/0.63,混合+重排拉到 1.00/0.97;Faithfulness 除 1 个编造个案外全满,拒答桶 60/60 全拒。最新趋势轮:Recall@5=0.986、MRR=0.897、Faithfulness=1.000。

## 设计取舍与坑

- **子句拆分必须配精排才有收益**:单路检索拆完反而更吵(评估集实测 +0.02 vs -0.04~-0.06)。
- **Milvus 线程模型**:所有同步 pymilvus 调用固定到专用单线程执行器——gRPC 客户端跨线程共享在 uvloop async server 下会间歇静默返回空,实测复现后根治(`milvus_client.py:14-17`)。
- **评估分两段**:确定性段(Recall/MRR/证据覆盖)不依赖 LLM 裁判、可复现;生成段单点故障不拖垮整轮,失败样本按可用样本计(不把「没评上」记成 0 分);裁判模型 temperature=0(0.3 下同一份证据+答案重放四次给过三种结果)。
- 评估指标落 `eval_runs` 表连趋势,任一指标下滑 >0.005 标 ⚠——验证「飞轮写回没把质量拉下来」。
