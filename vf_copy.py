"""发布文案（10-18 《喵中之龙》第一集的做法写进程序）：围绕立意写账号简介 / 剧集介绍 / 本集发布文案，每样 3 版，导演挑。
  vf copy <项目> [--only 本集] [--notes 意见]     写 3 版 + 逐版自查，存 story/copy.md
  vf copy <项目> --pick 账号=2,剧集=1,本集=3      导演挑定，存 final/文案.txt（vf deliver 会一起发）
账号、剧集的立意和真实情况写在故事文件夹的「文案设定.json」（各集共用）：
  {"account": "这个号在做什么", "account_intent": "账号立意", "series": "这部剧是什么", "series_intent": "剧集立意",
   "facts": "真实情况（只在账号简介里讲）", "hidden": "暗线（不点破）", "final": {"账号": "定稿全文", "剧集": "定稿全文"}}
已经有定稿的（final 里有）默认不再重写，只写本集。
"""
import json, os
from vf_common import *

WRITER = os.environ.get("VIDFLOW_WRITER_MODEL", "gpt-6-astra")
PARTS = {"账号": "账号简介（抖音主页，3~4 行）", "剧集": "剧集介绍（合集简介，4~6 行）",
         "本集": "本集发布文案（前两行要让人停下来；结尾留一个让人想评论的问题；最后一行带 3~5 个话题，含 #剧名）"}

RULES = """好文案先有立意，每个字都为立意服务。规矩（每条都要做到）：
1. 不介绍剧情、不罗列信息、不复述画面；有画面、有余味，克制不煽情，短句有力量，像好电影的海报文案。
2. 前两行就要抓人：一个让人停下来的判断、反差或一句台词。
3. 结尾留一个问题或悬念，让人想评论。
4. 猫的真实情况只在账号简介里讲，剧集介绍、本集文案里不提。
5. 不能写出有歧义的句子：每句话只能有一种读法（尤其是谁是谁的猫、谁还在谁已经走了），读者第一遍就不会误会。
6. 暗线只能暗示，不能点破。"""

WRITE = """你是一位文字很好的作者，在抖音写文案。
""" + RULES + """

【账号】{account}
账号立意：{account_intent}
真实情况：{facts}

【剧】{series}
剧集立意：{series_intent}
暗线（不要点破）：{hidden}
{finals}
【本集】{title}
本集讲什么：{episode}
本集立意：{intent}
本集的关键台词（可以引用一句）：{lines}

修改意见：{notes}

请写：{asks}
每样 3 版。只输出 JSON：{{"账号":["第1版全文（换行用\\n）","第2版","第3版"],"剧集":[...],"本集":[...]}}（没让写的那样不要输出）"""

CHECK = """你是挑剔的文案编辑，第一次看到下面这些抖音文案。逐版检查规矩，只挑真问题。
""" + RULES + """
真实情况（用来查有没有写错、写出歧义）：{facts}
暗线：{hidden}
对每一版：用一句话说你读到了什么；有没有违反规矩（写明哪条、哪句）；有歧义的句子写出两种读法。
只输出 JSON：{{"账号":[{{"read":"读到了什么","problems":["问题"]}}],"剧集":[...],"本集":[...]}}（和输入的结构一一对应）
文案：{copy}"""


def story_dir(pj):
    d = os.path.dirname(os.path.realpath(pj.dir))
    return d if os.path.realpath(d) != os.path.realpath(PROD) else pj.dir


def settings(pj):
    f = os.path.join(story_dir(pj), "文案设定.json")
    return (json.load(open(f, encoding="utf-8")) if os.path.exists(f) else {}), f


