# vidflow

AI 竖屏短片出片管线。命令是 `vf`。有剧情、多镜头、要配音成片的，整条只走这套，不另写生图/生视频/拼接脚本。

版本 3.0。Agent 说明书在 [`hermes_skill/SKILL.md`](hermes_skill/SKILL.md)。

这不是开箱即用的 SaaS。clone 之后必须自备三个接口的密钥：出图、视频、配音。没有 key 出不了片。飞书交付是可选的。

## 它干什么

按关卡推进，每步跑完自动检查，不过关就停。标了要确认的步骤，必须人点头才能往下：

立意 → 故事 → 剧本 → 分镜表 → 设定卡 → 试音 → 分镜首帧 → **先录对白再拍** → 视频片段 → 对口型 → 合成 → 成片检查 → 文案 → 飞书交付

成片规格：9:16，720×1280。视频模型默认 `grok-imagine-video-1.5`，出图默认 `gpt-image-2.5`。

## 怎么跑

本机需要：Python 3.11+、ffmpeg、一份 OpenAI 兼容的文本/出图/视频网关、MiMo 配音密钥。

```bash
git clone https://github.com/Cchhiillll/vidflow.git
cd vidflow
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
chmod +x vf
cp env.example ~/.config/vidflow/.env   # 填密钥
# 可选：cp image_providers.example.json ~/.config/vidflow/image_providers.json
./vf new 故事/第1集 --brief brief.example.json
./vf bg 故事/第1集 auto
./vf status 故事/第1集
```

日常三条：

```bash
vf bg <项目> auto          # 后台往下跑，遇到确认或检查失败就停
vf status <项目>           # 跑到哪、下一步、要给人看的文件
vf approve <项目> <步骤>   # 人确认后放行；story / intent 要带 --pick 编号
```

全部命令：终端里直接跑 `vf`（不带参数）。

项目目录默认 `~/production/<故事>/<集>/`，可用 `VIDFLOW_PROD` 改。

## 仓库里是什么

- `vf` / `vf.py`：入口（建项目、后台、状态、放行、按序调度）
- `vf_story.py`：立意 / 故事 / 剧本
- `vf_pre.py`：分镜表 / 设定卡 / 分镜首帧
- `vf_post.py`：配音、片段、对口型、合成、质检
- `vf_voice.py`：MiMo 配音
- `vf_copy.py`：发布文案
- `vf_deliver.py`：飞书交付（没配就跳过）
- `vf_common.py`：项目状态、模型调用、出图 fallback
- `brief.example.json` / `env.example` / `image_providers.example.json`：样例，不含密钥

密钥不进仓库。优先读环境变量 `VIDFLOW_API_KEY` / `MIMO_API_KEY`，本机 Hermes 仍可回落到 `~/.hermes/.env`。

质检标准不许 Agent 私自放宽。卡住了报人，不要改 `vf_pre.py` / `vf_post.py` 里的检查混过去。
