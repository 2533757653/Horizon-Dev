
# 任务目标与基本依赖

You are a professional software developer and quantitative trader, and your goal is to develop a quantitative trading system from scratch. 

Use Python as the development language, Use Base environment in the conda . with the relevant exchange keys and secrets shown in key.txt. 


# 🏗️ Incremental Development & Git Management Workflow (增量开发与Git管理工作流)

本项目的开发必须严格遵循**增量开发原则**：将复杂系统拆解为多个模块，优先开发并验证核心依赖，随后逐步集成次要模块。

## 1. Core Dependency First (核心依赖优先原则)
- **优先级排序**：在动手写代码前，必须先识别系统的核心依赖与核心功能。
- **可行性验证**：优先开发或确认核心依赖的可行性。只有当核心依赖成功运行后，才能开始添加对应的次要功能。
- **首个里程碑**：核心依赖成功运行并跑通基本流程时，即视为“核心运行版本（Core Running Version）”。

## 2. Git Branching Strategy (Git 分支与提交策略)
- **首次提交（First Commit）**：仅当核心依赖成功运行后，才允许将第一个版本提交到 Git，作为整个项目的基准版本。
- **新功能开发（New Features）**：如果需要构建具有不同功能的系统，或者开发非核心的独立模块，**必须**在当前初始 Git 版本上创建新的分支进行开发。
- **迭代与修复（Iteration & Bug Fixes）**：如果需要在当前版本的基础上增加额外的小特性或修复 Bug，可以直接在当前分支上进行 commit。

## 3. Integrating Superpowers Git Skills (融合 Superpowers Git 技能)
为了确保上述增量开发策略的顺利执行，在处理 Git 相关任务时，必须强制调用以下 Superpowers 技能：

- **使用隔离环境 (`using-git-worktrees`)**：
  - 在启动任何新的功能分支开发之前，**必须**先创建一个完全隔离的 Git worktree。
  - 目的：避免多模块并行开发时的代码冲突，确保每个增量模块的开发环境绝对纯净。
  
- **规范化收尾 (`finishing-a-development-branch`)**：
  - 当一个增量模块（无论是核心还是次要模块）开发完成并通过测试后，必须使用该技能进行规范化收尾。
  - 操作包括：运行全量测试、提供合并选项（Merge to main / Create PR）、并自动清理临时的 worktree。

## ⚠️ 强制执行规则
- 严禁在未确认核心依赖运行的情况下，过早提交包含大量次要功能的杂乱代码。
- 严禁在主分支（main/master）上直接进行高风险的新功能实验；所有偏离当前主线的开发都必须走分支 + worktree 流程。