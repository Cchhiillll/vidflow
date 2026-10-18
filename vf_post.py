"""制作与后期：视频片段（clips）→ 对白（voice）→ 合成（mix）→ 成片检查（qc）。"""
import base64, concurrent.futures as cf, difflib, hashlib, json, os, re, shutil, subprocess
from vf_common import *
from vf_pre import identity_ok, continuity_check

FPS = 24


def ref_for(pj, c):
    """角色比对基准：有原图用原图，没有就用它的设定卡。"""
    r = pj.load("brief.json").get("refs", {}).get(c)
    r = (r if isinstance(r, list) else [r]) if r else []
    return r[0] if r else pj.p("cast", f"{c}_sheet.png")


def frame_at(video, t, dest):
    sh("ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, t):.3f}", "-i", video, "-frames:v", "1", "-q:v", "2", dest, check=True)
    return dest


def last_frame(video, dest):
    sh("ffmpeg", "-v", "error", "-y", "-sseof", "-0.15", "-i", video, "-frames:v", "1", "-q:v", "2", dest, check=True)
    return dest


def frozen_ratio(video, dur):
    err = sh("ffmpeg", "-i", video, "-vf", "freezedetect=n=0.002:d=1.0", "-map", "0:v", "-f", "null", "-").stderr
    tot = sum(float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", err))
    return tot / max(dur, 0.1)


CLIP_CHECK = ("这是短片第 {sid} 镜视频里的一帧。只检查四件单帧能判断的事："
              "1) 不能出现：{hide}；2) 角色的身体状态要和要求一致（站着还是坐着、在不在凳子上）：{action}；3) 除了 {chars} 不能有近处的别的人或动物（远处背景里模糊的路人不算）；4) 画面上不能叠加字幕或对白文字（纸张、招牌等道具上本来印着的字不算）。"
              "视线朝向、头往哪边转、动作做没做完、道具是否在这一帧画面里，都不用管。"
              '只输出 JSON：{{"ok":true,"problems":"具体问题"}}')


def clip_check(s, chars, img):
    try:
        r = vision(CLIP_CHECK.format(sid=s["id"], hide=s.get("hide") or "无要求", action=s["action"], chars="、".join(chars) or "（无角色）"), [img])
        return {"ok": bool(r.get("ok")), "problems": r.get("problems", "")}
    except Exception as e:
        return {"ok": False, "problems": f"检查失败：{e}"}


def check_take(pj, s, path, chars):
    """返回 (是否合格, 问题列表)。"""
    pr, probs = probe(path), []
    if pr["h"] < 1200 or abs(pr["w"] / max(pr["h"], 1) - 9 / 16) > 0.02:
        probs.append(f"画幅 {pr['w']}×{pr['h']} 不是 720×1280 竖屏")
    if pr["dur"] < float(s["duration"]) - 0.3:
        probs.append(f"时长 {pr['dur']:.1f}s 短于分镜 {s['duration']}s")
    fr = frozen_ratio(path, pr["dur"])
    if fr > 0.5:
        probs.append(f"画面一半以上时间是静止的（{fr:.0%}）")
    # 物理：比开头和结尾两帧，没人碰的东西不能自己挪、凭空出现或消失（10-17 上菜镜头盘子自己滑）
    f0 = frame_at(path, 0.15, path + ".start.jpg"); f1 = frame_at(path, pr["dur"] - 0.15, path + ".end.jpg")
    try:
        ph = vision("图1是这段视频的开头，图2是结尾。镜头要求：" + s["action"] + "。检查有没有违反物理常识的变化："
                    "没有被手拿着或推动的物体（盘子、杯子、食物、家具）自己移动了位置、凭空出现或消失；角色身体不自然地变形，或从坐变站、从站变坐。"
                    "视线和头部朝向的变化不算问题。"
                    '镜头推拉摇移造成的位置变化不算。只输出 JSON：{"ok":true,"problems":"具体问题"}', [f0, f1])
        if not ph.get("ok"):
            probs.append("物理：" + str(ph.get("problems", ""))[:100])
    except Exception as e:
        probs.append(f"物理检查失败：{e}")
    for t in (pr["dur"] * 0.5, pr["dur"] - 0.2):
        f = frame_at(path, t, path + f".{t:.1f}.jpg")
        for c in chars:
            ok, r = identity_ok(c, ref_for(pj, c), f)
            if not ok:
                probs.append(f"{t:.1f}s 处{c}：{r.get('problems', '')[:80]}")
        # 10-17：视频模型会自己把镜头拉开/改姿势，导致提前露底、姿势不接——片段也要过「道具/不能出现/多余人物/动作」检查
        cr = clip_check(s, chars, f)
        if not cr.get("ok"):
            probs.append(f"{t:.1f}s 处连续性：{str(cr.get('problems', ''))[:100]}")
    return not probs, probs


PICK = ("这是同一个镜头的 {n} 条候选，每条按顺序给了开头、中间、结尾三帧（第 1 条是图1~3，第 2 条是图4~6，依此类推）。"
        "镜头要求：{action}。请选最符合要求、动作最自然、角色没有变形的一条。只输出 JSON：{{\"pick\":序号(从1开始),\"why\":\"理由\"}}")


def pick_best(s, takes):
    if len(takes) == 1:
        return takes[0], "只有一条合格"
    frames = []
    for t in takes:
        d = probe(t)["dur"]
        frames += [frame_at(t, x, t + f".pick{i}.jpg") for i, x in enumerate((0.1, d / 2, d - 0.2))]
    try:
        r = vision(PICK.format(n=len(takes), action=s["action"]), frames)
        i = max(1, min(len(takes), int(r.get("pick", 1))))
        return takes[i - 1], r.get("why", "")
    except Exception as e:
        return takes[0], f"挑选失败，用第一条（{e}）"


def trim(src, dur, dest):
    sh("ffmpeg", "-v", "error", "-y", "-i", src, "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
       "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", dest, check=True)


def cmd_clips(pj, args):
    pj.require("boards", "approved")
    pj.require("voice")          # 先录对白再拍画面（动画/配音行业的做法）：视频按台词真实时长张嘴
    sc = pj.load("script/script.json")
    lines = pj.load("audio/lines.json")
    anchors = pj.load("cast/anchors.json")
    looks = {c["name"]: anchors.get(c["name"]) or c["look"] for c in sc["characters"]}
    ntake = int(args.get("takes") or 2)
    only = set(args.get("shots") or [])
    os.makedirs(pj.p("clips"), exist_ok=True)
    picks = pj.load("clips/picks.json") if os.path.exists(pj.p("clips/picks.json")) else {}
    report = {}

    def do_shot(s):
        sid, chars = s["id"], s.get("characters", [])
        start = pj.p("boards", f"{sid}.png")
        refs = None
        src = s.get("continuous_from")
        if src and src in picks:                       # 连续镜头：用上一镜成片的最后一帧当首帧，接缝处是同一帧
            start = last_frame(pj.p("clips", picks[src]["file"]), pj.p("clips", f"{sid}_start.jpg"))
        elif not os.path.exists(start):                # 没单独画首帧：拿角色原图/设定卡 + 场景图直接生成视频
            scenes = sc.get("scenes") or []
            loc = pj.p("cast", f"location_{s.get('scene')}.png") if len(scenes) > 1 else pj.p("cast", "location.png")
            refs = [ref_for(pj, c) for c in chars] + [pj.p("cast", f"{c}_sheet.png") for c in chars] + [loc]
            refs = [r for r in dict.fromkeys(refs) if os.path.exists(r)][:7]
            start = None
        # 图生视频只写动作和机位，外形全由首帧决定（10-17 教训：把「牛仔裤、皮鞋」写进提示词，Grok 就会把腿和鞋画出来、导致露底）
        if start:
            prompt = (s["video_prompt"] + " Keep every character's appearance, framing and the visible parts of the body exactly as in the first frame;"
                      " do not reveal anything that is outside the first frame. Vertical 9:16, live-action film look. No subtitles, captions or any on-screen text overlaid on the picture.")
        else:                                          # 参照图生成：要把构图写进去，长相衣服以参照图为准
            prompt = (f"Opening frame: {s['shot_size']}, {s['image_prompt']} Then: {s['video_prompt']} "
                      "The characters must look exactly like the reference images (same face, fur and outfit). "
                      + (f"Do NOT show: {s['hide']}. " if s.get("hide") else "") + "Vertical 9:16, live-action film look. No subtitles, captions or any on-screen text overlaid on the picture.")
        ln = lines.get(sid)
        if ln and not (s.get("dialogue") or {}).get("offscreen"):
            sa = float((s.get("dialogue") or {}).get("speak_at", 0.5))     # 分镜表可指定先做完动作再开口（10-18 s21：放下枪、举手之后才说话）
            prompt += (f" Timing: the speaking character starts talking about {sa:.1f} seconds into the shot and keeps talking continuously "
                       f"for about {ln['dur']:.1f} seconds, mouth opening and closing on every syllable, then closes the mouth and stays silent.")
        good, bad = [], {}
        if args.get("recheck"):                        # 只用新规则重查已生成的片段，不花额度重新生成
            import glob
            for out in sorted(glob.glob(pj.p("clips", f"{sid}_t*.mp4"))):
                ok, probs = check_take(pj, s, out, chars)
                (good.append(out) if ok else bad.__setitem__(os.path.basename(out), probs))
                pj.log(f"重查片段 {os.path.basename(out)}：{'过' if ok else '不过 ' + '；'.join(probs)[:150]}")
            if not good:
                return sid, None, bad, ""
            best, why = pick_best(s, good)
            final = pj.p("clips", f"{sid}.mp4"); trim(best, float(s["duration"]), final)
            return sid, os.path.basename(final), bad, why
        for k in range(ntake + 2):                     # 先生成 ntake 条；都不合格再补两条
            if len(good) >= ntake or (k >= ntake and good):
                break
            out = pj.p("clips", f"{sid}_t{k + 1}.mp4")
            try:
                gen_video(prompt, start, s["duration"], out, refs=refs)
            except Exception as e:
                bad[f"t{k + 1}"] = [str(e)[:120]]; continue
            ok, probs = check_take(pj, s, out, chars)
            (good.append(out) if ok else bad.__setitem__(f"t{k + 1}", probs))
            pj.log(f"片段 {sid} t{k + 1}：{'过' if ok else '不过 ' + '；'.join(probs)[:150]}")
        if not good:
            return sid, None, bad, ""
        best, why = pick_best(s, good)
        final = pj.p("clips", f"{sid}.mp4")
        trim(best, float(s["duration"]), final)
        return sid, os.path.basename(final), bad, why + (f"｜line_dur={ln['dur']:.2f}" if ln else "")

    shots = [s for s in sc["shots"] if not only or s["id"] in only]
    first = [s for s in shots if not s.get("continuous_from")]
    chain = [s for s in shots if s.get("continuous_from")]
    fails = []
    with cf.ThreadPoolExecutor(3) as ex:
        for sid, f, bad, why in ex.map(do_shot, first):
            report[sid] = {"rejected": bad, "why": why}
            if f:
                picks[sid] = {"file": f, "why": why}
            else:
                fails.append(sid); picks.pop(sid, None)      # 重做失败就作废旧的挑选，不许拿被否的旧片段混过去
    for s in chain:                                    # 连续镜头按顺序做，等上一镜挑好
        sid, f, bad, why = do_shot(s)
        report[sid] = {"rejected": bad, "why": why}
        if f:
            picks[sid] = {"file": f, "why": why}
        else:
            fails.append(sid); picks.pop(sid, None)      # 重做失败就作废旧的挑选，不许拿被否的旧片段混过去
    pj.save("clips/picks.json", picks)
    missing = [s["id"] for s in sc["shots"] if s["id"] not in picks]
    pj.set("clips", "passed" if not missing else "failed", {"fails": missing, "detail": report})
    print("✅ 所有镜头都有合格片段" if not missing else "❌ 这些镜头没生成出合格片段（可 --shots 重做，或回到分镜改这镜）：" + "、".join(missing))


def cmd_reframe(pj, args):
    """剪辑里的放大重构图（punch-in）：视频模型不听机位、露出了不该露的东西时，取某条片段的一部分画面放大到全屏。
    vf reframe <项目> --shot s01 --take t2 --top 0 --height 0.65   （top/height 是占画面高度的比例，宽度按 9:16 自动取中间）"""
    pj.require("boards", "approved")
    sc = pj.load("script/script.json")
    s = next(x for x in sc["shots"] if x["id"] == args["shot"])
    src = pj.p("clips", f"{s['id']}_{args.get('take', 't1')}.mp4")
    top, hh = float(args.get("top", 0)), float(args.get("height", 0.7))
    pr = probe(src)
    ch = int(pr["h"] * hh) // 2 * 2; cw = int(ch * 9 / 16) // 2 * 2
    y = int(pr["h"] * top); x = (pr["w"] - cw) // 2
    out = pj.p("clips", f"{s['id']}_reframed.mp4")
    sh("ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos",
       "-c:v", "libx264", "-crf", "16", "-c:a", "copy", out, check=True)
    ok, probs = check_take(pj, s, out, s.get("characters", []))
    pj.log(f"重构图 {s['id']}（{args.get('take')} 取高度 {top:.2f}~{top + hh:.2f}）：{'过' if ok else '不过 ' + '；'.join(probs)[:200]}")
    if not ok:
        raise SystemExit("❌ 重构图后仍没过检查：" + "；".join(probs))
    final = pj.p("clips", f"{s['id']}.mp4"); trim(out, float(s["duration"]), final)
    picks = pj.load("clips/picks.json"); picks[s["id"]] = {"file": os.path.basename(final), "why": f"放大重构图自 {args.get('take')}"}
    pj.save("clips/picks.json", picks)
    missing = [x["id"] for x in sc["shots"] if x["id"] not in picks]
    pj.set("clips", "passed" if not missing else "failed", {"fails": missing, "reframed": s["id"]})
    print(f"✅ {s['id']} 重构图通过检查并已替换" + ("" if not missing else "；还缺：" + "、".join(missing)))


# ---------- 对白 ----------
_asr = [None]


ASR_NAMES = [""]     # 片里的人名提示给听写（10-18：「锦山」被听成「李珊」，把念对的台词判成不合格）


def set_asr_names(sc):
    names = [c["name"] for c in sc.get("characters", [])]
    extra = re.findall(r"[一-鿿]{2}", "".join(n for n in names))
    places = [re.sub(r"(酒吧|大门|出口|门廊|街口)$", "", x["name"]) for x in sc.get("scenes", [])]   # 地名也提示（10-18 第2集「赛蕾娜」被听成「瑟琳娜」）
    ASR_NAMES[0] = ("人名、地名：" + "、".join(dict.fromkeys((names + ["一马", "锦", "由美", "堂岛", "桐生"] if "桐生" in names else names) + places)) + "。") if names else ""
    if sc.get("asr_hints"):      # 分镜表可写容易被听错的词（10-18 第2集：「两清」听成「领轻」、「白蹲」听成「白顿」）
        ASR_NAMES[0] += "常用词：" + "、".join(sc["asr_hints"]) + "。"


def asr(path):
    """本机 faster-whisper large-v3-turbo（已缓存），提示用简体普通话；MiMo 的听写要付费余额，不用。"""
    if _asr[0] is None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")   # 模型已缓存；联网检查更新曾卡死整个流程（10-18）
        from faster_whisper import WhisperModel
        _asr[0] = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
    segs, _ = _asr[0].transcribe(path, language="zh", beam_size=5, initial_prompt="以下是普通话的句子，使用简体中文。" + ASR_NAMES[0],
                                 condition_on_previous_text=False)
    return "".join(s.text for s in segs)


def norm_zh(t):
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t or "")


