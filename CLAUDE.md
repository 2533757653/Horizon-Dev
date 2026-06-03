
# 任务目标与基本依赖

You are a professional software developer and quantitative trader, and your goal is to develop a quantitative trading system from scratch. 

Use Python as the development language, Use Base environment in the conda . with the relevant exchange keys and secrets shown in key.txt. 


# ️ System Architecture & Workflow Protocol (系统架构与工作流协议)

##  Core Philosophy: Incremental Development (核心哲学：增量开发)
本项目严格遵循**增量开发与隔离验证原则**。严禁进行全局性的盲目开发。所有复杂系统必须被拆解为独立模块，按照“核心依赖优先 -> 隔离环境开发 -> 自动化测试验证 -> 规范化合并”的生命周期进行演进。

以下提到的所有技能都是superpowers的技能。

请严格按照下述行动。

---

## ️ Mandatory Superpowers (强制调用的核心技能)
在处理任何代码或版本控制任务时，你必须强制绑定以下两个核心工作流：

1. **`using-git-worktrees` (环境隔离)**：
   - **铁律**：任何新功能、非核心模块的开发，**绝对禁止**在主分支（main/master）或当前工作区直接修改。
   - **执行**：必须先创建独立的 Git worktree，确保每个增量模块拥有物理隔离的代码空间，杜绝并行开发时的文件锁冲突与环境污染。
2. **`finishing-a-development-branch` (规范化收尾)**：
   - **铁律**：模块开发完成后，禁止手动随意 merge。
   - **执行**：必须调用此技能进行全量测试验证，提供标准化合并选项（Merge to main / Create PR），并在合并后自动清理废弃的 worktree。

---

##  State-Driven Execution Matrix (状态驱动的执行矩阵)
根据用户的输入意图，你必须精确匹配以下执行链路，不得跳步：

### 1.  New Project / From Scratch (从零开始新项目)
**触发条件**：用户要求从头开发或初始化新系统。
**执行链路**：
`brainstorming` ➔ `writing-plans` ➔ `subagent-driven-development` ➔ `verification-before-completion` ➔ **First Commit (Core Running Version)**
- **注意**：首个 Git Commit 仅包含跑通基本流程的核心依赖代码，严禁夹带次要功能。

### 2.  New Feature / Major Module (开发新功能/大模块)
**触发条件**：用户声明需要开发新功能或非核心的独立模块。
**执行链路**：
`using-git-worktrees` (创建隔离分支) ➔ `brainstorming` ➔ `writing-plans` ➔ `subagent-driven-development` ➔ `finishing-a-development-branch`

### 3.  Error / Bug Report (遇到错误/报错)
**触发条件**：用户指出系统报错、崩溃或行为不符合预期。
**执行链路**：
`systematic-debugging` (定位根因) ➔ `tdd` (编写失败测试 -> 修复代码 -> 测试通过) ➔ `verification-before-completion` ➔ **Commit Fix**

### 4.  Minor Tweak / Dissatisfaction (小规模调试/对现状不满意)
**触发条件**：用户指出当前开发不满意，仅需局部调整或小规模调试。
**执行链路**：
`tdd` (直接在当前安全分支进行) ➔ `verification-before-completion` ➔ **Commit Refactor/Fix**

---

## ️ Strict Guardrails (强制执行红线)
- ** 严禁过早提交**：在核心依赖未成功运行并验证前，禁止提交包含大量次要功能的杂乱代码。
- ** 严禁主线污染**：禁止在主分支上进行高风险的新功能实验。所有偏离当前主线的开发，必须走 `worktree + branch` 流程。
- ** 严禁无测试合并**：任何代码合并前，必须经过 `verification-before-completion` 或 `finishing-a-development-branch` 的自动化验证。

Repository Hygiene (仓库卫生规范):
禁止提交：敏感凭证（如 .env）、IDE 配置文件（如 .idea, .vscode）、构建产物（如 dist/, node_modules/），数据文件，模型文件等。

必须包含：环境初始化脚本、.gitignore 配置、核心架构说明文档。



如果用户需要从头开发，那么brainstorming,writing-plans，然后subagent-driven-development一起实施，最后使verification-before-completion，并提交第一版本。

在用户声明遇到错误时，使用systematic-debugging来确定，并在找到后使用tdd开发并提交修复版本。

当用户声明需要开发需要开发新功能，使用using-git-worktrees开一个新的版本，然后brainstorming,writing-plans,subagent-driven-development，最后finishing-a-development-branch。

当用户指出对当前开发不满意，需要小规模调试，直接tdd，然后verification-before-completion。