"""前期：剧本分镜表（script）→ 定角色和场景（cast）→ 每镜分镜图（boards）。"""
import concurrent.futures as cf, json, math, os, re, shutil
from vf_common import *

BANNED = re.compile(r"cinematic tension|epic|dramatic|emotional|humorous|intense|powerful|majestic|stunning|breathtaking|"
                    r"contrast|vibe|mood of|feeling of|atmosphere of|masterpiece|8k|ultra[- ]?realistic", re.I)

SCRIPT_PROMPT = """你是短片导演兼分镜师。根据下面的创作要求，写出可以直接拿去拍的分镜表。

创作要求：{req}
已确认的故事（如有，情节和台词含义照它来，只做拍摄上的拆分和打磨）：{story}
参考图里的主体与场景：{ref_desc}
目标总时长：约 {dur} 秒

必须遵守的行业规范：
1. 结构：开场 3 秒内有具体动作或悬念；起、承、转（反转）、合；结尾留余味。
2. 每个镜头只拍一个主要动作；景别（大远景/全景/中景/近景/特写/大特写）和机位运动（固定/推/拉/摇/跟）写清楚。
3. 镜头时长按内容定：纯动作或反应 2~5 秒；有台词的镜头 ≈ 台词字数÷4 + 1.5 秒；单镜不超过 15 秒（视频模型上限）。总时长靠镜头数凑，不要把单镜拉长。
4. 一个镜头最多一句台词、一个说话人；对白多就拆成说话人镜头和听者反应镜头。台词口语、短句，不写散文。
5. 前后一致：场景尽量少（≤3 个），在 scenes 里列出每个场景并写固定英文外观，每镜用 scene 指明在哪个场景；人物外观以参考图为准，不改服装。
6. 和上一镜是同一机位、同一动作的直接延续时，continuous_from 写上一镜的 id，否则 null。
7. image_prompt（英文）：这个镜头第一帧的画面：景别、机位角度、谁在画面哪个位置、姿态、道具、光线。只写看得见的东西。
8. video_prompt（英文）：从第一帧开始发生的事：一个具体的身体动作 + 一个机位运动 + 环境动态。禁止抽象形容词（cinematic tension、epic、dramatic、emotional、humorous 之类）。有台词的镜头写明谁在说、嘴随说话开合，并把台词原文用引号写进去，例如：The cat speaks in Mandarin, mouth moving with the words: "……"。不说话的角色写 mouth closed。
9. 避开视频模型容易崩的画面：人手从画外伸进来拿放东西、剧烈转身、多个角色肢体接触。道具需要出现时，改成道具已经在位的插入镜头加人物反应镜头。
   物理常识：道具不会自己动。要让东西移动，画面里必须拍到推它的手并在 video_prompt 写清楚；拍不了手，就让道具一开始就在位、全程静止（video_prompt 写 the plate stays completely still）。
10. 道具连续：列出关键道具（props），每个写一句固定的英文外观（会原样写进每个镜头），每镜 props 写这一镜画面里有哪些。同一道具全片外观不变（比如生鱼片不能变成熟肉、水杯不能变成酒杯）。
11. 反转/揭底：如果故事靠某个信息在后面揭开（比如角色身材、身份），揭开之前的每一镜在 hide 里写清楚画面不能出现什么，并用景别和机位藏住它；揭开那一镜及之后 hide 写空。
12. 画面里除了角色表里的人物，不要出现别的人。
14. 剪辑节奏与连贯（行业剪辑规范）：平均每镜 2~4 秒，只有关键情绪或揭底镜头可以到 5~7 秒；镜头之间按动作接、按视线接（上一镜看向哪，下一镜就是他看到的东西）；对话用正反打；保持 180 度轴线和左右方位一致；同一场景里不要无缘无故跳机位。每一镜都要回答「为什么现在切到这里」。
15. 台词精简：每句台词都必须推动情节或揭示人物，不能重复画面已经交代的信息，不要解释潜台词；能用表情和动作表达的就不用台词。全片台词越少越好。
17. 画面字幕（时间地点、「片刻前」「十年后」这类）写在 caption（中文，没有写空字符串），后期统一加；画面里的纸张、招牌等小字不要依赖它清楚可读（视频模型写字会糊），需要观众读懂的信息用 caption 或台词交代。
16. 每句台词写 direction（表演指导：情绪、语气、轻重、停顿）；每个说话角色写 voice_desc（中文音色描述：性别、年龄感、音高、质感、语速）。
13. 所有说话的人（包括只出声不出镜的画外音）都必须在 characters 里。画外音台词在 dialogue 里写 "offscreen": true，这一镜画面里的角色嘴巴闭着。

只输出 JSON，不要别的文字：
{{"title":"片名","logline":"一句话故事","theme":"想表达什么","characters":[{{"name":"角色名","look":"英文外观描述（会固定写进每个提示词）","has_ref":true,"voice_desc":"中文音色描述"}}],
"location":"主场景英文描述","scenes":[{{"name":"场景名","look":"英文固定外观：地点、陈设、光线、时间、天气"}}],"props":[{{"name":"道具名","look":"英文固定外观"}}],"shots":[{{"id":"s01","scene":"场景名","caption":"","props":["道具名"],"hide":"这一镜画面里不能出现的信息（英文，没有就写空字符串）","purpose":"这镜在故事里的作用","shot_size":"中景","camera":"slow dolly-in","action":"中文：一个动作","characters":["角色名"],"dialogue":{{"speaker":"角色名","text":"台词","offscreen":false,"direction":"表演指导"}},"duration":4,"continuous_from":null,"image_prompt":"...","video_prompt":"..."}}]}}
没有台词的镜头 dialogue 写 null。"""