def same_line(heard, text):
    """按读音比（同音字不算错，比如 蓝鳍/蓝棋）。"""
    from pypinyin import lazy_pinyin
    a, b = lazy_pinyin(norm_zh(heard)), lazy_pinyin(norm_zh(text))
    return difflib.SequenceMatcher(None, a, b).ratio()


def with_pause(t):
    """在第一个自然停顿处加逗号：已有标点就不动；否则在第 2 个字后（多为主语/名词后）加。"""
    core = re.sub(r"[。！？!?]+$", "", t)
    if re.search(r"[，,、]", core) or len(norm_zh(core)) < 4:
        return t
    return core[:2] + "，" + core[2:] + t[len(core):]


def tts_text(t):
    """送 TTS 前去掉开头的省略号/标点（「……刀口」会被念乱）。"""
    return re.sub(r"^[\s…．.。，,、！!？?—-]+", "", t or "").strip()


FILL = """给这份短片分镜表补配音信息。只输出 JSON：
{{"voices":{{"角色名":"中文音色描述：性别、年龄感、音高、质感（沙哑/清亮…）、语速、说话习惯，40~80字"}},
"directions":{{"镜号":"这句台词的表演指导：情绪、语气、轻重、停顿，20~40字"}}}}
只给有台词的角色和镜号。音色要贴合人物设定，不要卡通腔。
分镜表：{script}"""

