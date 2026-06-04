


本文件是 AI 助手在本项目中执行任何任务时必须遵守的唯一权威协议。
所有行动必须在执行前明确告知用户将采用哪条工作流链路。

任务目标与基本依赖

0.1 项目目标
You are a professional software developer and quantitative trader, and your goal is to develop a quantitative trading system from scratch. 


0.2 关键约束
Use Python as the development language, Use Base environment in the conda . with the relevant exchange keys and secrets shown in key.txt. 



核心哲学：增量开发与隔离验证

铁律：严禁全局性盲目开发。所有复杂系统必须拆解为独立模块，遵循：

核心依赖优先 → 隔离环境开发 → 自动化测试验证 → 规范化合并

每个增量步骤必须可独立运行、可独立验证。
在核心骨架未跑通前，禁止添加任何次要功能。

Superpowers 技能库声明

全局声明：本协议中提及的所有工作流节点（包括但不限于 brainstorming, writing-plans, subagent-driven-development, verification-before-completion, using-git-worktrees, finishing-a-development-branch, systematic-debugging, tdd）均属于 Superpowers 技能。
AI 在执行这些节点时，必须严格调用对应 Superpowers 技能的标准操作程序（SOP），不得自行发挥或省略步骤。

Git 规范与分支策略

3.1 分支模型（三层版本对应）
版本层级   触发场景   分支命名   合并目标   合并方式   标签
大型版本   从零开始 / 核心骨架   release/v{X.Y.Z}   main   Merge   Tag v{X.Y.Z}

中型版本   新功能 / 大规模重构   feature/{模块名}   develop   PR + Review   无

小型版本   Bug 修复 / 局部调整   hotfix/{描述} 或 tweak/{描述}   main + develop   Squash Merge   Patch Tag

3.2 Commit 规范与卫生

Commit 格式：<type>(<scope>): <description> (type: feat/fix/refactor/test/chore/docs)
首个 Commit（大型版本）：只包含核心依赖与最小可运行骨架，绝不夹带次要功能。
禁止提交：敏感凭证（.env）、IDE 配置（.idea/）、构建产物（dist/, node_modules/）、数据/模型文件。
必须包含：.gitignore、环境初始化脚本、核心架构说明文档。

工作流引擎：状态驱动执行链路

根据用户输入意图，精确匹配以下链路，不得跳步。执行前必须告知用户当前走哪条链路。

4.1 🔨 From Scratch — 从零开始（大型版本）

触发条件：从头开发 / 核心架构搭建 / 大规模重构

执行链路 (Superpowers)：
brainstorming ➔ writing-plans ➔ subagent-driven-development ➔ verification-before-completion ➔ First Commit

Git 动作：
在 main 或 develop 主工作区完成核心骨架开发（无需 Worktree）。
骨架跑通后，拉出 release/v1.0.0 分支。
验证通过后，合并入 main 并打上 Tag v1.0.0。

4.2 🧩 Major Module — 新功能 / 大模块（中型版本）

触发条件：用户声明需要开发新功能或非核心独立模块

执行链路 (Superpowers)：
using-git-worktrees ➔ brainstorming ➔ writing-plans ➔ subagent-driven-development ➔ finishing-a-development-branch

Git 动作（严格绑定 Worktree）：
强制隔离：从 develop 拉出 feature/xxx 分支，并必须为其创建独立的 Git Worktree 物理目录。
在 Worktree 中完成开发与测试。
规范化收尾：调用 finishing-a-development-branch 时，合并目标必须且只能是 develop（通过 PR），严禁直接合并到 main。
合并后自动清理废弃的 Worktree。

4.3 🐛 Bug Report — 错误修复（小型版本）

触发条件：用户指出系统报错、崩溃或行为不符合预期

执行链路 (Superpowers)：
systematic-debugging ➔ 修复代码 ➔ verification-before-completion ➔ Commit Fix

Git 动作：
从 main 拉出 hotfix/fix-xxx 分支（可选使用 Worktree，若修复简单可直接在主工作区切分支）。
修复并验证后，Squash Merge 回 main 和 develop。
打补丁 Tag（如 v1.0.1）。

4.4 🔧 Minor Tweak — 局部调整（小型版本）

触发条件：用户对当前开发不满意，仅需局部调整或小规模调试

执行链路 (Superpowers)：
tdd ➔ verification-before-completion ➔ Commit Refactor/Fix

Git 动作：
在当前安全分支（如 develop 或现有的 feature/xxx Worktree）直接进行（无需新建 Worktree）。
验证后，使用 Squash Merge 将零碎提交压缩为一个干净 Commit。

Superpowers 技能上下文适配规则 (防冲突补丁)

为防止 Superpowers 技能的默认行为与本项目的 Git 规范冲突，AI 在调用以下技能时必须遵守：

5.1 using-git-worktrees 适配
强制使用：仅在 4.2 Major Module (中型版本) 时强制使用。
豁免场景：4.1 (大型骨架开发)、4.3 (简单 Hotfix)、4.4 (Minor Tweak) 不强制创建 Worktree，允许在主工作区切换分支操作，以保持轻量。

5.2 finishing-a-development-branch 适配
动态目标：该技能在提示合并选项时，必须读取当前分支类型：
  若当前为 feature/* 分支 ➔ 选项必须为 "Create PR to develop"（屏蔽 Merge to main 选项）。
  若当前为 release/* 分支 ➔ 选项必须为 "Merge to main"。
  若当前为 hotfix/* 分支 ➔ 选项必须为 "Squash Merge to main & develop"。

红线规则（Strict Guardrails）
红线   说明
1   🚫 严禁过早提交   核心依赖未成功运行并验证前，禁止提交包含大量次要功能的杂乱代码

2   🚫 严禁主线污染   禁止在 main 上进行高风险实验，中型功能必须走 worktree + feature 分支

3   🚫 严禁无测试合并   任何合并前必须经过 verification-before-completion 或 finishing-a-development-branch

4   🚫 严禁越级合并   Feature 分支严禁直接合并到 main，必须经过 develop

快速决策树

text
用户输入
  │
  ├─ "从头开发" / "重构整个系统"
  │    → 链路 4.1 (From Scratch) → 大型版本 (无需 Worktree)
  │
  ├─ "加一个新功能" / "开发 XX 模块"
  │    → 链路 4.2 (Major Module) → 中型版本 (强制 Worktree + PR to develop)
  │
  ├─ "报错了" / "崩溃了" / "不对"
  │    → 链路 4.3 (Bug Report) → 小型版本 (Hotfix 分支)
  │
  ├─ "不太满意" / "调一下" / "改改这里"
  │    → 链路 4.4 (Minor Tweak) → 小型版本 (当前分支直接 TDD)
  │
  └─ 无法判断
       → 主动询问用户意图，再做决策

OK，全部翻译为英文