def check_script(sc, brief):
    errs, warns = [], []
    shots = sc.get("shots") or []
    if not shots:
        return ["没有镜头"], []
    names = {c["name"] for c in sc.get("characters", [])}
    ids = [s.get("id") for s in shots]
    if len(set(ids)) != len(ids):
        errs.append("镜头 id 重复")
    total = sum(float(s.get("duration") or 0) for s in shots)
    target = float(brief.get("duration", 60))
    if not (0.8 * target <= total <= 1.2 * target):
        errs.append(f"总时长 {total:.0f}s，目标 {target:.0f}s（允许 ±20%）")
    for s in shots:
        sid, d = s.get("id"), float(s.get("duration") or 0)
        for f in ("shot_size", "camera", "action", "image_prompt", "video_prompt"):
            if not s.get(f):
                errs.append(f"{sid} 缺 {f}")
        if not 2 <= d <= 15:
            errs.append(f"{sid} 时长 {d}s 不在 2~15s")
        dl = s.get("dialogue")
        if dl:
            n = len(re.sub(r"[^一-鿿A-Za-z0-9]", "", dl.get("text", "")))
            need = n / 4.5 + 1.0
            if d < need:
                errs.append(f"{sid} 台词 {n} 字至少要 {need:.1f}s，现在 {d}s")
            if dl.get("speaker") not in names:
                errs.append(f"{sid} 说话人「{dl.get('speaker')}」不在角色表里")
            if not dl.get("offscreen") and '"' not in s.get("video_prompt", "") and "“" not in s.get("video_prompt", ""):
                errs.append(f"{sid} 有台词但 video_prompt 里没写台词原文")
        for f in ("image_prompt", "video_prompt"):
            m = BANNED.search(s.get(f, ""))
            if m:
                errs.append(f"{sid} {f} 里有抽象词「{m.group(0)}」，只写看得见的动作")
        if s.get("continuous_from") and s["continuous_from"] not in ids:
            errs.append(f"{sid} continuous_from 指向不存在的镜头")
        for c in s.get("characters", []):
            if c not in names:
                errs.append(f"{sid} 角色「{c}」不在角色表里")
        if sc.get("scenes") and s.get("scene") not in {x["name"] for x in sc["scenes"]}:
            errs.append(f"{sid} 场景「{s.get('scene')}」不在场景表里")
        for pp in s.get("props", []) or []:
            if pp not in {x["name"] for x in sc.get("props", [])}:
                errs.append(f"{sid} 道具「{pp}」不在道具表里")
        if d > 10:
            warns.append(f"{sid} {d}s 偏长，视频模型越长越容易变形")
    return errs, warns


