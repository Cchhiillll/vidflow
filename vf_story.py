"""前期·故事开发（行业流程：立意 → 故事梗概/节拍表 → 剧本 → 分镜表）。写作用 CPA 上写作最强的模型（默认 gpt-6-astra）。
  vf intent <项目> [--notes 意见]         ★ 先定立意：3 个候选（一句话：想让观众感到什么、为什么值得看），等用户选
  vf intent <项目> --set "立意"           导演自己写好的立意直接登记（仍要发用户过）
  vf approve <项目> intent --pick 2       选定立意（写进 brief.json 的 intent，后面写故事、剧本、文案、审读都按它）
  vf story <项目> [--notes 意见]          出 3 个故事方案（梗概 + 节拍表 + 结尾落点），等用户选
  vf approve <项目> story --pick 2        选定方案
  vf screenplay <项目> [--notes 意见]     按选定方案写剧本（场景、动作、台词、潜台词、表演指导），等用户确认
  vf approve <项目> screenplay            确认后 vf script 按剧本拆分镜表
"""
import json, os
from vf_common import *

WRITER = os.environ.get("VIDFLOW_WRITER_MODEL", "gpt-6-astra")

# 10-18 教训：旧规则「台词全片 ≤4 句」「用画面讲故事」「道具伏笔要回收」把《如龙》第一版写成了 100 秒 4 句台词、
# 一张通知单从头拿到尾代替人物说话。下面是重写后的规则，写故事、写剧本、拆分镜三处共用。
RULES = """写作铁律（每条都要做到）：
1. 讲清楚：一个没看过原作的观众看完，能说出谁、想要什么、做了什么、结果怎样、为什么。
2. 改编要忠于原作：情节、人物关系、动机以「原作依据」为准；原作没写的细节可以合理补，但不能和原作冲突、不能改大方向。
3. 顺叙为主；倒叙必须有明确标识（字幕或转场），而且非用不可。
4. 人物靠说话沟通：要紧的事直接说出口（比如「我妹妹明天手术」），不用纸条、文件、手机屏幕、道具代替说话。台词口语、短句、有来有回，像真人求助、争吵、安慰那样说。
5. 台词撑得起时长：有对话的戏平均每 6~10 秒至少一句台词（一分钟约 6~10 句）；台词承载冲突、关系和情绪的变化；两句台词不能说同一件事；不用台词复述画面。
6. 每场戏、每个镜头都要推进故事或人物关系，说不出作用就删；不拍和主线无关的走路、看东西、空镜。
7. 道具只在剧情需要时出现，用完就不再反复出镜；不为了「伏笔」硬塞道具。
8. 系列集：本集只讲一件事（见「本集任务」）；有前情就用开头一两个镜头交代；结尾停在悬念或情绪落点上。
9. 视频模型拍得出：场景 ≤3 个；暴力用暗示（枪声、倒下），不拍血腥；避免复杂手部交互和多人缠斗。
10. 镜头服务内容：一件简单的事用简单的拍法讲清楚，一场戏用少数几个机位（一个交代全景/双人镜头 + 说话人近景来回切），不切碎；特写只在观众非看清不可、而且推动剧情时才用，整片不超过两三个；不拍没有意义的手部、道具特写（手拿枪、手推门、看单子）。
11. 同一场戏里人物和道具的状态前后一致（谁拿着枪就一直拿着、谁站在哪就一直在哪），不来回变。
12. 动作符合物理和生活常识：人看纸看正面、开门要先转把手、枪响后人不会原地不动……每个动作都要像真实世界会发生的。"""

# 10-18 教训：《如龙》第一版剧本、抖音文案都因为动笔前没有立意被用户否掉——写出来的东西没有中心，每句都在交代信息。
INTENT = """你是资深电影监制。下面这部短片动笔写故事之前，先定「立意」：一句话说清想让观众看完感到什么、为什么这件事值得看。
立意不是剧情梗概，不是主题词（「友情」「牺牲」），而是一个有态度、能让人心里一动的判断，比如「有人替你扛下十年，你就欠下了一辈子」。
后面写故事、剧本、文案时，每一场戏、每一句台词都要为它服务；审读时按它检查。

需求：{req}
故事内核：{core}
原作依据（改编作品必须忠于它；没有写「无」）：{source}
本集任务（系列短片时本集只讲这一件事；没有写「无」）：{episode}
修改意见：{notes}

给 3 个不同方向的立意。只输出 JSON：{{"options":[{{"intent":"立意（一句话，30 字以内）","feel":"观众看完心里留下的感觉（具体）","why":"为什么值得看、和原作/本集任务怎么对上","scene":"最能落实这个立意的一场戏或一个画面"}}]}}"""