PRESET_REFS = {"taijian": "taijian_voice_reference", "roxy": "roxy_voice_reference_zh", "eris": "eris_voice_reference",
               "sylphiette": "sylphiette_voice_reference", "aisha": "aisha_voice_reference", "norn": "norn_voice_reference"}


def voice_ref(pj, c, spec, desc, audition_text):
    """角色的声音参考：用户给的音频文件 / 已有克隆音色 / 按描述设计（设计出的试音就是之后克隆的参考）。"""
    import vf_voice
    ref = pj.p("cast", f"voice_{c}.mp3")
    if spec.startswith("preset:"):                      # MiMo 自带固定音色（苏打、白桦、冰糖、茉莉…）
        json.dump({"preset": spec.split(":", 1)[1]}, open(pj.p("cast", f"voice_{c}.json"), "w"), ensure_ascii=False)
        return None, f"MiMo 固定音色 {spec.split(':', 1)[1]}"
    if os.path.exists(pj.p("cast", f"voice_{c}.json")):
        os.remove(pj.p("cast", f"voice_{c}.json"))
    if spec and os.path.exists(spec):
        shutil.copy(spec, ref); return ref, "用户提供的参考音"
    if spec in PRESET_REFS:
        b64 = f"{HOME}/.hermes/assets/{PRESET_REFS[spec]}.mp3.b64"
        open(ref, "wb").write(base64.b64decode(open(b64).read().strip())); return ref, f"已有克隆音色 {spec}"
    female = bool(re.search("女|少女|姑娘|阿姨", desc or "")) and not re.search("男", (desc or "")[:6])
    lo, hi = (150, 330) if female else (70, 190)
    cands = []
    for k in range(3):              # 设计音色每次都不一样：出 3 个候选，按「念得对 + 音高在该性别区间」客观挑一个当参考
        f = pj.p("cast", f"voice_{c}_design{k}.wav")
        try:
            vf_voice.design(desc, audition_text, f)
        except Exception as e:
            pj.log(f"设计声音 {c} 第 {k + 1} 个失败：{str(e)[:80]}"); continue
        sim = same_line(asr(f), audition_text); f0 = f0_median(f)
        ok = lo <= f0 <= hi
        cands.append((sim + (0.3 if ok else 0), f, sim, f0))
        pj.log(f"设计声音 {c} 候选 {k + 1}：读音相似 {sim:.0%}，音高 {f0:.0f}Hz（{'在' if ok else '不在'}{'女' if female else '男'}声区间）")
    if not cands:
        raise RuntimeError(f"{c} 的声音设计全部失败")
    best = max(cands)
    sh("ffmpeg", "-v", "error", "-y", "-i", best[1], "-ar", "24000", "-ac", "1", ref, check=True)
    return ref, f"按描述设计（3 选 1：读音 {best[2]:.0%}，音高 {best[3]:.0f}Hz）"


def say(pj, c, direction, text, dest):
    """按角色定好的声音直接念一段（试音、长段落合成用）：固定音色或克隆参考。"""
    import vf_voice
    pf = pj.p("cast", f"voice_{c}.json")
    if os.path.exists(pf):
        return vf_voice.preset(json.load(open(pf))["preset"], direction, text, dest)
    return vf_voice.clone(pj.p("cast", f"voice_{c}.mp3"), direction, text, dest)


def voice_style(sc, c):
    """克隆/固定音色的人物描述（Hermes 实测：不给「用某某（性格）的语调说话」，模型念得很平）。分镜表角色可写 voice_style 覆盖。"""
    ch = next((x for x in sc.get("characters", []) if x["name"] == c), {})
    if ch.get("voice_style"):
        return ch["voice_style"]
    return f"用{c}（{ch.get('voice_desc') or '自然'}）的语调说话，普通话标准，咬字清晰纯正"


def f0_median(path):
    """粗测说话音高中位数（Hz），用来查声音有没有漂（男声跑成女声、忽高忽低）。只用 numpy 自相关，够分辨大偏差。"""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    fr, hop, out = 640, 320, []
    for k in range(0, len(x) - fr, hop):
        w = x[k:k + fr] - x[k:k + fr].mean()
        if np.sqrt((w ** 2).mean()) < 0.02:
            continue
        ac = np.correlate(w, w, "full")[fr - 1:]
        lo, hi = 16000 // 400, 16000 // 70
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] > 0.3 * ac[0]:
            out.append(16000 / lag)
    return float(np.median(out)) if len(out) >= 5 else 0.0


def _char_times(path):
    """faster-whisper 逐字时间：[(字, 起, 止)]。"""
    asr(path)
    segs, _ = _asr[0].transcribe(path, language="zh", beam_size=5, word_timestamps=True,
                                 initial_prompt="以下是普通话的句子，使用简体中文。" + ASR_NAMES[0], condition_on_previous_text=False)
    out = []
    for s in segs:
        for w in s.words:
            t = norm_zh(w.word)
            for i2, ch in enumerate(t):
                out.append((ch, w.start + (w.end - w.start) * i2 / max(1, len(t)), w.end))
    return out


def _silences(path, db=-38, d=0.12):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af", f"silencedetect=n={db}dB:d={d}", "-f", "null", "-"], capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", r)]; en = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", r)]
    return list(zip(st, en))


def _ends_ok(heard, text):
    """听写的开头两个字、结尾两个字和台词读音一致（同音字算对）。"""
    from pypinyin import lazy_pinyin
    h, t = lazy_pinyin(norm_zh(heard)), lazy_pinyin(norm_zh(text))
    if len(t) < 2 or len(h) < 2:
        return h == t
    return h[:2] == t[:2] and h[-2:] == t[-2:]


