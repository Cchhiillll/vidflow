#!/usr/bin/env python3
"""vidflow：出短片的固定流程（行业前期 → 制作 → 后期）。每一步跑完自动检查，不过关就停；标 ★ 的要用户确认才能往下走。

常用（Hermes 用这三条就够）：
  vf bg <项目> auto                       后台自动往下跑，遇到「要用户确认」或「没过检查」就停
  vf status <项目>                        每一步的状态、正在跑什么、下一步做什么、要给用户看的文件
  vf approve <项目> <步骤> [--pick N]     用户确认后放行（story 要带 --pick 选方案号）

全部步骤（按顺序）：
  vf new <项目> --brief brief.json        建项目（需求、故事内核、时长、角色原图、音色、水印）
  vf story <项目> [--notes 意见]           ★ 故事开发：3 个方案（梗概+节拍表+结尾落点）
  vf screenplay <项目> [--notes 意见]      ★ 剧本：场景、动作、台词、潜台词、表演指导
  vf script <项目> [--from 分镜表.json]    分镜表：按剧本拆镜头（节奏、连贯、反转前藏住、道具不自己动），自动检查+场记审查
  vf cast <项目> [--recheck]               ★ 角色设定卡（三视图+表情+服装配件+细节+色板，严格照原图）+ 场景图
  vf voices <项目> [--only 角色1,角色2]                         ★ 选角试音：固定音色 / 参考音克隆 / 按描述设计
  vf boards <项目> [--shots s03,s05 [--grids]] ★ 分镜图（9:16，照设定卡），角色/道具/不该露的检查 + 整组连续性提醒
  vf voice <项目>                          录对白：确认过的声音逐句录、带表演指导、听写核对（先录对白再拍画面）
  vf clips <项目> [--takes 2] [--shots] [--recheck]   视频片段：720p 竖屏、每镜多条挑最好；说话镜头按台词时长张嘴；露底/姿势/物理检查
  vf reframe <项目> --shot s01 --take t2 --top 0 --height 0.65   某镜露了不该露的：取一部分画面放大（剪辑常规做法）
  vf sync <项目>                           对口型：看画面里嘴动的时间放台词；对不上的镜头列出来重做
  vf mix <项目> [--music 文件]             合成：统一规格拼接、环境声交叉淡化、对白、配乐、字幕、水印
  vf qc <项目>                             ★ 成片检查（画幅/响度/每句台词听写/每镜角色比对）→ 过了把成片发给用户
  vf log <项目> [行数]                     看后台运行的日志
"""
import json, os, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vf_common import Project, STAGES

GATES = {"story": "story/options.md", "screenplay": "story/screenplay.md", "cast": "cast/（*_sheet.png、location.png）",
         "voices": "cast/（voice_*_试音.mp3）", "boards": "boards/storyboard.jpg", "qc": "final/<项目>.mp4"}