STORY = """你是获奖短片编剧。为下面的需求开发 3 个不同的故事方案，用于一部约 {dur} 秒、竖屏、真人质感的 AI 短片（角色可以是拟人化动物，按参考图）。

需求：{req}
故事内核：{core}
立意（每个方案都必须为它服务，结尾要让观众感到它）：{intent}
原作依据（改编作品必须忠于它；没有写「无」）：{source}
本集任务（系列短片时本集只讲这一件事；没有写「无」）：{episode}
参考图里的主角与场景：{ref}
修改意见：{notes}

""" + RULES + """

每个方案还要有：一句话故事（人物、欲望、阻碍、转折）；主题（让观众感到什么）；节拍表 6~10 个节拍，每拍标秒数和这一拍的台词。

输出 JSON：{{"options":[{{"title":"片名","logline":"一句话故事","theme":"主题","why":"这个方案好在哪、观众最后会记住什么",
"beats":[{{"sec":"0-4","beat":"节拍名","what":"画面上发生什么（具体）","line":"这一拍的台词（可以多句，用 / 分隔；没有写空）"}}],
"ending":"结尾落点的具体画面和它的意义"}}]}}"""

SCREENPLAY = """你是电影编剧。把选定的故事方案写成可以直接拍的短片剧本（中文）。

选定方案：{option}
目标时长约 {dur} 秒；参考图里的主角与场景：{ref}
立意（每场戏、每句台词都为它服务；和它无关的删掉）：{intent}
原作依据：{source}
本集任务：{episode}
修改意见：{notes}

""" + RULES + """

格式：场景标题（内/外·地点·时间），动作描写（只写看得见、拍得出的东西，现在时），角色名 + 台词，台词下用括号写表演指导（情绪、语气、轻重、停顿）；标出每段大约几秒。
道具不会自己动：要动就写清楚是谁的手；同一场景里角色的姿势、位置前后一致。

输出 JSON：{{"title":"片名","screenplay":"完整剧本正文（换行用\\n）","dialogue":[{{"speaker":"角色","text":"台词","direction":"表演指导","subtext":"潜台词"}}],
"voices":{{"角色":"音色描述：性别、年龄感、音高、质感、语速"}}}}"""

REVIEW = """你是挑剔的观众兼剧本医生，没看过原作。读下面这份{kind}，先用三句话复述你看到的故事（谁、想要什么、做了什么、结果怎样）。
再对照写作铁律逐条挑问题，只挑真问题：看不懂或交代不清的地方；没有作用的场/镜头；台词总量和时长不匹配（约 {dur} 秒）；
重复的台词、不像真人说话的台词、用道具代替说话；和原作依据冲突；本集任务没完成；结尾没力量；
看完感受不到立意，或有场戏、台词和立意无关。
立意：{intent}
原作依据：{source}
本集任务：{episode}
""" + RULES + """
只输出 JSON：{{"retell":"三句话复述","felt":"你看完感到了什么（一句话，用来和立意对照）","pass":true,"problems":["位置：问题和具体改法"]}}
{kind}：{text}"""


def review_text(kind, text, brief):
    """观众视角审读：复述 + 按写作铁律挑问题。pass 且没有问题才算过。"""
    try:
        r = chat([{"role": "user", "content": REVIEW.format(kind=kind, text=text, dur=brief.get("duration", 60), source=brief.get("source") or "无", intent=brief.get("intent") or "无",
                                                              episode=brief.get("episode") or "无")}], model=WRITER, json_out=True, stream=True)
    except Exception as e:
        return {"retell": "", "pass": False, "problems": [f"审读失败：{e}"]}
    probs = [str(x) for x in (r.get("problems") or [])]
    return {"retell": r.get("retell", ""), "felt": r.get("felt", ""), "pass": bool(r.get("pass")) and not probs, "problems": probs}


def _ref(pj):
    p = pj.p("script/ref_desc.json")
    if os.path.exists(p):
        return pj.load("script/ref_desc.json")["desc"]
    brief = pj.load("brief.json")
    refs = [r for v in brief.get("refs", {}).values() for r in (v if isinstance(v, list) else [v])]
    d = vision("用中文详细描述这张图：主体是谁、长相、穿着、姿态、场景、光线。只描述看得见的。", refs[:1], json_out=False) if refs else "无"
    pj.save("script/ref_desc.json", {"desc": d}); return d


def cmd_intent(pj, args):
    """定立意：导演写好的用 --set 直接登记；否则出 3 个候选。都要发用户过（awaiting_approval）。"""
    brief = pj.load("brief.json")
    if isinstance(args.get("set"), str) and args["set"].strip():
        opts = [{"intent": args["set"].strip(), "feel": args.get("feel") or "", "why": args.get("why") or "导演定", "scene": args.get("scene") or ""}]
    else:
        r = chat([{"role": "user", "content": INTENT.format(req=brief.get("requirement", ""), core=brief.get("story_core") or brief.get("story", ""),
                                                            source=brief.get("source") or "无", episode=brief.get("episode") or "无", notes=args.get("notes") or "无")}],
                 model=WRITER, json_out=True, stream=True)
        opts = r.get("options", [])
    pj.save("story/intent.json", opts)
    L = ["# 立意", "", "动笔前先定：想让观众看完感到什么、为什么值得看。后面的故事、剧本、文案、审读都按选定的这一条。", ""]
    for i, o in enumerate(opts, 1):
        L += [f"## {i}. {o.get('intent')}", f"- 观众看完会感到：{o.get('feel')}", f"- 为什么值得看：{o.get('why')}", f"- 最能落实它的戏：{o.get('scene')}", ""]
    open(pj.p("story/intent.md"), "w", encoding="utf-8").write("\n".join(L))
    pj.set("intent", "awaiting_approval", {"n": len(opts)})
    print(f"✅ 立意 {len(opts)} 条，请用户过（vf approve <项目> intent --pick 编号）：{pj.p('story/intent.md')}")


