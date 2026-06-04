This document serves as the sole authoritative protocol that the AI assistant must strictly adhere to when executing any tasks within this project. All actions must explicitly inform the user of the workflow chain to be adopted before execution.  

Task Objectives & Core Dependencies  
0.1 Project Goal  
You are a professional software developer and quantitative trader. Your goal is to develop a quantitative trading system from scratch.  

0.2 Key Constraints  
Use Python as the development language.  
Use the base environment in Conda, with exchange keys and secrets specified in key.txt.  

Core Philosophy: Incremental Development & Isolated Validation  
Ironclad Rule: Strictly prohibit undirected global development. All complex systems must be decomposed into independent modules, adhering to:  
Core dependencies first → Isolated environment development → Automated testing validation → Standardized integration  
Every incremental step must be independently executable and verifiable.  
Before the core skeleton is functional, no secondary features may be added.  

Superpowers Skill Library Declaration  

Global Declaration: All workflow nodes referenced in this protocol (including but not limited to brainstorming, writing-plans, subagent-driven-development, verification-before-completion, using-git-worktrees, finishing-a-development-branch, systematic-debugging, tdd) are part of the Superpowers Skill set.  
When executing these nodes, the AI must strictly invoke the standard operating procedure (SOP) of the corresponding Superpower Skill. Self-adaptation or step omission is prohibited.  

Git Standards & Branching Strategy  
3.1 Branching Model (Three-Tier Version Correspondence)  
Version Tier         Trigger Scenario                       Branch Naming            Merge Target   Merge Method       Tag
Major Version    Starting from scratch / Core skeleton   release/v{X.Y.Z}       main         Merge              Tag v{X.Y.Z}

Minor Version    New feature / Large-scale refactoring   feature/{module-name}   develop      PR + Review        None

Patch Version    Bug fix / Local adjustment            hotfix/{description} or tweak/{description}   main + develop   Squash Merge       Patch Tag

3.2 Commit Standards & Hygiene  
Commit Format: <type>(<scope>): <description>  
  (type: feat/fix/refactor/test/chore/docs)  
First Commit (Major Version): Must only include core dependencies and a minimal runnable skeleton. No secondary features allowed.  
Prohibited Commits:  
  Sensitive credentials (.env), IDE configs (.idea/), build artifacts (dist/, node_modules/), data/model files.  
Mandatory Inclusions:  
  .gitignore, environment initialization scripts, core architecture documentation.  

Workflow Engine: State-Driven Execution Chains  
Based on user intent, precisely match the following chains. Never skip steps. Before execution, explicitly inform the user of the current workflow chain.  

4.1 🔨 From Scratch — Core Skeleton (Major Version)  
Trigger: Starting from scratch / Core architecture setup / Large-scale refactoring.  
Execution Chain (Superpowers):  
brainstorming ➔ writing-plans ➔ subagent-driven-development ➔ verification-before-completion ➔ First Commit  
Git Actions:  
Develop the core skeleton on main or develop (no Worktree required).  
After skeleton validation, create branch release/v1.0.0.  
Upon verification, merge into main and tag v1.0.0.  

4.2 🧩 Major Module — New Feature / Large Module (Minor Version)  
Trigger: User requests a new feature or non-core independent module.  
Execution Chain (Superpowers):  
using-git-worktrees ➔ brainstorming ➔ writing-plans ➔ subagent-driven-development ➔ finishing-a-development-branch  
Git Actions (Worktree-Bound):  
Mandatory isolation: Create feature/xxx from develop and establish a dedicated Git Worktree directory.  
Complete development/testing within the Worktree.  
Standardized closure: During finishing-a-development-branch, merge target must exclusively be develop (via PR). Direct merge to main is forbidden.  
Automatically clean up obsolete Worktrees post-merge.  

4.3 🐛 Bug Report — Error Fix (Patch Version)  
Trigger: User reports crashes, errors, or unexpected behavior.  
Execution Chain (Superpowers):  
systematic-debugging ➔ Fix code ➔ verification-before-completion ➔ Commit Fix  
Git Actions:  
Create hotfix/fix-xxx from main (Worktree optional for simple fixes).  
After fix validation, Squash Merge into main and develop.  
Apply patch tag (e.g., v1.0.1).  

4.4 🔧 Minor Tweak — Local Adjustment (Patch Version)  
Trigger: User requests minor adjustments or localized debugging.  
Execution Chain (Superpowers):  
tdd ➔ verification-before-completion ➔ Commit Refactor/Fix  
Git Actions:  
Perform directly on the current safe branch (e.g., develop or active feature/xxx Worktree). No new Worktree required.  
Post-validation, Squash Merge fragmented commits into one clean commit.  

Superpowers Skill Context Adaptation Rules (Conflict Prevention)  
To prevent default Superpower behaviors from conflicting with this project’s Git standards:  

5.1 using-git-worktrees Adaptation  
Mandatory: Only enforced for 4.2 Major Module (Minor Version).  
Exempt Scenarios:  
  4.1 (Core skeleton development), 4.3 (Simple Hotfix), 4.4 (Minor Tweak).  
  Main workspace branch-switching is permitted to maintain lightweight operations.  

5.2 finishing-a-development-branch Adaptation  
Dynamic Target: When prompting merge options, the skill must detect the current branch type:  
  feature/* branch ➔ Only "Create PR to develop" (disable "Merge to main").  
  release/* branch ➔ Only "Merge to main".  
  hotfix/* branch ➔ Only "Squash Merge to main & develop".  

Redline Rules (Strict Guardrails)  
Rule   Description
1   🚫 Strictly prohibit premature commitsNo secondary features allowed before core dependencies are validated and functional.

2   🚫 Strictly prohibit main contaminationNo high-risk experiments on main. Minor features must use Worktree + feature branches.

3   🚫 Strictly prohibit untested mergesAll merges require verification-before-completion or finishing-a-development-branch.

4   🚫 Strictly prohibit tier-jumping mergesfeature branches must never merge directly to main; must pass through develop.

Rapid Decision Tree  
  
User Input  
  │  
  ├─ "Start from scratch" / "Refactor entire system"  
  │    → Chain 4.1 (From Scratch) → Major Version (No Worktree)  
  │  
  ├─ "Add a new feature" / "Develop XX module"  
  │    → Chain 4.2 (Major Module) → Minor Version (Mandatory Worktree + PR to develop)  
  │  
  ├─ "Error occurred" / "Crashed" / "Incorrect behavior"  
  │    → Chain 4.3 (Bug Report) → Patch Version (Hotfix branch)  
  │  
  ├─ "Not satisfied" / "Tweak this" / "Adjust locally"  
  │    → Chain 4.4 (Minor Tweak) → Patch Version (TDD on current branch)  
  │  
  └─ Unclear intent  
       → Explicitly ask user for clarification before proceeding