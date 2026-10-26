# 上下文管理与提示词前缀缓存

> 决策文档。写的是「为什么这么做」,实现细节看代码与测试。

## 要解决的问题

客服 Agent 的每轮对话都要把历史发给模型。两个成本压力:

1. **长对话费用爆炸**:滑窗外的历史要压缩,但不能丢关键信息;
2. **重复计费**:每轮请求的前缀(系统提示词 + 工具 schema + 已发生的对话)几乎不变,不缓存意味着同样的 token 每轮都全价付费。

上游(OpenAI 协议)提供**前缀缓存**:两次请求中,从开头起完全相同的 token 段只计一次费,命中还更快。

## 方案

### 1. 一条恒定的 SystemMessage

发给模型的消息列表里**只有一条 SystemMessage,内容恒等于 `AGENT_SYSTEM`**(`app/graph/nodes.py:272-277`)。摘要、检索证据、退款订单数据等每轮可变的内容一律不进 system,而是打包成一条带哨兵 id 的 HumanMessage(`TURN_CTX_ID = "turn-ctx"`,`nodes.py:251-252`),插在滑窗里**最后一条用户消息之后**(`nodes.py:255-266`)。

这样 ReAct 环第 2 步的 `[Sys, 用户问, 材料, AI, Tool]` 完整包含第 1 步的 `[Sys, 用户问, 材料]` 作前缀,**轮内也能命中缓存**。`tests/test_prompt_cache_prefix.py` 把这个插入位置钉死成单测(「step1 的 prompt 必须是 step2 的严格前缀」)。

### 2. 双层滑窗(`app/core/memory.py`)

- **摘要边界切窗**:找第一条 `db-id > summary_upto_msg_id` 的用户消息作为窗口起点(`memory.py:47-58`);
- **token 兜底**:`trim_messages` 按 token 近似计数从尾部裁剪,`start_on="human"`、`allow_partial=False` 不留半条消息(`memory.py:18-26`);锚点缺失(旧会话)退化为纯 token 裁剪;
- 滑窗只现拼、不回写 State(`test_turn_context_is_not_written_back_to_state` 防污染跨轮历史与审计)。

### 3. 后台摘要(`app/core/summarizer.py`)

距上次摘要新增消息 >= 30 条且无在跑任务时,`create_task` 后台异步执行,**不阻塞回复**(`summarizer.py:71-98`)。新边界取倒数第 8 轮用户消息的前一条消息 id(`summarizer.py:24-34`);LLM 把旧摘要 + 新滑出段合并重写;成功后**原子更新** summary 与 summary_upto_msg_id 两字段。失败只 log 不重试——触发条件仍满足,下一轮自然重触发。

## 关键参数与实测

| 项 | 值 |
| - | - |
| 缓存命中(无摘要会话) | `cache_read=2048` |
| 摘要进了 system 之后 | `cache_read=0`(整段作废) |
| 稳定前缀(system+tools 段) | 约 1664 tokens |
| 滑窗 token 上限 | `context_window_max_tokens=3000` |
| 摘要后保留原文轮数 | `context_window_turns=8` |
| 摘要触发阈值 | `summary_trigger_messages=30` |

> 成本实测:开着思考链一句客服回复要 **22.4s / 318 tokens**;关掉后 **1.4s / 16 tokens**(`.env.example` 注释,`app/config.py:20-26`)。

## 设计取舍与坑

- **摘要绝不能单独占一条 system**:上游 chat template 会把所有 system 消息上提合并成一个头部块渲染,摘要每会话不同,会把工具 schema 挤到可变内容之后,前缀缓存整段作废(实测 2048 → 0)。两条单测(`exactly_one_system_message`、`volatile_payload_never_lands_in_any_system_message`)把这条钉死。
- **插入位置在最后一条用户消息后而非列表末尾**:为了 ReAct 轮内命中;追加到末尾则第 2 步公共前缀只到用户问那条。
- **哨兵只能用 id 不能用 name**:name 会被序列化发给上游、改变请求字节;id 不会。
- **cache_read=0 不代表没命中**:上游不报就是 0,要以上游控制台为准,别据此下结论(`nodes.py:322-324`)。
