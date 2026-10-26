#!/usr/bin/env bash
# 公共小工具：定位存放 sample-project 的题目目录。
#
# 该作品所在目录在不同提交里可能不同（历史上位于 minimax/，现位于 stepfun/），
# 因此按提交实际内容探测，而不是写死目录名。

# ensure_sddctx_on_path
# 把项目虚拟环境放到 PATH 最前面，否则直接用 bash scripts/xxx.sh 运行会报
# "sddctx: command not found"。
ensure_sddctx_on_path() {
  local venv="${SDDCTX_VENV:-$HOME/minimax-venv}"
  if [ -x "$venv/bin/sddctx" ]; then
    export PATH="$venv/bin:$PATH"
  fi
}

# detect_project_dir <repo_root> <commit>
# 输出：该提交下包含 sample-project 的顶层目录名（探测不到时回退为 stepfun）。
detect_project_dir() {
  local repo_root="$1"
  local commit="$2"
  local dir
  for dir in $(git -C "$repo_root" ls-tree -d --name-only "$commit" 2>/dev/null); do
    if git -C "$repo_root" cat-file -e "$commit:$dir/sample-project" 2>/dev/null; then
      echo "$dir"
      return 0
    fi
  done
  echo "stepfun"
}