def _lead_cut(full, lead, n):
    """找前文和台词的分界点。句子之间的停顿总比句内停顿长：在「它后面剩下的说话时长 ≈ 这句台词按语速该有的长度」的范围里，
    取最长的那个停顿下刀（10-18：靠听写认字定位，会被句内停顿、前文和台词重字、单字称呼认不出带偏，剪掉「一马」「放开她」）。"""
    ch = _char_times(full)
    if len(ch) < max(2, n // 2):
        return None
    sp0, sp1 = ch[0][1], ch[-1][2]
    rate = (sp1 - sp0) / len(ch)                 # 每个字大约多少秒（含停顿）
    expect = n * rate
    t0 = ch[-n][1] if len(ch) >= n else ch[0][1]   # 按字数倒推的台词起点（听写漏字时会偏，只作参考）
    cands = [((b - a) - 1.5 * abs(b - t0), a, b) for a, b in _silences(full, db=-40, d=0.08) if a > sp0 and 0.8 * expect <= sp1 - b <= 1.3 * expect]
    if cands:                                     # 停顿越长越像句子分界，离推算起点越近越可信（下限 0.8：句内停顿不会被当成分界）
        return max(0.0, max(cands)[2] - 0.06)
    return max(0.0, t0 - 0.08)


def _rate_ok(dur, text, direction=""):
    """语速：平均每字不超过 0.36 秒（10-18 第一集：堂岛 15 个字念了 8 秒、紧急电话念得拖沓）；
    台词带「……」或表演指导写了慢/轻声/一字一顿/自言自语的放宽到 0.6 秒。"""
    n = max(1, len(norm_zh(text)))
    slow = "……" in text or bool(re.search("慢|轻声|很轻|一字一顿|一字一句|自言自语|几乎说不出", direction or ""))
    return dur <= (0.6 if slow else 0.36) * n + 0.4      # +0.4 秒：短句前后的起音收尾和句中停顿（「有种，你就开枪」6 个字要 2.5 秒左右）


def say_line(pj, sc, sid, c, text, lead, dest, ref_f0=0.0, tries=6, direction=""):
    """录一句片中台词（10-18 定稿做法，用户认可的质量）：
    短句单独合成很不稳（吞字、走调、乱加词），所以把这句接在一段同情绪的「前文」后面一起合成（完整原声参考、wav 无损），
    再按逐字时间把这一句剪出来；每次都听写核对（读音一致、不多字）、查音高没漂，最多录 tries 次挑最好的。
    返回 (成功, 听写, 相似度, 说明)。"""
    n = len(norm_zh(text)); best = None
    lead = (lead or "").strip()
    style = voice_style(sc, c)
    plan = [lead] * tries + ([""] * 3 if lead else [])     # 带前文的都不合格，再试 3 次不带前文直接念（10-18 s01：模型把前文和台词连着念，没有下刀点）
    for k, lead in enumerate(plan):
        if k >= tries and best and best[0] >= 0.95:
            break
        full, cut = pj.p("audio", f"{sid}_full{k}.wav"), pj.p("audio", f"{sid}_take{k}.wav")
        try:
            say(pj, c, style, (lead + text) if lead else text, full)
        except Exception as e:
            pj.log(f"对白 {sid} 第 {k + 1} 次合成失败：{str(e)[:80]}"); continue
        start = 0.0
        if lead:
            start = _lead_cut(full, lead, n)
            if start is None:
                continue
        sh("ffmpeg", "-v", "error", "-y", "-i", full, "-ss", f"{start:.3f}", "-af",
           "silenceremove=start_periods=1:start_threshold=-45dB,areverse,silenceremove=start_periods=1:start_threshold=-45dB,areverse,afade=t=in:d=0.015",
           "-ar", "48000", "-ac", "1", cut, check=True)
        heard = asr(cut); sim = same_line(heard, text)
        extra = len(norm_zh(heard)) > n + 1
        ends_ok = _ends_ok(heard, text)        # 开头、结尾两个字必须念到（10-18：「一马！」被剪掉还按 91% 放过）
        rate_ok = _rate_ok(probe(cut)["dur"], text, direction)
        f0 = f0_median(cut)
        drift = bool(ref_f0 and f0 and (f0 < ref_f0 * 0.7 or f0 > ref_f0 * 1.8))   # 喊叫、激动时音高自然升高，只有偏低 30% 或高到 1.8 倍才算漂（像换了个人）
        score = sim - (0.5 if extra else 0) - (0.3 if drift else 0) - (0.3 if not ends_ok else 0) - (0.3 if not rate_ok else 0)
        pj.log(f"对白 {sid} 第 {k + 1} 版：读音相似 {sim:.0%}{'，多字' if extra else ''}{'' if rate_ok else '，太慢'}，音高 {f0:.0f}Hz{'（漂了）' if drift else ''}，听写「{heard}」")
        if best is None or score > best[0]:
            best = (score, cut, heard, sim, f0)
        if sim >= 0.99 and not extra and not drift and ends_ok and rate_ok:
            break
    if not best or best[0] < 0.95:
        return False, best[2] if best else "", best[3] if best else 0.0, "录不出合格的一版"
    sh("ffmpeg", "-v", "error", "-y", "-i", best[1], "-af", "loudnorm=I=-19:TP=-1.5", "-ar", "48000", "-ac", "1", dest, check=True)
    for f in os.listdir(pj.p("audio")):
        if f.startswith(f"{sid}_full") or f.startswith(f"{sid}_take"):
            os.remove(pj.p("audio", f))
    return True, best[2], best[3], f"音高 {best[4]:.0f}Hz"


def line_lead(sc, s):
    """这句台词的「前文」：分镜表 dialogue.lead 优先（同一角色、同一情绪、在这句之前会说的话，只用来带出语气，不进成片）；
    没写就用同一场戏里这个角色前面两句台词。"""
    dl = s.get("dialogue") or {}
    if dl.get("lead"):
        return dl["lead"]
    # 只用同一个角色自己说过的话（10-18 第2集：拿别的角色的台词当前文，语气和声音都带偏了）
    prev = [x["dialogue"]["text"] for x in sc["shots"][:sc["shots"].index(s)]
            if x.get("dialogue") and x.get("scene") == s.get("scene") and x["dialogue"].get("speaker") == dl.get("speaker")]
    return "".join(prev[-2:])


def cmd_voices(pj, args):
    """选角试音：每个说话角色定一个声音，出一段试音（念他自己的两句台词），等用户确认。"""
    import vf_voice
    pj.require("cast", "approved")
    sc, brief = pj.load("script/script.json"), pj.load("brief.json")
    set_asr_names(sc)
    speakers = []
    for s in sc["shots"]:
        dl = s.get("dialogue")
        if dl and dl["speaker"] not in speakers:
            speakers.append(dl["speaker"])
    need = [c for c in speakers if not next((x for x in sc["characters"] if x["name"] == c), {}).get("voice_desc")] or \
           [s["id"] for s in sc["shots"] if s.get("dialogue") and not s["dialogue"].get("direction")]
    if need:
        fill = chat([{"role": "user", "content": FILL.format(script=json.dumps(sc, ensure_ascii=False))}], json_out=True)
        for c in sc["characters"]:
            c.setdefault("voice_desc", (fill.get("voices") or {}).get(c["name"], ""))
        for s in sc["shots"]:
            if s.get("dialogue"):
                s["dialogue"].setdefault("direction", (fill.get("directions") or {}).get(s["id"], "自然、克制地说"))
        pj.save("script/script.json", sc)
    os.makedirs(pj.p("cast"), exist_ok=True)
    only = set((args.get("only") or "").replace("，", ",").split(",")) - {""}     # --only 角色1,角色2：只重录这几个，其余沿用上次结果
    prev = pj.state().get("voices", {}).get("report", {}) or {}
    report, fails = (dict(prev.get("detail") or {}), [f for f in (prev.get("fails") or []) if f not in only]) if only else ({}, [])
    for c in speakers:
        if only and c not in only:
            continue
        mine = [s for s in sc["shots"] if (s.get("dialogue") or {}).get("speaker") == c]
        text = "".join(tts_text(s["dialogue"]["text"]) for s in mine[:2])
        desc = next(x for x in sc["characters"] if x["name"] == c).get("voice_desc", "")
        spec = (brief.get("voices") or {}).get(c, "")
        ref, how = voice_ref(pj, c, spec, desc, text)
        ref_f0 = f0_median(ref) if ref else 0.0
        json.dump({"ref_f0": ref_f0, "how": how}, open(pj.p("cast", f"voice_{c}_meta.json"), "w"), ensure_ascii=False)
        os.makedirs(pj.p("audio"), exist_ok=True)
        parts, heard_all, sims = [], [], []
        for s in mine[:3]:          # 试音 = 这个角色在片里的前几句，按成片的录法录
            tmp = pj.p("cast", f"voice_{c}_{s['id']}_试.wav")
            ok, heard, sim, note = say_line(pj, sc, s["id"] + "_试", c, s["dialogue"]["text"], line_lead(sc, s), tmp, ref_f0)
            if ok:
                parts.append(tmp); heard_all.append(heard); sims.append(sim)
        aud = pj.p("cast", f"voice_{c}_试音.mp3")
        if parts:
            open(pj.p("cast", "_l.txt"), "w").write("".join(f"file '{x}'\n" for x in parts))
            sh("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", pj.p("cast", "_l.txt"), "-af", "apad=pad_dur=0.5",
               "-c:a", "libmp3lame", "-b:a", "192k", aud, check=True)
        sim = min(sims) if sims else 0.0; heard = " / ".join(heard_all)
        report[c] = {"how": how, "desc": desc, "audition": aud, "heard": heard, "match": round(sim, 2), "ref_f0": round(ref_f0)}
        pj.log(f"选角 {c}：{how}，试音 {len(parts)}/{min(3, len(mine))} 句合格，最低读音相似 {sim:.0%}（{heard}）")
        if len(parts) < min(3, len(mine)) or sim < 0.85:
            fails.append(c)
    pj.set("voices", "awaiting_approval" if not fails else "failed", {"detail": report, "fails": fails})
    print(("✅ 试音做好了，请用户听后确认（vf approve <项目> voices）：" if not fails else "❌ 这些角色的试音念错了：" + "、".join(fails) + "\n") +
          "\n".join(f"- {c}：{r['how']}；{r['desc']}\n  试音 {r['audition']}" for c, r in report.items()))


def cmd_voice(pj, args):
    """录对白：用确认过的声音逐句录，带表演指导；念错自动重录（交替加停顿），听写按读音核对。不依赖视频，先于生成画面。"""
    import vf_voice
    pj.require("voices", "approved")
    sc = pj.load("script/script.json")
    set_asr_names(sc)
    os.makedirs(pj.p("audio"), exist_ok=True)
    old_lines = pj.load("audio/lines.json") if os.path.exists(pj.p("audio/lines.json")) else {}
    lines, fails = {}, []
    for s in sc["shots"]:
        dl = s.get("dialogue")
        if not dl:
            continue
        sid, c = s["id"], dl["speaker"]
        ref = pj.p("cast", f"voice_{c}.json") if os.path.exists(pj.p("cast", f"voice_{c}.json")) else pj.p("cast", f"voice_{c}.mp3")
        prev = old_lines.get(sid)
        if prev and prev.get("text") == dl["text"] and prev.get("ref_mtime") == os.path.getmtime(ref) and prev.get("match", 0) >= 0.7:
            lines[sid] = dict(prev, offscreen=bool(dl.get("offscreen"))); continue      # 沿用旧录音时，画外音标记以分镜表为准（10-18 s22 改了标记没生效）
        clean = pj.p("audio", f"{sid}_clean.wav")
        meta = pj.p("cast", f"voice_{c}_meta.json")
        ref_f0 = json.load(open(meta)).get("ref_f0", 0.0) if os.path.exists(meta) else 0.0
        ok_tts, heard, sim, note = say_line(pj, sc, sid, c, tts_text(dl["text"]), line_lead(sc, s), clean, ref_f0, direction=dl.get("direction", ""))
        if not ok_tts:
            fails.append(f"{sid} 录了 5 次都不合格（最后听写「{heard}」）"); continue
        lines[sid] = {"file": os.path.basename(clean), "dur": round(probe(clean)["dur"], 3), "speaker": c, "text": dl["text"],
                      "offscreen": bool(dl.get("offscreen")), "asr": heard, "match": round(sim, 2), "ref_mtime": os.path.getmtime(ref)}
        pj.log(f"对白 {sid}：{c} {lines[sid]['dur']:.1f}s 听写相似 {sim:.0%}")
        need = lines[sid]["dur"] + 1.2
        if float(s["duration"]) < need:          # 镜头比台词短：把镜头拉长到能说完
            pj.log(f"{sid} 镜头 {s['duration']}s 放不下 {lines[sid]['dur']:.1f}s 的台词，改为 {min(15, round(need + 0.4))}s")
            s["duration"] = min(15, round(need + 0.4))
    pj.save("script/script.json", sc)
    pj.save("audio/lines.json", lines)
    pj.set("voice", "passed" if not fails else "failed", {"fails": fails})
    print("✅ 对白都已录好并核对" if not fails else "❌ 对白问题：\n- " + "\n- ".join(fails))


# ---------- 对口型 ----------
MOUTH = ('图中「{c}」的嘴巴和下巴在哪里？给出归一化坐标（0~1，左上角为原点）的边界框，框住嘴和下巴、略放大一圈。'
         '只输出 JSON：{{"x0":0.4,"y0":0.4,"x1":0.6,"y1":0.55}}')


def mouth_activity(pj, s, clip, c):
    """看画面判断嘴什么时候在动：嘴部区域逐帧差分（扣掉整张脸的晃动），返回 [(起, 止)]。
    镜头会推拉、角色会走动，嘴的位置会变（10-18 s21 教训：只在 40% 处定一次位置，推镜开头那句完全没测到），
    所以把片段分前/中/后三段，每段在自己中间那一帧重新找嘴，只采信这一段时间里的结果。"""
    import numpy as np
    d = probe(clip)["dur"]
    pr = probe(clip); W0, H0 = pr["w"], pr["h"]
    found = []
    for k, frac in enumerate((1 / 6, 1 / 2, 5 / 6)):
        f = frame_at(clip, d * frac, clip + f".mouth{k}.jpg")
        try:
            b = vision(MOUTH.format(c=c), [f])
            x0, y0, x1, y1 = [float(b[q]) for q in ("x0", "y0", "x1", "y1")]
        except Exception:
            continue
        cw, ch = max(16, int((x1 - x0) * W0)), max(16, int((y1 - y0) * H0)); cx, cy = int(x0 * W0), int(y0 * H0)
        fy0 = max(0, cy - 2 * ch)                   # 嘴上方同宽的一块（眼鼻）当「整体晃动」参照
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", clip, "-vf", f"fps={FPS},crop={cw}:{ch * 3}:{cx}:{fy0},scale=48:72,format=gray",
                              "-f", "rawvideo", "-"], capture_output=True).stdout
        fr = np.frombuffer(raw, np.uint8).reshape(-1, 72, 48).astype(np.float32)
        if len(fr) < 4:
            continue
        m0 = int(72 * (cy - fy0) / (ch * 3))
        dm = np.abs(np.diff(fr[:, m0:, :], axis=0)).mean(axis=(1, 2))
        du = np.abs(np.diff(fr[:, :max(1, m0), :], axis=0)).mean(axis=(1, 2))
        act = np.clip(dm - 0.8 * du, 0, None)
        act = np.convolve(act, np.ones(5) / 5, mode="same")
        thr = np.median(act) + 0.6 * (np.percentile(act, 90) - np.median(act))
        on = act > max(thr, 0.3)
        lo, hi = d * (frac - 1 / 6), d * (frac + 1 / 6)
        st = None
        for i, v in enumerate(on):
            if v and st is None:
                st = i
            if (not v or i == len(on) - 1) and st is not None:
                a, b2 = max(st / FPS, lo), min((i + 1) / FPS, hi)
                if b2 > a:
                    found.append([a, b2])
                st = None
    if not found:
        return None
    found.sort(); merged = []
    for a, b2 in found:
        if merged and a - merged[-1][1] < 0.35:
            merged[-1][1] = max(merged[-1][1], b2)
        else:
            merged.append([a, b2])
    return [m for m in merged if m[1] - m[0] >= 0.3]


