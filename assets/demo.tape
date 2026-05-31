# vhs tape — 录制 transmutary-demo 离线 demo 生成 README hero gif。
# 生成: vhs assets/demo.tape  (产出 assets/demo.gif)
# 之后 ffmpeg palette 压缩见 README/提交说明。

Output assets/demo.gif

Set Shell "zsh"
Set FontSize 13
Set Width 1180
Set Height 1340
Set Padding 18
Set Theme "Dracula"

# 隐藏环境准备（venv 激活 + 清屏），gif 只展示干净的 demo 运行。
Hide
Type "cd /Users/seasontemple/workspace/transmutary && source .venv/bin/activate && clear"
Enter
Sleep 1s
Show

# 干净展示：一条命令，零凭据零网络跑完整管线。
Type "transmutary-demo"
Sleep 800ms
Enter
Sleep 7s
