"""前期·故事开发（行业流程：故事梗概/节拍表 → 剧本 → 分镜表）。写作用 CPA 上写作最强的模型（默认 gpt-6-astra）。
  vf story <项目> [--notes 意见]          出 3 个故事方案（梗概 + 节拍表 + 结尾落点），等用户选
  vf approve <项目> story --pick 2        选定方案
  vf screenplay <项目> [--notes 意见]     按选定方案写剧本（场景、动作、台词、潜台词、表演指导），等用户确认
  vf approve <项目> screenplay            确认后 vf script 按剧本拆分镜表
"""
import json, os
from vf_common import *

WRITER = os.environ.get("VIDFLOW_WRITER_MODEL", "gpt-6-astra")

STORY = """你是获奖短片编剧。为下面的需求开发 3 个不同的故事方案，用于一部约 {dur} 秒、竖屏、真人质感的 AI 短片（角色可以是拟人化动物，按参考图）。

需求：{req}
必须保留的故事内核（用户已定）：{core}
参考图里的主角与场景：{ref}
用户意见：{notes}

行业标准（每个方案都要做到）：
- 一句话故事（logline）要有人物、欲望、阻碍、反转。
- 主题：这部片子到底想让观众感到什么，一句话。
- 节拍表 6~8 个节拍，每拍标秒数：开场钩子（3 秒内抓人）→ 建立人物和欲望 → 阻碍/升级 → 转折 → 高潮（反转揭晓）→ 落点。
- 结尾必须有落点：用一个具体的动作或画面收住主题，留余味；不能是「吃完了/看着远方」这种空镜。
- 埋伏笔与回收：前面出现的道具或细节，结尾要回收产生新的意义。
- 用画面讲故事，台词全片 ≤4 句，每句都要推动故事或揭示人物，有潜台词，不解释情绪。
- 视频模型能拍：场景尽量少（≤3 个）、动作简单、避免复杂的手部交互、激烈打斗和多人肢体接触；暴力只用暗示（枪声在画外、倒下的剪影），不拍血腥；道具不会自己动。
- 不要陈词滥调（不要子弹、通缉令、血、抽烟）。

输出 JSON：{{"options":[{{"title":"片名","logline":"一句话故事","theme":"主题","why":"这个方案好在哪、观众最后会记住什么",
"beats":[{{"sec":"0-4","beat":"开场钩子","what":"画面上发生什么（具体）","line":"台词（没有写空）"}}],
"ending":"结尾落点的具体画面和它的意义","plant_payoff":"埋了什么、怎么回收"}}]}}"""

SCREENPLAY = """你是电影编剧。把选定的故事方案写成可以直接拍的短片剧本（中文）。

选定方案：{option}
目标时长约 {dur} 秒；参考图里的主角与场景：{ref}
用户意见：{notes}

要求：
- 标准剧本格式：场景标题（内/外·地点·时间），动作描写（只写看得见、拍得出的东西，现在时），角色名 + 台词，台词下用括号写表演指导（情绪、语气、轻重、停顿）。
- 台词少而准，全片 ≤4 句；每句写出它的潜台词是什么（给演员看，不念出来）。
- 节奏：标出每段大约几秒；不拖沓，每个画面都有存在的理由。
- 结尾落点要具体有力；伏笔要回收。
- 道具不会自己动：要动就写清楚是谁的手；同一场景里角色的姿势、位置前后一致。

输出 JSON：{{"title":"片名","screenplay":"完整剧本正文（换行用\\n）","dialogue":[{{"speaker":"角色","text":"台词","direction":"表演指导","subtext":"潜台词"}}],
"voices":{{"角色":"音色描述：性别、年龄感、音高、质感、语速"}}}}"""


def _ref(pj):
    p = pj.p("script/ref_desc.json")
    if os.path.exists(p):
        return pj.load("script/ref_desc.json")["desc"]
    brief = pj.load("brief.json")
    refs = [r for v in brief.get("refs", {}).values() for r in (v if isinstance(v, list) else [v])]
    d = vision("用中文详细描述这张图：主体是谁、长相、穿着、姿态、场景、光线。只描述看得见的。", refs[:1], json_out=False) if refs else "无"
    pj.save("script/ref_desc.json", {"desc": d}); return d


def cmd_story(pj, args):
    brief = pj.load("brief.json")
    r = chat([{"role": "user", "content": STORY.format(dur=brief.get("duration", 55), req=brief.get("requirement", ""), core=brief.get("story_core") or brief.get("story", ""),
                                                       ref=_ref(pj), notes=args.get("notes") or "无")}], model=WRITER, json_out=True, stream=True)
    opts = r.get("options", [])
    pj.save("story/options.json", opts)
    L = []
    for i, o in enumerate(opts, 1):
        L += [f"## 方案 {i}：{o.get('title')}", f"**一句话**：{o.get('logline')}", f"**主题**：{o.get('theme')}", f"**好在哪**：{o.get('why')}", "", "| 秒 | 节拍 | 画面 | 台词 |", "|---|---|---|---|"]
        L += [f"| {b.get('sec')} | {b.get('beat')} | {b.get('what')} | {b.get('line') or '—'} |" for b in o.get("beats", [])]
        L += ["", f"**结尾落点**：{o.get('ending')}", f"**伏笔与回收**：{o.get('plant_payoff')}", ""]
    open(pj.p("story/options.md"), "w", encoding="utf-8").write("\n".join(L))
    pj.set("story", "awaiting_approval", {"n": len(opts)})
    print(f"✅ {len(opts)} 个故事方案，请用户选（vf approve <项目> story --pick 编号）：{pj.p('story/options.md')}")


def cmd_screenplay(pj, args):
    pj.require("story", "approved")
    brief = pj.load("brief.json")
    opts = pj.load("story/options.json"); pick = int(pj.state()["story"]["report"].get("pick", 1))
    o = opts[pick - 1]
    r = chat([{"role": "user", "content": SCREENPLAY.format(option=json.dumps(o, ensure_ascii=False), dur=brief.get("duration", 55), ref=_ref(pj),
                                                            notes=args.get("notes") or "无")}], model=WRITER, json_out=True, stream=True)
    pj.save("story/screenplay.json", r)
    L = [f"# {r.get('title')}", "", r.get("screenplay", ""), "", "## 台词表", "| 角色 | 台词 | 表演指导 | 潜台词 |", "|---|---|---|---|"]
    L += [f"| {d.get('speaker')} | {d.get('text')} | {d.get('direction')} | {d.get('subtext')} |" for d in r.get("dialogue", [])]
    L += ["", "## 音色", *[f"- {k}：{v}" for k, v in (r.get("voices") or {}).items()]]
    open(pj.p("story/screenplay.md"), "w", encoding="utf-8").write("\n".join(L))
    pj.set("screenplay", "awaiting_approval", {"pick": pick})
    print(f"✅ 剧本写好了，请用户确认（vf approve <项目> screenplay）：{pj.p('story/screenplay.md')}")