def cmd_sync(pj, args):
    """把录好的台词放到画面里嘴在动的那段；对不上的镜头列出来重做。画外音按镜头开头放。"""
    pj.require("clips")
    sc = pj.load("script/script.json")
    picks, lines = pj.load("clips/picks.json"), pj.load("audio/lines.json")
    place, fails, refit = {}, [], []
    cpath = pj.p("audio", "mouth_cache.json")
    cache = json.load(open(cpath)) if os.path.exists(cpath) else {}
    for s in sc["shots"]:
        sid = s["id"]; ln = lines.get(sid)
        if not ln:
            continue
        clip = pj.p("clips", picks[sid]["file"]); src = pj.p("audio", ln["file"])
        if ln.get("offscreen"):
            place[sid] = {"file": ln["file"], "start": 0.5, "dur": ln["dur"], "text": ln["text"]}; continue
        at = (s.get("dialogue") or {}).get("place_at")     # 导演看过画面后手动指定台词从第几秒开始（比如说话人中途转过身去）
        if at is not None:
            place[sid] = {"file": ln["file"], "start": float(at), "dur": ln["dur"], "text": ln["text"], "manual": True}
            pj.log(f"对口型 {sid}：按分镜表手动指定从 {float(at):.2f}s 开始"); continue
        # 测嘴动靠模型框嘴的位置，每次框得不一样结果就会变（10-18：没动过的 s05/s06 这轮过、下轮不过）：
        # 同一片段的结果存下来不重测；判对不上之前再测两次取最长的那次
        key = f"{os.path.basename(clip)}:{os.path.getmtime(clip):.0f}"
        m = re.search(r"line_dur=([0-9.]+)", picks[sid].get("why", ""))
        if m and abs(float(m.group(1)) - ln["dur"]) > 0.3 and ln.get("fit_for") != key:     # 台词重录后时长变了，画面里的嘴还是按旧台词动的（10-18 s01/s13）
            fails.append(f"{sid}：台词重录过（{float(m.group(1)):.1f}s→{ln['dur']:.1f}s），画面按旧台词生成——先试 vf refit <项目> --shots {sid}（按画面嘴动长度重录），不行再重做这一镜（vf clips <项目> --shots {sid}）")
            refit.append(sid); continue
        segs = cache.get(key)
        if segs is None:
            segs = mouth_activity(pj, s, clip, ln["speaker"]) or []
        def span(x):
            return (x[-1][1] - x[0][0]) if x else 0.0
        if span(segs) + 0.6 < ln["dur"] / 1.12:
            for _ in range(2):
                more = mouth_activity(pj, s, clip, ln["speaker"]) or []
                if span(more) > span(segs):
                    segs = more
        cache[key] = segs
        if not segs:
            fails.append(f"{sid}：画面里没看到{ln['speaker']}张嘴说话"); continue
        a, b = segs[0][0], segs[-1][1]               # 第一次开口到最后一次闭口
        wlen = b - a; ratio = ln["dur"] / max(wlen, 0.3)
        tempo = max(0.95, min(1.12, ratio))      # 放慢最多 5%（10-18：放慢 12% 听着拖沓，嘴也比声音多动）
        placed = pj.p("audio", f"{sid}_placed.wav")
        sh("ffmpeg", "-v", "error", "-y", "-i", src, "-af", f"atempo={tempo:.4f}", placed, check=True)
        pdur = probe(placed)["dur"]
        pj.log(f"对口型 {sid}：嘴动 {a:.2f}~{b:.2f}s（{wlen:.1f}s），台词 {ln['dur']:.1f}s → 变速 {tempo:.2f}")
        if pdur > wlen + 0.6 or pdur < wlen - 0.8:     # 嘴比声音多动 0.8 秒以上就算对不上（10-18 用户指出「嘴在动却没声音」）
            fails.append(f"{sid}：嘴动 {wlen:.1f}s，台词 {pdur:.1f}s，对不上——先试 vf refit <项目> --shots {sid}（重录到 {wlen * 0.97:.1f}~{wlen * 1.08:.1f}s），不行再重做这一镜（vf clips <项目> --shots {sid}）")
            refit.append(sid)
        place[sid] = {"file": os.path.basename(placed), "start": round(a, 3), "dur": round(pdur, 3), "text": ln["text"],
                      "mouth": [round(a, 2), round(b, 2)]}
    pj.save("audio/placement.json", place)
    json.dump(cache, open(cpath, "w"), ensure_ascii=False)
    pj.set("sync", "passed" if not fails else "failed", {"fails": fails, "refit": refit})
    print("✅ 台词都放到了嘴在动的位置" if not fails else "❌ 口型对不上：\n- " + "\n- ".join(fails)
          + (f"\n建议先按画面长度重录这几句（不动画面，最便宜）：vf refit {pj.name} --shots {','.join(refit)}，然后重跑 vf sync" if refit else ""))