SUPERVISOR = """你是场记（script supervisor），审查下面这份分镜表能不能拍、故事是否成立。逐条检查：
1. 反转/揭底：揭开前的镜头有没有提前暴露（看 image_prompt 和 video_prompt 有没有写出 hide 里要藏的东西、景别是否能藏住）。
2. 道具连续：同一道具每镜的描述是否一致；有没有道具凭空出现或消失。
3. 人物连续：服装、位置、朝向前后是否接得上；有没有角色表以外的人。
4. 叙事：每镜是否推动故事；台词是否符合人物；结尾是否落在主题上。
5. 节奏：有没有拖沓的镜头（同一信息拍了两遍、动作太少却给了很长时间）；有没有多余或重复的台词。
6. 剪辑连贯：镜头之间是否按动作/视线接上，方位和轴线是否一致。
只输出 JSON：{{"ok":true,"problems":["镜号：具体问题和改法"]}}。只列真问题，没有就空列表。
分镜表：{script}"""


def supervise(sc):
    try:
        r = chat([{"role": "user", "content": SUPERVISOR.format(script=json.dumps(sc, ensure_ascii=False))}], json_out=True)
        return [str(x) for x in (r.get("problems") or [])]
    except Exception as e:
        return [f"场记审查失败：{e}"]


def cmd_script(pj, args):
    """分镜表：有确认过的剧本就严格按剧本拆（台词、表演指导原样带过来），否则按需求写。"""
    brief = pj.load("brief.json")
    if pj.state().get("screenplay", {}).get("status") == "approved":
        sp = pj.load("story/screenplay.json")
        brief = dict(brief, story="【已确认的剧本，必须严格按它拆镜头，台词一字不改、表演指导原样带上，不加新台词】\n" + sp.get("screenplay", "")
                     + "\n台词表：" + json.dumps(sp.get("dialogue", []), ensure_ascii=False) + "\n音色：" + json.dumps(sp.get("voices", {}), ensure_ascii=False))
    refs = [r for v in brief.get("refs", {}).values() for r in (v if isinstance(v, list) else [v])]
    ref_desc = vision("用中文详细描述这张图：主体是谁、长相（毛色、脸型、眼睛）、穿着（每一件）、姿态、所处场景、光线、镜头景别。只描述看得见的。", refs[:1], json_out=False) if refs else "无"
    pj.save("script/ref_desc.json", {"desc": ref_desc})
    if args.get("from"):
        sc = json.load(open(args["from"], encoding="utf-8"))
        pj.log("剧本：用传入的分镜表")
    else:
        last_err = []
        for attempt in range(5):
            fix = ("\n\n上一版没过检查，问题：" + "；".join(last_err)) if last_err else ""
            if args.get("notes"):
                fix = "\n\n用户对上一版的修改意见（必须全部落实，故事内核不变）：" + args["notes"] + fix
            if args.get("prev"):
                fix = "\n\n上一版分镜表（在它基础上按意见改）：" + json.dumps(pj.load(args["prev"]), ensure_ascii=False)[:12000] + fix
            sc = chat([{"role": "user", "content": SCRIPT_PROMPT.format(req=brief.get("requirement", ""), story=brief.get("story", "无"),
                                                                          ref_desc=ref_desc, dur=brief.get("duration", 60)) + fix}], json_out=True)
            last_err, _ = check_script(sc, brief)
            if not last_err:
                last_err = supervise(sc)
            pj.log(f"剧本第 {attempt + 1} 版：{len(sc.get('shots', []))} 镜，问题 {len(last_err)} 个 " + "；".join(last_err)[:200])
            if not last_err:
                break
    errs, warns = check_script(sc, brief)
    if not errs:
        errs = last_err          # 场记审查结果用循环里最后一次的，不重复审（模型每次审的结论会有出入）
    pj.save("script/script.json", sc)
    write_script_md(pj, sc)
    pj.set("script", "passed" if not errs else "failed", {"errors": errs, "warnings": warns})
    print(("✅ 分镜表通过检查" if not errs else "❌ 剧本没过检查：\n- " + "\n- ".join(errs)) + ("\n提醒：" + "；".join(warns) if warns else ""))
    print("文件：" + pj.p("script/script.md"))


