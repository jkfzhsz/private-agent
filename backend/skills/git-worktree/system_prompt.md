# Git Worktree 隔离开发 —  移植版

移植自 obra/superpowers。使用 Git Worktree 在独立分支中隔离开发。

## 何时使用

- 任何新功能开发
- 多任务并行时
- 需要保持主分支干净时

## 流程

### 1. 创建隔离环境
```bash
# 先拉取最新代码
git fetch origin

# 创建 worktree（分支名可用功能名）
git worktree add -b feature/<feature-name> ../<repo>-<feature-name> main
```

### 2. 在新 worktree 中开发
- 所有文件操作在隔离目录进行
- 切换 worktree：cd ../<repo>-<feature-name>
- 运行项目setup确认环境：npm install && npm test（基线测试全过）

### 3. 开发完成后
- 提交所有变更
- 切回主 worktree
- 合并或提交 PR

### 4. 清理
```bash
git worktree remove ../<repo>-<feature-name>
git branch -d feature/<feature-name>
```

## 检查清单
- [ ] Worktree 创建成功，基线测试通过
- [ ] 所有开发在独立 worktree 中进行
- [ ] 主分支保持干净（不变更直接提交到 main）
- [ ] 开发完成后清理 worktree