def mouth_span(pj, s, clip, speaker):
    """画面里这个角色第一次开口到最后一次闭口（秒），用对口型同一份存档。"""
    cpath = pj.p("audio", "mouth_cache.json")
    cache = json.load(open(cpath)) if os.path.exists(cpath) else {}
    key = f"{os.path.basename(clip)}:{os.path.getmtime(clip):.0f}"
    segs = cache.get(key)
    if segs is None:
        segs = mouth_activity(pj, s, clip, speaker) or []
        cache[key] = segs; json.dump(cache, open(cpath, "w"), ensure_ascii=False)
    return key, segs


def cmd_refit(pj, args):
    """按画面长度重录台词（10-18 第一集 s01/s02/s13/s21 的做法）：台词改过、或口型对不上时，先不动画面，
    把这句重录到画面里嘴动的长度——每句最多 10 遍，挑读音合格、长度落在区间里的；10 遍都不在区间，挑最接近的，
    仍差太多就提示重做画面。区间默认按嘴动长度的 0.97~1.08 倍（对口型最多放慢 5%、加快 12%），也可 --range 2.5-2.9 手动指定。
    录完把对口型、合成、成片检查标成要重跑。"""
    sc = pj.load("script/script.json"); set_asr_names(sc)
    picks, lines = pj.load("clips/picks.json"), pj.load("audio/lines.json")
    only = args.get("shots") or (pj.state().get("sync", {}).get("report") or {}).get("refit") or []
    if not only:
        raise SystemExit("要写重录哪几镜：vf refit <项目> --shots s01,s02（对口型没过时会列出建议的镜号）")
    rng = args.get("range")
    done, bad, changed = [], [], []
    for sid in only:
        s = next((x for x in sc["shots"] if x["id"] == sid), None); ln = lines.get(sid)
        if not s or not s.get("dialogue") or not ln:
            bad.append(f"{sid}：这一镜没有台词"); continue
        d = s["dialogue"]; clip = pj.p("clips", picks[sid]["file"])
        key, segs = mouth_span(pj, s, clip, d["speaker"])
        if isinstance(rng, str):
            lo, hi = [float(x) for x in rng.split("-")]
        elif d.get("offscreen") or d.get("place_at") is not None or not segs:
            bad.append(f"{sid}：{'画外音/手动起点' if segs else '画面里没测到嘴动'}，没有画面长度可对，要用 --range 指定"); continue
        else:
            wlen = segs[-1][1] - segs[0][0]; lo, hi = wlen * 0.97, wlen * 1.08
        meta = pj.p("cast", f"voice_{d['speaker']}_meta.json")
        ref_f0 = json.load(open(meta)).get("ref_f0", 0.0) if os.path.exists(meta) else 0.0
        best = None
        for k in range(10):
            f = pj.p("audio", f"{sid}_fit{k}.wav")
            ok, heard, sim, note = say_line(pj, sc, f"{sid}_fit{k}", d["speaker"], tts_text(d["text"]), line_lead(sc, s), f, ref_f0, tries=2, direction=d.get("direction", ""))
            if not ok:
                continue
            dur = probe(f)["dur"]; gap = 0 if lo <= dur <= hi else min(abs(dur - lo), abs(dur - hi))
            pj.log(f"重录 {sid} 第 {k + 1} 遍：{dur:.2f}s（要 {lo:.2f}~{hi:.2f}s）听写「{heard}」{'，长度合适' if gap == 0 else ''}")
            if best is None or gap < best[0]:
                best = (gap, f, dur, heard, sim)
            if gap == 0:
                break
        if not best:
            bad.append(f"{sid}：10 遍都没录出读音合格的"); continue
        shutil.copy(best[1], pj.p("audio", f"{sid}_clean.wav"))
        lines[sid].update(file=f"{sid}_clean.wav", dur=round(best[2], 3), asr=best[3], match=round(best[4], 2), fit_for=key, fit_range=[round(lo, 2), round(hi, 2)])
        pj.save("audio/lines.json", lines); changed.append(sid)
        (done if best[0] <= 0.3 else bad).append(f"{sid}：{best[2]:.2f}s（要 {lo:.2f}~{hi:.2f}s）" + ("" if best[0] <= 0.3 else "，重录也对不上，要重做画面（vf clips <项目> --shots " + sid + "）"))
    for f in os.listdir(pj.p("audio")):
        if "_fit" in f:
            os.remove(pj.p("audio", f))
    for st in ("sync", "mix", "qc"):           # 台词变了，后面的都要重跑
        if changed and st in pj.state():
            pj.set(st, "failed", {"fails": [f"重录了 {'、'.join(changed)} 的台词，要重跑"]})
    print(("✅ 重录好了：\n- " + "\n- ".join(done) if done else "") + ("\n❌ 没解决：\n- " + "\n- ".join(bad) if bad else "")
          + (f"\n下一步：vf bg {pj.name} auto（重跑对口型→合成→成片检查），交付前 vf review {pj.name} --shots {','.join(changed)} 逐帧核对" if changed else ""))