def need_intent(pj, brief):
    if not (brief.get("intent") or "").strip():
        raise SystemExit("还没定立意：先 vf intent <项目>（或 --set \"立意\"），用户过了 vf approve <项目> intent --pick 编号")


def cmd_story(pj, args):
    brief = pj.load("brief.json")
    need_intent(pj, brief)
    r = chat([{"role": "user", "content": STORY.format(dur=brief.get("duration", 55), req=brief.get("requirement", ""), core=brief.get("story_core") or brief.get("story", ""), intent=brief["intent"],
                                                       source=brief.get("source") or "无", episode=brief.get("episode") or "无", ref=_ref(pj), notes=args.get("notes") or "无")}], model=WRITER, json_out=True, stream=True)
    opts = r.get("options", [])
    pj.save("story/options.json", opts)
    L = []
    for i, o in enumerate(opts, 1):
        L += [f"## 方案 {i}：{o.get('title')}", f"**一句话**：{o.get('logline')}", f"**主题**：{o.get('theme')}", f"**好在哪**：{o.get('why')}", "", "| 秒 | 节拍 | 画面 | 台词 |", "|---|---|---|---|"]
        L += [f"| {b.get('sec')} | {b.get('beat')} | {b.get('what')} | {b.get('line') or '—'} |" for b in o.get("beats", [])]
        L += ["", f"**结尾落点**：{o.get('ending')}", ""]
    open(pj.p("story/options.md"), "w", encoding="utf-8").write("\n".join(L))
    pj.set("story", "awaiting_approval", {"n": len(opts)})
    print(f"✅ {len(opts)} 个故事方案，请用户选（vf approve <项目> story --pick 编号）：{pj.p('story/options.md')}")


def cmd_screenplay(pj, args):
    pj.require("story", "approved")
    brief = pj.load("brief.json")
    need_intent(pj, brief)
    opts = pj.load("story/options.json"); pick = int(pj.state()["story"]["report"].get("pick", 1))
    o = opts[pick - 1]
    notes, rv, r = args.get("notes") or "无", None, {}
    for rnd in range(3):            # 写完交给「观众」审读，不过就把问题当修改意见重写
        r = chat([{"role": "user", "content": SCREENPLAY.format(option=json.dumps(o, ensure_ascii=False), dur=brief.get("duration", 55), ref=_ref(pj), intent=brief["intent"],
                                                                source=brief.get("source") or "无", episode=brief.get("episode") or "无", notes=notes)}],
                 model=WRITER, json_out=True, stream=True)
        rv = review_text("剧本", r.get("screenplay", "") + "\n台词表：" + json.dumps(r.get("dialogue", []), ensure_ascii=False), brief)
        pj.log(f"剧本第 {rnd + 1} 版审读：{'过' if rv['pass'] else '不过'}；复述：{rv['retell'][:120]}；问题：{'；'.join(rv['problems'])[:300]}")
        if rv["pass"]:
            break
        notes = (args.get("notes") or "") + "\n上一版审读意见（必须全部改掉）：" + "；".join(rv["problems"]) + "\n上一版剧本：" + r.get("screenplay", "")[:6000]
    pj.save("story/screenplay.json", r); pj.save("story/screenplay_review.json", rv)
    L = [f"# {r.get('title')}", "", r.get("screenplay", ""), "", "## 台词表", "| 角色 | 台词 | 表演指导 | 潜台词 |", "|---|---|---|---|"]
    L += [f"| {d.get('speaker')} | {d.get('text')} | {d.get('direction')} | {d.get('subtext')} |" for d in r.get("dialogue", [])]
    L += ["", "## 音色", *[f"- {k}：{v}" for k, v in (r.get("voices") or {}).items()]]
    L += ["", "## 观众审读", f"立意：{brief['intent']}", f"观众复述：{rv['retell']}", f"观众感到：{rv.get('felt', '')}", *[f"- {x}" for x in rv["problems"]]]
    open(pj.p("story/screenplay.md"), "w", encoding="utf-8").write("\n".join(L))
    pj.set("screenplay", "awaiting_approval", {"pick": pick, "review_pass": rv["pass"], "review": rv["problems"]})
    print(("✅ 剧本写好并通过观众审读" if rv["pass"] else "⚠️ 剧本写了 3 轮仍有审读问题（导演判断后改或放行）：\n- " + "\n- ".join(rv["problems"]))
          + f"\n复述：{rv['retell']}\n确认：vf approve <项目> screenplay；文件 {pj.p('story/screenplay.md')}")