def parse(argv):
    pos, opt = [], {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            k = a[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                opt[k] = argv[i + 1]; i += 2
            else:
                opt[k] = True; i += 1
        else:
            pos.append(a); i += 1
    if isinstance(opt.get("shots"), str):
        opt["shots"] = opt["shots"].split(",")
    return pos, opt


def cmd_new(pj, opt):
    os.makedirs(pj.dir, exist_ok=True)
    brief = json.load(open(opt["brief"], encoding="utf-8"))
    os.makedirs(pj.p("refs"), exist_ok=True)
    refs = {}
    for name, paths in brief.get("refs", {}).items():
        paths = paths if isinstance(paths, list) else [paths]
        refs[name] = []
        for i, p in enumerate(paths):
            dst = pj.p("refs", f"{name}_{i}{os.path.splitext(p)[1] or '.jpg'}")
            shutil.copy(p, dst); refs[name].append(dst)
    brief["refs"] = refs
    pj.save("brief.json", brief)
    print(f"项目已建：{pj.dir}\n下一步：vf bg {pj.name} auto")


def todo(pj):
    """还要做的步骤：项目里没用过、而后面步骤已经做过的（比如老项目没有故事开发），算跳过。"""
    st = pj.state(); out = []
    for i, s in enumerate(STAGES):
        if st.get(s, {}).get("status") in ("passed", "approved"):
            continue
        if s in ("story", "screenplay") and s not in st and any(t in st for t in STAGES[i + 1:]):
            continue
        out.append(s)
    return out


def review_file(pj, stage):
    f = GATES.get(stage, "")
    return pj.p(f.replace("<项目>", pj.name)) if f and "（" not in f else (pj.p(f.split("/")[0]) + "/ " + f.split("/", 1)[1] if f else "")


def cmd_status(pj, opt):
    st = pj.state()
    for s in STAGES:
        x = st.get(s, {})
        print(f"{s:10s} {x.get('status', '—'):18s} {x.get('at', '')}")
    run = running(pj)
    if run:
        print(f"\n▶ 后台正在跑：{run['cmd']}（开始于 {run['at']}），日志 vf log {pj.name}")
    nxt = (todo(pj) or [None])[0]
    if not nxt:
        print("\n全部完成，成片：" + pj.p("final", f"{pj.name}.mp4")); return
    x = st.get(nxt, {})
    if x.get("status") == "awaiting_approval":
        print(f"\n下一步：把 {review_file(pj, nxt)} 发给用户看；用户确认后 vf approve {pj.name} {nxt}" + (" --pick 方案号" if nxt == "story" else ""))
    elif x.get("status") == "failed":
        print(f"\n下一步：「{nxt}」没过检查，问题：\n- " + "\n- ".join(map(str, (x.get("report") or {}).get("fails") or (x.get("report") or {}).get("errors") or ["见日志"]))
              + f"\n按问题改（改 script/script.json 或用 --shots 重做那几镜），再 vf bg {pj.name} auto")
    elif not run:
        print(f"\n下一步：vf bg {pj.name} auto")


def cmd_approve(pj, opt, stage):
    st = pj.state().get(stage, {})
    over = opt.get("override")      # 检查器误判时人工放行：必须写理由，理由记进状态，事后可查
    if st.get("status") != "awaiting_approval" and not (st.get("status") == "failed" and isinstance(over, str) and over.strip()):
        raise SystemExit(f"{stage} 现在是 {st.get('status')}，不是等确认状态"
                         + ("（检查没过但人工判定可用时：vf approve <项目> <步骤> --override \"理由\"）" if st.get("status") == "failed" else ""))
    rep = dict(st.get("report") or {})
    if st.get("status") == "failed":
        rep["override"] = {"reason": over.strip(), "at": time.strftime("%m-%d %H:%M"), "fails": rep.get("fails")}
    if stage == "story" and not opt.get("pick"):
        raise SystemExit("选故事方案要带 --pick 方案号")
    if opt.get("pick"):
        rep["pick"] = int(opt["pick"])
    pj.set(stage, "approved", rep)
    print(f"已确认 {stage}。下一步：vf bg {pj.name} auto")


def dispatch(cmd, pj, opt):
    if cmd in ("story", "screenplay"):
        import vf_story
        return getattr(vf_story, "cmd_" + cmd)(pj, opt)
    if cmd in ("script", "cast", "boards"):
        import vf_pre
        return getattr(vf_pre, "cmd_" + cmd)(pj, opt)
    if cmd in ("voices", "voice", "clips", "reframe", "sync", "mix", "qc"):
        import vf_post
        return getattr(vf_post, "cmd_" + cmd)(pj, opt)
    raise SystemExit(f"没有这个命令：{cmd}\n" + __doc__)


def cmd_auto(pj, opt):
    """按顺序把没完成的步骤跑掉；遇到要用户确认的、没过检查的就停。每步每次最多跑一遍。"""
    for s in todo(pj):
        stt = pj.state().get(s, {}).get("status")
        if stt == "awaiting_approval":
            print(f"⏸ 等用户确认「{s}」：把 {review_file(pj, s)} 发给用户；确认后 vf approve {pj.name} {s}" + (" --pick 方案号" if s == "story" else "")); return
        print(f"▶ {s}", flush=True)
        try:
            dispatch(s, pj, {})
        except SystemExit as e:
            print(str(e)); return
        stt = pj.state().get(s, {}).get("status")
        if stt == "failed":
            print(f"⏹ 「{s}」没过检查，先按问题修再继续（vf status {pj.name} 看问题）"); return
        if stt == "awaiting_approval":
            print(f"⏸ 等用户确认「{s}」：把 {review_file(pj, s)} 发给用户；确认后 vf approve {pj.name} {s}" + (" --pick 方案号" if s == "story" else "")); return
    print("✅ 全部完成：" + pj.p("final", f"{pj.name}.mp4"))


def running(pj):
    f = pj.p("logs", "running.json")
    if not os.path.exists(f):
        return None
    r = json.load(open(f))
    try:
        os.kill(r["pid"], 0); return r
    except OSError:
        return None


def cmd_bg(pj, opt, rest):
    """后台跑任意 vf 命令（生成视频一跑十几分钟，不要在前台等）。同一项目同时只允许一个后台任务。"""
    if running(pj):
        raise SystemExit(f"这个项目已经有后台任务在跑：{running(pj)['cmd']}；用 vf status / vf log 看进度")
    os.makedirs(pj.p("logs"), exist_ok=True)
    log = pj.p("logs", "run.log")
    args = [sys.executable, os.path.abspath(__file__)] + rest[:1] + [pj.name] + rest[1:]
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"\n===== {time.strftime('%m-%d %H:%M:%S')} vf {' '.join(rest)} =====\n")
        p = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    json.dump({"pid": p.pid, "cmd": "vf " + " ".join(rest), "at": time.strftime("%H:%M:%S")}, open(pj.p("logs", "running.json"), "w"), ensure_ascii=False)
    print(f"已在后台开始：vf {' '.join(rest)}（进度 vf status {pj.name}，日志 vf log {pj.name}）")


def cmd_log(pj, n):
    log = pj.p("logs", "run.log")
    if not os.path.exists(log):
        print("还没有后台日志"); return
    lines = [l for l in open(log, encoding="utf-8", errors="ignore").read().splitlines() if "Warning" not in l and "warn(" not in l]
    print("\n".join(lines[-n:]))


def main():
    pos, opt = parse(sys.argv[1:])
    if len(pos) < 2:
        print(__doc__); return
    cmd, pj = pos[0], Project(pos[1])
    if cmd == "new":
        return cmd_new(pj, opt)
    if not os.path.exists(pj.p("brief.json")):
        raise SystemExit(f"没有这个项目：{pj.dir}")
    if cmd == "status":
        return cmd_status(pj, opt)
    if cmd == "log":
        return cmd_log(pj, int(pos[2]) if len(pos) > 2 else 40)
    if cmd == "approve":
        if len(pos) < 3:
            raise SystemExit("要写确认哪一步：vf approve <项目> <story|screenplay|cast|voices|boards>")
        return cmd_approve(pj, opt, pos[2])
    if cmd == "bg":
        rest = sys.argv[sys.argv.index(pos[1]) + 1:]
        if not rest:
            raise SystemExit("vf bg <项目> <命令…>，比如 vf bg 赊账 auto")
        return cmd_bg(pj, opt, rest)
    if cmd == "auto":
        return cmd_auto(pj, opt)
    return dispatch(cmd, pj, opt)


if __name__ == "__main__":
    main()