# ---------- 合成 ----------
def watermark_png(src, dest, width):
    from PIL import Image
    im = Image.open(src).convert("RGBA"); bb = im.getchannel("A").getbbox()
    if bb:
        im = im.crop(bb)
    tw = int(width * 0.30); th = int(im.height * tw / im.width)
    im.resize((tw, th), Image.LANCZOS).save(dest)
    return dest


def ass_time(t):
    h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def sub_wrap(t, n=13):
    """字幕超过 n 个字就在最靠中间的标点处折成两行（10-18：长台词一行排不下，两头被截掉）。"""
    if len(t) <= n:
        return t
    cuts = [i + 1 for i, ch in enumerate(t[:-1]) if ch in "，。？！、；…"]
    k = min(cuts, key=lambda i: abs(i - len(t) / 2)) if cuts else len(t) // 2
    return t[:k] + "\\N" + t[k:]


def cmd_mix(pj, args):
    pj.require("sync")
    sc, brief = pj.load("script/script.json"), pj.load("brief.json")
    picks, lines = pj.load("clips/picks.json"), pj.load("audio/placement.json")
    os.makedirs(pj.p("final"), exist_ok=True)
    shots, offs, t = sc["shots"], {}, 0.0
    for s in shots:
        offs[s["id"]] = t; t += float(s["duration"])
    total = t
    # 1) 画面：统一 720×1280、24fps 后拼接
    vlist = pj.p("final", "video_list.txt")
    with open(vlist, "w") as f:
        for s in shots:
            f.write(f"file '{pj.p('clips', picks[s['id']]['file'])}'\n")
    video = pj.p("final", "video_only.mp4")
    sh("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", vlist,
       "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},format=yuv420p",
       "-an", "-c:v", "libx264", "-crf", "16", "-preset", "medium", video, check=True)
    # 2) 声音设计（10-18 教训：直接用视频模型自带的原声，会混进和画面无关的关门声、杂音）：
    #    环境底噪按场景程序合成（雨夜/街道/室内），全场统一、换场淡入淡出；
    #    事件声只在分镜表 sfx 标了的镜头出现：clip=用这一镜片段自带的声音（画面就是这个事件，声画同步）、siren=合成警笛（可跨镜头，按 gain 由远到近）。
    def scene_amb(s):
        scn = next((x for x in sc.get("scenes") or [] if x["name"] == s.get("scene")), {})
        t_ = scn.get("ambience") or ("rain" if re.search(r"rain|雨", scn.get("look", ""), re.I) else "street" if re.search(r"street|街", scn.get("look", ""), re.I) else "room")
        return t_
    BED = {"rain": "anoisesrc=color=pink:amplitude=0.22:sample_rate=48000,highpass=f=350,lowpass=f=9000,volume=0.9[a];"
                   "anoisesrc=color=brown:amplitude=0.3:sample_rate=48000,lowpass=f=180,volume=0.35[b];[a][b]amix=inputs=2:normalize=0",
           "street": "anoisesrc=color=brown:amplitude=0.3:sample_rate=48000,lowpass=f=260,volume=0.4",
           "room": "anoisesrc=color=brown:amplitude=0.2:sample_rate=48000,lowpass=f=320,volume=0.9",   # 10-18：原来太低，停顿处像声音断了
           "none": "anullsrc=r=48000:cl=mono"}
    parts = []
    for s in shots:
        sid, d = s["id"], float(s["duration"])
        seg = pj.p("final", f"amb_{sid}.wav")
        sh("ffmpeg", "-v", "error", "-y", "-filter_complex", BED[scene_amb(s)] + f",atrim=0:{d + 0.3:.3f},aformat=channel_layouts=stereo[o]",
           "-map", "[o]", "-ar", "48000", seg, check=True)
        parts.append(seg)
    amb = pj.p("final", "ambience.wav")
    xin, xf = [], []
    for p_ in parts:
        xin += ["-i", p_]
    cur = "[0:a]"
    for k2 in range(1, len(parts)):
        same = scene_amb(shots[k2]) == scene_amb(shots[k2 - 1])
        xf.append(f"{cur}[{k2}:a]acrossfade=d={0.3 if same else 0.8}:c1=tri:c2=tri[x{k2}]"); cur = f"[x{k2}]"
    if xf:
        sh("ffmpeg", "-v", "error", "-y", *xin, "-filter_complex", ";".join(xf) + f";{cur}afade=t=in:d=0.8,apad[o]", "-map", "[o]",
           "-t", f"{total:.3f}", amb, check=True)
    else:
        sh("ffmpeg", "-v", "error", "-y", "-i", parts[0], "-af", "afade=t=in:d=0.8", amb, check=True)
    sfx_ins, sfx_fl, sfx_tags = [], [], []
    for s in shots:
        for fx in s.get("sfx") or []:
            st = offs[s["id"]] + float(fx.get("at", 0)); dur = float(fx.get("dur", s["duration"])); g = float(fx.get("gain", 0))
            src = pj.p("final", f"sfx_{s['id']}_{fx['type']}.wav")
            if fx["type"] == "clip":
                sh("ffmpeg", "-v", "error", "-y", "-i", pj.p("clips", picks[s["id"]]["file"]), "-vn", "-ss", f"{fx.get('at', 0)}", "-t", f"{dur:.3f}",
                   "-af", f"volume={g}dB,afade=t=out:st={max(0, dur - 0.3):.2f}:d=0.3", "-ar", "48000", "-ac", "2", src, check=True)
            elif fx["type"] == "siren":
                g2 = float(fx.get("gain_end", g))
                sh("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                   f"aevalsrc='0.5*sin(2*PI*900*t-250/0.45*cos(2*PI*0.45*t))':s=48000:d={dur:.3f}", "-af",
                   f"lowpass=f=2500,aecho=0.8:0.6:120:0.35,volume='{10 ** (g / 20):.4f}+({10 ** (g2 / 20):.4f}-{10 ** (g / 20):.4f})*t/{dur:.3f}':eval=frame,"
                   f"afade=t=in:d=1.0,afade=t=out:st={max(0, dur - 1.0):.2f}:d=1.0", "-ac", "2", src, check=True)
            else:
                continue
            ms = int(st * 1000)
            sfx_ins += ["-i", src]; sfx_tags.append(ms)
    # 3) 对白、配乐混音
    ins, fl, mixin = ["-i", amb], [], ["[0:a]"]
    k = 1
    for n_, ms in enumerate(sfx_tags):
        ins += sfx_ins[2 * n_:2 * n_ + 2]
        fl.append(f"[{k}:a]adelay={ms}|{ms},aformat=channel_layouts=stereo[f{k}]"); mixin.append(f"[f{k}]"); k += 1
    for sid, ln in lines.items():
        ins += ["-i", pj.p("audio", ln["file"])]
        ms = int((offs[sid] + ln["start"]) * 1000)
        fl.append(f"[{k}:a]volume=1.6,adelay={ms}|{ms},aformat=channel_layouts=stereo[d{k}]"); mixin.append(f"[d{k}]"); k += 1
    music = args.get("music") or brief.get("music")
    if music and os.path.exists(music):
        ins += ["-stream_loop", "-1", "-i", music]
        fl.append(f"[{k}:a]volume=0.25,atrim=0:{total:.3f},afade=t=out:st={max(0, total - 3):.2f}:d=3[m]"); mixin.append("[m]"); k += 1
    fl.append("".join(mixin) + f"amix=inputs={len(mixin)}:normalize=0:duration=first,loudnorm=I=-16:TP=-1.5:LRA=11[aout]")
    audio = pj.p("final", "audio.wav")
    sh("ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(fl), "-map", "[aout]", "-t", f"{total:.3f}", "-ar", "48000", audio, check=True)
    # 4) 字幕（只有台词）+ 水印
    ass = pj.p("final", "subs.ass")
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 720\nPlayResY: 1280\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            "Style: D,Noto Sans CJK SC,52,&H00FFFFFF,&H00000000,&H64000000,0,0,1,2.2,0.6,2,40,40,170\n"
            "Style: C,Noto Serif CJK SC,44,&H00F0F0F0,&H00000000,&H90000000,0,0,3,10,0,8,60,60,150\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Text\n")
    ev = "".join(f"Dialogue: 0,{ass_time(offs[sid] + ln['start'])},{ass_time(offs[sid] + ln['start'] + ln['dur'] + 0.15)},D,{sub_wrap(ln['text'])}\n"
                 for sid, ln in lines.items())
    # 画面字幕（时间地点、「十年后」）：放在画面上部，整镜显示，淡入淡出
    ev += "".join(f"Dialogue: 1,{ass_time(offs[s['id']] + 0.2)},{ass_time(offs[s['id']] + min(float(s['duration']) - 0.2, 3.5))},C,{{\\fad(300,300)}}{s['caption']}\n"
                  for s in shots if s.get("caption"))
    open(ass, "w", encoding="utf-8").write(head + ev)
    vf = f"subtitles={ass}"
    ins2 = ["-i", video, "-i", audio]
    wm = brief.get("watermark")
    if wm and os.path.exists(wm):
        wmp = watermark_png(wm, pj.p("final", "wm.png"), W)
        ins2 += ["-i", wmp]
        fc = f"[0:v]{vf}[s];[s][2:v]overlay=W-w-28:H-h-36[v]"
    else:
        fc = f"[0:v]{vf}[v]"
    out = pj.p("final", f"{os.path.basename(pj.dir)}.mp4")
    sh("ffmpeg", "-v", "error", "-y", *ins2, "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "17",
       "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out, check=True)
    pj.set("mix", "passed", {"file": out, "duration": total})
    print(f"✅ 合成完成：{out}（{total:.0f}s）\n下一步：vf qc {pj.name}")


# ---------- 成片检查 ----------
def cmd_qc(pj, args):
    pj.require("mix")
    sc, lines = pj.load("script/script.json"), pj.load("audio/placement.json")
    set_asr_names(pj.load("script/script.json"))   # 成片听写也提示人名（10-18：「桐生」被听成「红尚」误判）
    out = pj.state()["mix"]["report"]["file"]
    pr, errs, offs, t = probe(out), [], {}, 0.0
    for s in sc["shots"]:
        offs[s["id"]] = t; t += float(s["duration"])
    if (pr["w"], pr["h"]) != (W, H):
        errs.append(f"画幅 {pr['w']}×{pr['h']}，应为 {W}×{H}")
    if not pr["audio"]:
        errs.append("没有声音")
    if abs(pr["dur"] - t) > 0.1 * t:
        errs.append(f"片长 {pr['dur']:.1f}s，分镜合计 {t:.1f}s")
    loud = sh("ffmpeg", "-i", out, "-af", "ebur128", "-f", "null", "-").stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", loud)
    lufs = float(m[-1]) if m else -99
    if not -20 <= lufs <= -12:
        errs.append(f"整体响度 {lufs} LUFS，应在 -20~-12（手机外放听得清）")
    heard = {}
    for sid, ln in lines.items():
        a = offs[sid] + ln["start"]
        clip = pj.p("final", f"qc_{sid}.wav")
        sh("ffmpeg", "-v", "error", "-y", "-ss", f"{a:.2f}", "-t", f"{ln['dur'] + 0.3:.2f}", "-i", out, "-vn", "-ac", "1", "-ar", "16000", clip)
        h = asr(clip); sim = same_line(h, ln["text"])
        heard[sid] = {"heard": h, "match": round(sim, 2)}
        if sim < 0.5:
            errs.append(f"{sid} 成片里这句听写成「{h}」，台词是「{ln['text']}」")
    ident = {}
    for s in sc["shots"]:
        if not s.get("characters"):
            continue
        f = frame_at(out, offs[s["id"]] + float(s["duration"]) / 2, pj.p("final", f"qc_{s['id']}.jpg"))
        for c in s["characters"]:
            ok, r = identity_ok(c, ref_for(pj, c), f, final=True)
            ident[f"{s['id']}_{c}"] = r
            if not ok:
                errs.append(f"{s['id']} 成片里{c}和定妆照对不上：{r.get('problems', '')[:80]}")
    sha = hashlib.sha256(open(out, "rb").read()).hexdigest()
    pj.save("final/qc.json", {"errors": errs, "lufs": lufs, "dialogue": heard, "identity": ident, "sha256": sha, "file": out})
    pj.set("qc", "passed" if not errs else "failed", {"errors": errs, "sha256": sha})
    print(("✅ 成片检查通过，可以交给用户看：" if not errs else "❌ 成片没过检查：\n- " + "\n- ".join(errs) + "\n文件：") + out + f"\nsha256 {sha[:16]}")



def cmd_review(pj, args):
    """交付前的音画核对图（10-18 用户：新加和修改的镜头交付前要全面检查）：
    每个有台词的镜头按 0.25 秒截帧，有声音的帧标红「声」，存到 final/review/<镜号>.jpg；另出一张全片每秒一帧总览。
    导演逐张看：标「声」的帧嘴必须在动，嘴在动的帧必须标「声」，动作合乎常识。"""
    from PIL import Image, ImageDraw, ImageFont
    sc = pj.load("script/script.json"); P = pj.load("audio/placement.json")
    video = pj.p("final", f"{os.path.basename(pj.dir)}.mp4")
    out = pj.p("final", "review"); os.makedirs(out, exist_ok=True)
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 18)
    off, t = {}, 0.0
    for s in sc["shots"]:
        off[s["id"]] = t; t += float(s["duration"])
    only = set(args.get("shots") or [])
    files = []
    for s in sc["shots"]:
        sid = s["id"]
        if (only and sid not in only) or (not only and sid not in P):
            continue
        a0 = P.get(sid, {}).get("start", -1); a1 = a0 + P.get(sid, {}).get("dur", 0)
        ims = []
        for i in range(int(float(s["duration"]) / 0.25)):
            x = 0.1 + i * 0.25
            tmp = pj.p("final", "review", "_f.jpg")
            sh("ffmpeg", "-v", "error", "-y", "-ss", f"{off[sid] + x:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=160:-1", tmp, check=True)
            im = Image.open(tmp).convert("RGB"); on = a0 <= x <= a1
            ImageDraw.Draw(im).text((3, 2), f"{x:.2f}{' 声' if on else ''}", fill="red" if on else "yellow", font=font, stroke_width=2, stroke_fill="black")
            ims.append(im)
        if not ims:
            continue
        cols = 8; w, h = ims[0].size; rows = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (w * cols, h * rows + 30), "white")
        ImageDraw.Draw(sheet).text((6, h * rows + 4), f"{sid}  {(s.get('dialogue') or {}).get('text', '')}  {s['action'][:40]}", fill="black", font=font)
        for i, im in enumerate(ims):
            sheet.paste(im, ((i % cols) * w, (i // cols) * h))
        f = os.path.join(out, f"{sid}.jpg"); sheet.save(f, quality=82); files.append(f)
    pj.log(f"音画核对图 {len(files)} 张：{out}")
    print("\n".join(files))
