---
name: vidflow
description: "用户要做有剧情的 AI 短片/短剧（多镜头、配音、成片）时用。整条流程只走 vf 命令：立意→故事→剧本→分镜表→设定卡→选角→分镜首帧→录对白→视频片段→对口型→合成→成片检查→文案→飞书交付。"
version: 3.0.0
author: chillwang
metadata:
  hermes:
    tags: [video, short-film, storyboard, grok, gpt-image, mimo, pipeline]
---

# 出短片：只用 vf 这一套流程

**整条流程都用 `vf` 命令做**。不要自己另写生图/生视频/拼接/配音/发飞书的脚本，不要把流程交给子代理，不要跳步。每一步跑完会自动检查，不过关会停下并写清问题。全部命令说明：`vf`（不带参数）。

## 先读再动手
项目按故事分文件夹：`~/production/<故事>/<集或版本>/`（比如 `~/production/如龙/第2集_重逢/`）。
- 接手一个故事，先读 `~/production/<故事>/故事说明.md`（讲什么、分几集、各集做到哪；程序自动生成，手写规划在同目录 `故事备忘.md`）。
- 进到某一集，先读这一集的 `进度说明.md`（每一步状态、下一步、规矩；程序自动生成，不要手改），要记的决定写进同目录 `notes.md`。
- 新开一集：从 `~/production/<故事>/共用设定/` 复制角色卡、原声、照片、场景卡、道具卡到这一集的 `cast/` 和 `refs/`。项目名写成 `<故事>/<集>`，比如 `vf status 如龙/第2集_重逢`。

## 怎么跑
1. 建项目：先写 brief.json，再 `vf new <故事>/<集> --brief brief.json`
   ```json
   {"title":"片名","requirement":"用户原话需求","story_core":"故事内核","source":"原作依据（改编才写）","episode":"本集只讲哪件事（系列才写）",
    "duration":60,"refs":{"角色名":["/home/chillwang/.hermes/cache/images/用户发的原图.jpg"]},
    "voices":{"角色名":"preset:苏打 或 参考音文件路径"},"watermark":"/home/chillwang/assets/watermarks/overlay_c_1080p.png"}
   ```
2. `vf bg <项目> auto`：后台自动往下跑，遇到要用户过目的、没过检查的就停。生成视频一跑十几分钟，**不要在前台等**。
3. `vf status <项目>`：看跑到哪、要给用户看什么、下一步做什么。`vf log <项目>` 看日志。

## 每一样都先发用户过，他点头才往下推
用户是业务方：立意、剧本、台词、镜头由你判断定稿，但下面每一样都要发给用户看，**摆出来讲清意图**（想让观众感到什么、为什么这样拍），不要问「对不对」。

| 关卡 | 发给用户的东西 | 用户点头后 |
|---|---|---|
| 立意 intent | `story/intent.md`：一句话，想让观众看完感到什么、为什么值得看 | `vf approve <项目> intent --pick 编号` |
| 故事 story | `story/options.md`（3 个方案） | `vf approve <项目> story --pick 编号` |
| 剧本 screenplay | 讲清剧本意图（三段讲什么、为什么这样讲），不贴全文 | `vf approve <项目> screenplay` |
| 设定卡 cast | 新的角色卡、场景卡、道具卡（沿用共用设定的不用再发） | `vf approve <项目> cast` |
| 音色 voices | `cast/voice_*_试音.mp3` | `vf approve <项目> voices` |
| 分镜首帧 boards | `boards/storyboard.jpg`，按剧情顺序连起来看 | `vf approve <项目> boards` |
| 视频+配音 | 合成好的粗剪（先跑 vf review 核对过） | 按意见改 |
| 成片 qc | `final/<项目>.mp4`（qc 通过的那份） | `vf approve <项目> qc` |
| 文案 | `vf copy` 写的账号简介/剧集介绍/本集文案，你挑定后发 | `vf copy <项目> --pick 本集=编号` |

立意要先定：没定立意，故事、剧本、文案都写不了（程序会拦）。立意可以你自己写好登记：`vf intent <项目> --set "立意"`，同样要发用户过。

## 交付
- **交付任何视频前必须先跑 `vf review <项目> [--shots 改过的镜头]`**，逐张看核对图：标「声」的帧嘴必须在动，嘴在动的帧必须标「声」，动作合乎常识。改过的镜头要把脸放大逐帧核对音画同步。没看完不发。
- 发飞书只用 `vf deliver <项目>`（成片+定稿文案一起发）或 `vf deliver <项目> --file 文件 --note "附言"`：≤30MB 直接私信；更大的走云空间链接；云空间不通会自动压一版手机版发，并说明原因。

## 某一步没过检查时
`vf status <项目>` 会列出具体问题，按问题改，不要绕过：
- 分镜/片段：改那一镜的 `image_prompt` / `video_prompt`（只写看得见的动作和景别，不写长相、衣服鞋子），再 `--shots` 重做那一镜（`vf boards|clips <项目> --shots s03,s05`）。
- 只是检查规则改了、片段本身没问题：`vf clips <项目> --shots sXX --recheck` 只重查。
- **对口型对不上**：先 `vf refit <项目> --shots sXX`（不动画面，把台词重录到画面里嘴动的长度，每句最多 10 遍），再 `vf bg <项目> auto`；refit 说「重录也对不上」才重做那一镜 `vf clips <项目> --shots sXX`。
- 台词改过：改 `script/script.json` 里那句后重录；画面已经生成的，先 refit 到画面长度，不行再重做画面。
- 分镜表里可以手动指定：`dialogue.speak_at`（视频里角色几秒后开口，比如先放下枪再说话）、`dialogue.place_at`（导演看过画面后指定台词从第几秒放）。
- 只重录某几个角色的试音：`vf voices <项目> --only 角色1,角色2`。

## 规矩
1. **检查标准只有 Claude Code 能改**。你卡住了（反复不过、觉得检查误判）先报用户，不许自己放宽标准、不许改 vidflow 代码。
2. 检查器明显误判、你看过确实可用时，人工放行要写理由：`vf approve <项目> <步骤> --override "理由"`，理由会记进进度说明。
3. 发给用户的必须是检查通过的那份文件，原样发出。
4. 配音只用用户认可过的音色；找不到合适的原声就直接说，不拿别的声音冒充。
5. 只动有问题的镜头，改完和上一版对比，不越改越糟。