def cmd_copy(pj, args):
    cfg, cfg_file = settings(pj)
    brief = pj.load("brief.json")
    if isinstance(args.get("pick"), str):
        return pick(pj, args["pick"])
    if not cfg.get("account_intent") or not cfg.get("series_intent") or not brief.get("intent"):
        raise SystemExit(f"写文案前要先有立意：账号立意、剧集立意写进 {cfg_file}（account_intent / series_intent），本集立意用 vf intent 定")
    finals = cfg.get("final") or {}
    want = [x for x in (args.get("only") or "").replace("，", ",").split(",") if x] or [k for k in PARTS if k not in finals]
    sc = pj.load("script/script.json") if os.path.exists(pj.p("script/script.json")) else {"shots": []}
    lines = [s["dialogue"]["text"] for s in sc["shots"] if s.get("dialogue")]
    fin = "".join(f"已定稿的{k}（保持口吻一致，不要重复它的句子）：\n{v}\n" for k, v in finals.items() if k not in want)
    r = chat([{"role": "user", "content": WRITE.format(
        account=cfg.get("account", ""), account_intent=cfg["account_intent"], facts=cfg.get("facts", "无"), series=cfg.get("series", ""),
        series_intent=cfg["series_intent"], hidden=cfg.get("hidden", "无"), finals=fin, title=brief.get("title", pj.name), episode=brief.get("episode", ""),
        intent=brief["intent"], lines=" / ".join(lines) or "无", notes=args.get("notes") or "无", asks="；".join(PARTS[k] for k in want))}],
        model=WRITER, json_out=True, stream=True)
    r = {k: r.get(k) or [] for k in want}
    try:
        ck = chat([{"role": "user", "content": CHECK.format(facts=cfg.get("facts", "无"), hidden=cfg.get("hidden", "无"), copy=json.dumps(r, ensure_ascii=False))}],
                  model=WRITER, json_out=True, stream=True)
    except Exception as e:
        ck = {}; pj.log(f"文案自查失败：{e}")
    pj.save("story/copy.json", {"drafts": r, "check": ck})
    L = [f"# {brief.get('title', pj.name)} · 发布文案", "", f"本集立意：{brief['intent']}", f"剧集立意：{cfg['series_intent']}", f"账号立意：{cfg['account_intent']}", ""]
    for k in want:
        L += [f"## {k}"]
        for i, t in enumerate(r[k], 1):
            c = ((ck.get(k) or [])[i - 1] if i <= len(ck.get(k) or []) else {}) or {}
            L += [f"### {k} {i}", t, "", f"> 编辑读到：{c.get('read', '')}"] + [f"> ⚠️ {p}" for p in c.get("problems") or []] + [""]
    open(pj.p("story/copy.md"), "w", encoding="utf-8").write("\n".join(L))
    pj.set("copy", "awaiting_approval", {"parts": want})
    print(f"✅ 文案 {len(want)} 样各 3 版（带编辑自查）：{pj.p('story/copy.md')}\n导演挑定：vf copy {pj.name} --pick " + ",".join(f"{k}=编号" for k in want))


def pick(pj, spec):
    cfg, cfg_file = settings(pj)
    d = pj.load("story/copy.json")["drafts"]
    chosen = {}
    for kv in spec.replace("，", ",").split(","):
        k, n = kv.split("=")
        chosen[k.strip()] = d[k.strip()][int(n) - 1]
    finals = dict(cfg.get("final") or {})
    for k in ("账号", "剧集"):             # 账号、剧集定稿后各集共用
        if k in chosen:
            finals[k] = chosen[k]
    cfg["final"] = finals
    json.dump(cfg, open(cfg_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    out = [f"【{k}】\n{finals[k]}" for k in ("账号", "剧集") if k in finals] + ([f"【本集发布文案】\n{chosen['本集']}"] if "本集" in chosen else [])
    os.makedirs(pj.p("final"), exist_ok=True)
    open(pj.p("final", "文案.txt"), "w", encoding="utf-8").write("\n\n".join(out) + "\n")
    pj.set("copy", "approved", {"pick": spec})
    print(f"✅ 文案定稿：{pj.p('final', '文案.txt')}（vf deliver 会和成片一起发）")
