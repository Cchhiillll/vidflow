# vidflow

ThinkPad 上跑的 AI 竖屏短片出片管线。命令是 `vf`。有剧情、多镜头、要配音成片的，整条只走这套，不另写生图/生视频/拼接脚本。

版本 3.0。Agent 说明书在 [`hermes_skill/SKILL.md`](hermes_skill/SKILL.md)。

## 它干什么

按关卡推进，每步跑完自动检查，不过关就停。标了要确认的步骤，必须人点头才能往下：

立意 → 故事 → 剧本 → 分镜表 → 设定卡 → 试音 → 分镜首帧 → **先录对白再拍** → 视频片段 → 对口型 → 合成 → 成片检查 → 文案 → 飞书交付

成片规格：9:16，720×1280。视频模型默认 `grok-imagine-video-1.5`，出图默认 `gpt-image-2.5`（额度用完按 `~/.config/vidflow/image_providers.json` 降级）。

## 日常三条

```bash
vf bg <项目> auto          # 后台往下跑，遇到确认或检查失败就停
vf status <项目>           # 跑到哪、下一步、要给人看的文件
vf approve <项目> <步骤>   # 人确认后放行；story / intent 要带 --pick 编号
```

全部命令：终端里直接跑 `vf`（不带参数）。

项目目录默认 `~/production/<故事>/<集>/`，可用环境变量 `VIDFLOW_PROD` 改。

## 仓库里是什么

| 文件 | 干什么 |
|---|---|
| `vf.py` | 入口：建项目、后台、状态、放行、按序调度 |
| `vf_story.py` | 立意 / 故事 / 剧本 |
| `vf_pre.py` | 分镜表 / 设定卡 / 分镜首帧 |
| `vf_post.py` | 配音、片段、对口型、合成、质检 |
| `vf_voice.py` | MiMo 配音（固定音色 / 克隆 / 设计） |
| `vf_copy.py` | 发布文案 |
| `vf_deliver.py` | 飞书交付 |
| `vf_common.py` | 项目状态、模型调用、出图 fallback |
| `brief_*.json` | 本机项目的 brief 样例，路径是本机的，别当通用模板抄 |

密钥不进仓库。CPA 密钥读 `~/.hermes/.env` 的 `MODELVERSEAPI_API_KEY`；非 CPA 出图密钥在 `~/.config/vidflow/image_api.key`。

## 依赖环境

这不是开箱即用的 SaaS。当前绑定了本机 Hermes / CPA（文本+出图）/ Grok Imagine（视频）/ 小米 MiMo（配音）/ 飞书交付，以及本机代理 `http://127.0.0.1:10819`。没有这些，命令能装上，出片链跑不通。

质检标准不许 Agent 私自放宽。卡住了报人，不要改 `vf_pre.py` / `vf_post.py` 里的检查混过去。