def write_script_md(pj, sc):
    L = [f"# {sc.get('title')}", f"一句话故事：{sc.get('logline')}", f"想表达：{sc.get('theme')}", "",
         "| 镜号 | 时长 | 景别/机位 | 动作 | 台词 | 作用 |", "|---|---|---|---|---|---|"]
    for s in sc["shots"]:
        dl = s.get("dialogue")
        L.append(f"| {s['id']} | {s['duration']}s | {s['shot_size']} / {s['camera']} | {s['action']} | "
                 f"{(dl['speaker'] + ('（画外）' if dl.get('offscreen') else '') + '：' + dl['text'] + ('〔' + dl.get('direction', '') + '〕' if dl.get('direction') else '')) if dl else '—'} | {s.get('purpose', '')} |")
    L.append(f"\n总时长 {sum(float(s['duration']) for s in sc['shots']):.0f} 秒，{len(sc['shots'])} 个镜头")
    open(pj.p("script/script.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")


IDENTITY = ("图1是角色「{name}」的标准形象（以它为准）。图2是新生成的画面（可能是设定卡、分镜或视频截图）。"
            "只比角色本身：构图、版式、姿势、动作、表情、背景、光线、镜头角度不同都不算差别，不要列。"
            "如果图1是没穿衣服的真实动物照片，图2是它的拟人化版本（直立、穿衣服）：直立的体态和衣服都不算差别，只比脸型、毛色和花纹、眼睛颜色和形状、耳朵。"
            "先逐项列出角色本身的差别，再打分。逐项比："
            "年龄感和体型比例（幼猫/成猫、胖瘦、头身比）、头型脸型、眼睛（大小形状颜色）、鼻子耳朵颜色、毛色、每一件衣服和鞋。"
            "如果图2里只露出这个角色的一部分（手、背影、侧面局部），只比对露出部分，一致就算同一角色，不要因为看不到脸判不同。"
            "再看图2有没有畸形（多肢、多指/多爪、脸部扭曲融化、肢体穿插）或画面里多出文字水印（角色设定卡上的标注文字除外）。"
            '只输出 JSON：{{"diffs":["差别"],"key_diff":false,"same":true,"score":0到10,"deformed":false,"problems":"一句话总结"}}。'
            "key_diff 指年龄感/体型比例/脸型/眼睛/毛色/衣服有明显不同；有就 same=false。分数 10=看不出差别，8=只有光线角度差别，6 以下=像另一只。")


def identity_ok(name, ref, img, need=8, final=False):
    try:
        prompt = IDENTITY.format(name=name)
        if final:   # 成片截图：底部字幕和右下角水印是成片本来就有的
            prompt += "（图2是成片截图，底部的对白字幕和右下角的 CHILLWANG 水印是正常的，不算多出的文字。）"
        r = vision(prompt, [ref, img])
        # 模型的分数习惯性给 7，不可靠；以「列出的关键差别」为准：没有关键差别、没畸形、列出的差别为空或分数够高才过
        ok = bool(r.get("same")) and not r.get("key_diff") and not r.get("deformed")   # 只看有没有关键差别；裤脚卷边这类细节不算
        return ok, r
    except Exception as e:
        return False, {"problems": f"比对失败：{e}"}


ANCHOR = ("给 AI 画图用：根据这张照片写一段 60~90 词的英文角色锚定描述，只写看得见的、能让人画出同一个角色的特征："
          "物种和年龄感（幼年/成年）、体型和头身比、头型脸型、眼睛、鼻子、耳朵、毛色/肤色、每一件衣服和鞋的颜色款式细节。"
          "不要猜品种名，不要写姿势和背景。只输出这段英文。")

SHEET = ("Professional character design reference sheet (model sheet) for a live-action short film, landscape layout, clean off-white background, "
         "neat editorial layout with thin divider lines and small Chinese section labels. The character must be exactly the individual in the reference photo: "
         "same age look, same body proportions and head-to-body ratio, same face, same eyes, same fur, same clothes. {anchor} {aura}"
         "Sections: (1) top-left: title「{name}」with a one-line tagline, and below it a large cinematic key-art portrait of the character (film-noir low-key lighting, colored rim light, rain or neon atmosphere) that sells the character's temperament; (2) center: three full-body standing views labeled 正面 / 侧面 / 背面, same scale; "
         "(3) top-right: expression grid of six head close-ups labeled 平静 / 微笑 / 皱眉 / 惊讶 / 认真 / 说话中(嘴张开); "
         "(4) bottom-left: outfit breakdown, each clothing item laid out flat and labeled; (5) bottom-middle: accessories and props; "
         "(6) bottom-right: detail close-ups (eyes, paws/hands, fabric texture, shoes) and a color palette row with swatches. "
         "Photorealistic rendering of the character in every panel, consistent lighting, no extra characters.")


def cmd_cast(pj, args):
    """角色设定卡（行业里的 model sheet）：一次生成一整张（三视图 + 表情 + 服装/配件拆解 + 细节 + 色板），保证卡内同一个角色；
    有原图的角色严格照原图画，并和原图比对。另出一张场景空镜。出完等用户确认。"""
    pj.require("script")
    brief, sc = pj.load("brief.json"), pj.load("script/script.json")
    user_refs = brief.get("refs", {})
    os.makedirs(pj.p("cast"), exist_ok=True)
    anchors = pj.load("cast/anchors.json") if os.path.exists(pj.p("cast/anchors.json")) else {}
    report, fails = {}, []

    def one(c):
        name = c["name"]; refs = user_refs.get(name) or []
        refs = refs if isinstance(refs, list) else [refs]
        if refs and name not in anchors:
            # 原图定长相（毛色花纹、脸、眼、体型），服装按剧本造型——原图可能是没穿衣服的真猫
            phys = vision(ANCHOR, refs[:1], json_out=False).strip()
            anchors[name] = phys + " Outfit: " + c.get("look", "")
        anchor = anchors.get(name) or c.get("look", "")
        anchors[name] = anchor
        dest = pj.p("cast", f"{name}_sheet.png")
        aura = (brief.get("aura") or {}).get(name, "")
        aura = (f" Screen presence and temperament (show it in posture, gaze and every expression, keep the physical identity unchanged): {aura}. "
                "Moody cinematic low-key lighting with colored rim light." if aura else "")
        prompt = SHEET.format(anchor=anchor, name=name, aura=aura)
        if refs:
            prompt += (" The reference photo only defines the face, fur color and pattern, eyes and body; it may show the animal without clothes. "
                       "Dress the character in the outfit described above. If the outfit text mentions a fur color that differs from the photo, follow the photo.")
        if not refs:     # 没有原图的角色：完全按文字设定画，不能套用「照参考照片」的说法（否则会被画成别的物种）
            prompt = prompt.replace("The character must be exactly the individual in the reference photo: same age look, same body proportions and head-to-body ratio, same face, same eyes, same fur, same clothes. ",
                                    "The character is defined only by this description, follow it exactly (species, age, build, clothes): ")
        r = {}
        if args.get("recheck") and os.path.exists(dest):
            if not refs:
                return name, True, {"note": "无原图，以这张设定卡为准"}
            ok, r = identity_ok(name, refs[0], dest)
            return name, ok, r
        for t in range(3):
            gen_image(prompt, refs, dest, size="1536x1024")
            if not refs:
                return name, True, {"note": "无原图，以这张设定卡为准"}
            ok, r = identity_ok(name, refs[0], dest)
            if ok:
                return name, True, r
            prompt += " Correct these differences from the reference photo: " + "; ".join(map(str, r.get("diffs", [])))[:400]
        return name, False, r

    with cf.ThreadPoolExecutor(3) as ex:
        for name, ok, r in ex.map(one, sc["characters"]):
            report[name] = r
            pj.log(f"角色设定卡 {name}：{'过' if ok else '不过'} {r.get('score', '')} {str(r.get('diffs', r.get('problems', '')))[:120]}")
            if not ok:
                fails.append(name)
    pj.save("cast/anchors.json", anchors)
    all_refs = [r for v in user_refs.values() for r in (v if isinstance(v, list) else [v])]
    scenes = sc.get("scenes") or [{"name": "location", "look": sc.get("location", "")}]
    for i, scn in enumerate(scenes):
        dest = pj.p("cast", "location.png" if len(scenes) == 1 else f"location_{scn['name']}.png")
        if args.get("recheck") and os.path.exists(dest):
            continue
        if i == 0:
            prompt = (f"Empty establishing plate of the exact same location as the reference photo, vertical composition, no people or animals. "
                      f"{scn['look']}. Same lighting, time of day and props. Photorealistic, no text.")
        else:
            prompt = (f"Empty establishing plate, vertical composition, no people or animals: {scn['look']}. "
                      f"Match the photographic style, color grade and lighting quality of the reference photo. Photorealistic, no text.")
        gen_image(prompt, all_refs[:1], dest)
    status = "awaiting_approval" if not fails else "failed"
    pj.set("cast", status, {"fails": fails, "detail": report})
    print(("✅ 角色设定卡和场景图做好了，请用户确认后 vf approve <项目> cast：" if not fails else "❌ 这些角色的设定卡和原图对不上：" + "、".join(fails) + "\n文件夹：") + pj.p("cast"))


CONT = ("这是短片第 {sid} 镜的画面。镜头要求：{action}（角色的站/坐等姿势必须和要求一致）。检查：1) 画面里必须有这些道具且外观一致：{props}；"
        "2) 画面里不能出现：{hide}；3) 除了 {chars} 之外不能有别的人或动物。"
        '只输出 JSON：{{"ok":true,"problems":"具体问题"}}')


def continuity_check(s, sp, props, chars, img):
    try:
        r = vision(CONT.format(sid=s["id"], action=s["action"], props="；".join(f"{p}（{props[p]}）" for p in sp) or "无",
                               hide=s.get("hide") or "无要求", chars="、".join(chars) or "（无角色）"), [img])
        return {"ok": bool(r.get("ok")), "problems": r.get("problems", "")}
    except Exception as e:
        return {"ok": False, "problems": f"检查失败：{e}"}


def crop_916(path):
    from PIL import Image
    im = Image.open(path).convert("RGB"); w, h = im.size
    tw = int(h * 9 / 16)
    if tw < w:
        im = im.crop(((w - tw) // 2, 0, (w - tw) // 2 + tw, h))
    else:
        th = int(w * 16 / 9); im = im.crop((0, (h - th) // 2, w, (h - th) // 2 + th))
    im.save(path, quality=95)


def needs_keyframe(sc, s):
    """要单独画首帧的镜头：每个场景的第一镜、要藏住东西的、全景/揭底这类构图要卡准的、或分镜表里标了 keyframe 的。
    和上一镜连续的用上一镜最后一帧；其余镜头直接拿角色设定卡+场景图生成视频，不单独画（省 gpt-image 额度）。"""
    if s.get("continuous_from"):
        return False
    if s.get("keyframe") or s.get("hide") or s.get("shot_size") in ("全景", "大远景"):
        return True
    same = [x for x in sc["shots"] if x.get("scene") == s.get("scene")]
    return bool(same) and same[0]["id"] == s["id"]


GRID = ("Professional film storyboard sheet, {n} numbered panels in a {cols}x{rows} grid on white paper, each panel vertical 9:16, "
        "panel number written small in the top-left corner of each panel. Photorealistic frames, consistent characters and location from the reference images. "
        "Panels in order:\n{panels}")


def make_grids(pj, sc, looks, ref_of):
    """给用户审的拼版分镜：每个场景一张（≤9 格），一次生成。"""
    out = []
    scenes = sc.get("scenes") or [{"name": None}]
    for scn in scenes:
        shots = [x for x in sc["shots"] if scn["name"] is None or x.get("scene") == scn["name"]]
        for k in range(0, len(shots), 9):
            part = shots[k:k + 9]
            cols = 3 if len(part) > 4 else 2; rows = -(-len(part) // cols)
            chars = list(dict.fromkeys(c for x in part for c in x.get("characters", [])))[:3]
            loc = pj.p("cast", f"location_{scn['name']}.png") if scn["name"] and len(scenes) > 1 else pj.p("cast", "location.png")
            refs = [pj.p("cast", f"{c}_sheet.png") for c in chars] + [loc]
            panels = "\n".join(f"{x['id']}: {x['shot_size']}. {x['image_prompt'][:260]}" for x in part)
            dest = pj.p("boards", f"grid_{scn['name'] or 'all'}_{k // 9 + 1}.png")
            for t in range(3):   # 出图偶尔超时（网关 524），多试几次；还不行就跳过这张拼版，不让整批停掉
                try:
                    gen_image(GRID.format(n=len(part), cols=cols, rows=rows, panels=panels), [r for r in refs if os.path.exists(r)], dest, "1536x1024")
                    out.append(dest); break
                except Exception as e:
                    pj.log(f"拼版 {os.path.basename(dest)} 第 {t + 1} 次出图失败：{str(e)[:120]}")
    return out


def cmd_boards(pj, args):
    pj.require("cast", "approved")
    brief, sc = pj.load("brief.json"), pj.load("script/script.json")
    anchors = pj.load("cast/anchors.json")
    looks = {c["name"]: anchors.get(c["name"]) or c["look"] for c in sc["characters"]}
    user_refs = {k: (v if isinstance(v, list) else [v]) for k, v in brief.get("refs", {}).items()}

    def ref_of(c):
        return (user_refs.get(c) or [pj.p("cast", f"{c}_sheet.png")])[0]
    os.makedirs(pj.p("boards"), exist_ok=True)
    only = set(args.get("shots") or [])
    report = pj.state().get("boards", {}).get("report", {}).get("detail", {}) if only else {}

    def one(s):
        sid = s["id"]; dest = pj.p("boards", f"{sid}.png")
        chars = s.get("characters", [])
        scn = next((x for x in sc.get("scenes") or [] if x["name"] == s.get("scene")), None)
        loc = pj.p("cast", f"location_{scn['name']}.png") if scn and len(sc.get("scenes") or []) > 1 else pj.p("cast", "location.png")
        refs = [ref_of(c) for c in chars] + [pj.p("cast", f"{c}_sheet.png") for c in chars] + [loc]
        refs = [r for r in dict.fromkeys(refs) if os.path.exists(r)]
        props = {x["name"]: x["look"] for x in sc.get("props", [])}
        sp = [p for p in s.get("props", []) or [] if p in props]
        prompt = (f"Film storyboard keyframe, vertical 9:16 frame, photorealistic still from a live-action short film. {s['image_prompt']} "
                  + " ".join(f"{c}: {looks.get(c, '')}." for c in chars)
                  + " ".join(f" Prop {p}: {props[p]}." for p in sp)
                  + (f" Do NOT show: {s['hide']}." if s.get("hide") else "")
                  + " No other people in frame besides the listed characters."
                  + f" Location: {(scn or {}).get('look') or sc.get('location', '')}. Match the characters and location in the reference images exactly. No text, no captions, no watermark.")
        last = {}
        for t in range(3):
            try:      # 出图偶尔超时（网关 524）：只算这一次没画成，不让整批停掉
                gen_image(prompt, refs, dest, "1024x1536"); crop_916(dest)
            except Exception as e:
                last = {"出图": {"problems": f"出图失败：{str(e)[:120]}"}}
                continue
            res, ok_all = {}, True
            for c in chars:
                ok, r = identity_ok(c, ref_of(c), dest)
                res[c] = r; ok_all &= ok
            cont = continuity_check(s, sp, props, chars, dest)
            res["连续性"] = cont; ok_all &= bool(cont.get("ok"))
            last = res
            if ok_all:
                return sid, True, res
            prompt += " Fix: " + "; ".join(str(r.get("problems", "")) for r in res.values())
        return sid, False, last

    shots = [s for s in sc["shots"] if (not only or s["id"] in only) and (only or needs_keyframe(sc, s))]
    pj.log("单独画首帧：" + "、".join(x["id"] for x in shots) + f"（共 {len(shots)} 张；其余镜头用上一镜尾帧或角色设定卡直接生成视频）")
    fails = []
    with cf.ThreadPoolExecutor(4) as ex:
        for sid, ok, r in ex.map(one, shots):
            report[sid] = r
            pj.log(f"分镜图 {sid}：{'过' if ok else '不过'} " + "；".join(f"{k}:{v.get('score', v.get('problems', ''))}" for k, v in r.items())[:200])
            if not ok:
                fails.append(sid)
    grids = make_grids(pj, sc, looks, ref_of) if (not only or args.get("grids")) else []
    sheet = contact_sheet(pj, sc)
    # 跨镜头连续性（单张检查看不出来）：把整张分镜总览交给模型按场记标准过一遍
    try:
        cr = vision("这是一部短片按顺序排列的分镜总览（每格下方有镜号和动作说明）。按场记标准检查镜头之间的连续性："
                    "同一角色的姿势状态（站/坐/在哪）前后是否接得上、道具位置和数量、服装、光线和时间、场景布置。"
                    "说明里写明了变化的不算问题。只输出 JSON：{\"ok\":true,\"problems\":[\"镜号：问题\"]}", [sheet])
        cont_probs = [str(x) for x in (cr.get("problems") or [])]
    except Exception as e:
        cont_probs = [f"连续性检查失败：{e}"]
    if cont_probs:   # 场记意见：写进状态、列给用户（导演）判断，不自动卡住——模型每次挑的点不稳定，硬卡会无限重画
        pj.log("跨镜头连续性提醒：" + "；".join(cont_probs)[:300])
    status = "awaiting_approval" if not fails else "failed"
    pj.set("boards", status, {"fails": fails, "detail": report, "sheet": sheet, "grids": grids, "continuity": cont_probs})
    if grids:
        print("拼版分镜（给用户审）：\n" + "\n".join(grids))
    if cont_probs:
        print("场记提醒（请用户看分镜时一并判断）：\n- " + "\n- ".join(cont_probs))
    print(("✅ 分镜图全部通过检查，等你确认：" if not fails else f"❌ 这些镜头的分镜图没过（可用 --shots 单独重做）：{'、'.join(fails)}\n分镜总览：") + sheet)


def _font(size):
    from PIL import ImageFont
    for f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def contact_sheet(pj, sc, cols=4, tw=360):
    from PIL import Image, ImageDraw
    th = int(tw * 16 / 9); cap = 150
    n = len(sc["shots"]); rows = math.ceil(n / cols)
    # 最上面一排：角色参照图 + 场景空镜（审分镜时一起看）
    cast_files = sorted(f for f in os.listdir(pj.p("cast")) if f.endswith(".png")) if os.path.isdir(pj.p("cast")) else []
    cw = 200; ch = 260; ccols = max(1, (cols * (tw + 20)) // (cw + 14))
    crow_h = (math.ceil(len(cast_files) / ccols) * (ch + 50)) if cast_files else 0
    head = 90 + (crow_h + 50 if cast_files else 0)
    sheet = Image.new("RGB", (cols * (tw + 20) + 20, head + rows * (th + cap + 20) + 20), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    lg = sc.get('logline', '')
    d.text((20, 20), f"{sc.get('title')}  ·  {lg[:38]}", fill=(240, 240, 240), font=_font(30))
    if len(lg) > 38:
        d.text((20, 56), lg[38:90], fill=(200, 200, 200), font=_font(24))
    if cast_files:
        d.text((20, 100), "角色与场景参照（全片照这套画）", fill=(255, 210, 90), font=_font(24))
        for j, f in enumerate(cast_files):
            from PIL import ImageOps
            im = ImageOps.fit(Image.open(pj.p("cast", f)).convert("RGB"), (cw, ch))
            x = 20 + (j % ccols) * (cw + 14); y = 140 + (j // ccols) * (ch + 50)
            sheet.paste(im, (x, y)); d.text((x, y + ch + 6), f.replace(".png", ""), fill=(220, 220, 220), font=_font(18))
    f1, f2 = _font(22), _font(19)
    for i, s in enumerate(sc["shots"]):
        x = 20 + (i % cols) * (tw + 20); y = head + (i // cols) * (th + cap + 20)
        bp = pj.p("boards", f"{s['id']}.png")
        if os.path.exists(bp):
            im = Image.open(bp).convert("RGB").resize((tw, th)); sheet.paste(im, (x, y))
        d.text((x, y + th + 6), f"{s['id']}  {s['duration']}s  {s['shot_size']}", fill=(255, 210, 90), font=f1)
        d.text((x, y + th + 36), s["action"][:18], fill=(220, 220, 220), font=f2)
        dl = s.get("dialogue")
        if dl:
            t = f"{dl['speaker']}：{dl['text']}"
            d.text((x, y + th + 64), t[:18], fill=(150, 200, 255), font=f2)
            if len(t) > 18:
                d.text((x, y + th + 90), t[18:36], fill=(150, 200, 255), font=f2)
    out = pj.p("boards", "storyboard.jpg"); sheet.save(out, quality=88)
    return out
