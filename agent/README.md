# Agent 模块

## 职责

Agent 模块是系统的"大脑"，负责：
- 接收用户的调研主题
- 通过 ReAct 推理循环自主规划和执行调研流程
- 协调各工具的调用顺序和参数
- 生成最终的结构化综述

## 文件说明

| 文件 | 职责 |
|------|------|
| `core.py` | Agent 主类，负责 LLM / 工具集 / 执行器的初始化和运行 |
| `prompts.py` | 所有 Prompt 模板的集中管理（System Prompt、提取 Prompt、评估 Prompt） |

## 核心设计决策

### 为什么用 ReAct 而不是 Plan-and-Execute

文献调研是**探索性任务**：你在读第 3 篇论文时才知道第 4 步应该去搜什么。  
ReAct 的 Thought → Action → Observation 循环允许 Agent 每一步都根据最新观察做决策。

Plan-and-Execute 的问题在于它要求先生成完整计划——但调研初期你不知道这个领域有哪些子方向，无法提前规划。

### Prompt 设计原则

1. **给原则，不给脚本**：System Prompt 中给出"先广后深""定期自评"等原则，而非写死"第一步搜 A，第二步搜 B"
2. **嵌入自控机制**：通过显式指令（"每读 3-5 篇后调用 assess_coverage"）防止 Agent 过早停止或无限发散
3. **目标回顾**：要求 Agent 定期回顾原始主题，缓解 ReAct 长链推理偏离问题

### handle_parsing_errors=True

LLM 有时会输出不符合 ReAct 格式的内容（比如忘记写 Action）。开启此选项后 LangChain 会自动将解析错误信息反馈给 LLM，让它重新生成，而不是直接抛异常。这是一个简单但很重要的鲁棒性措施。

## 扩展方向

- 加入对话历史管理（ConversationBufferMemory），支持多轮调研
- 支持用户中途介入（暂停 Agent → 用户给反馈 → Agent 继续）
- 替换为 LangGraph 实现更细粒度的流程控制（如果 ReAct 的自由度过高导致不稳定）
