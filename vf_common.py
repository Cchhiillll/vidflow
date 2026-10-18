"""vidflow 公共部分：项目目录、状态、模型调用（CPA：Gemini / gpt-image-2.5 / grok-imagine-video-1.5）。"""
import base64, datetime as dt, io, json, os, re, subprocess, time, urllib.error, urllib.request

HOME = os.path.expanduser("~")
PROD = os.environ.get("VIDFLOW_PROD", f"{HOME}/production")
CPA = "https://cpa.modelverseapi.com/v1"
PROXY = os.environ.get("VIDFLOW_PROXY", "http://127.0.0.1:10819")
TEXT_MODEL = os.environ.get("VIDFLOW_TEXT_MODEL", "gemini-3.8-flash-high")
IMAGE_MODEL = os.environ.get("VIDFLOW_IMAGE_MODEL", "gpt-image-2.5")   # gpt 出图额度冷却时可临时换 grok-imagine-image-2.0（10-18 实测走 /images/edits 能照参考图画）
VIDEO_MODEL = "grok-imagine-video-1.5"
# 出图上游顺序写在 ~/.config/vidflow/image_providers.json（CPA → zzone → modelverse，额度用完自动换下一个）；
# 临时只用某一个：VIDFLOW_IMAGE_BASE=... VIDFLOW_IMAGE_MODEL=...（密钥放 ~/.config/vidflow/image_api.key）。
IMAGE_BASE = os.environ.get("VIDFLOW_IMAGE_BASE", CPA)
W, H = 720, 1280            # 成片规格：9:16 竖屏
STAGES = ["story", "screenplay", "script", "cast", "voices", "boards", "voice", "clips", "sync", "mix", "qc"]


def key():
    for l in open(f"{HOME}/.hermes/.env", encoding="utf-8"):
        if l.startswith("MODELVERSEAPI_API_KEY="):
            return l.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("缺少 CPA 密钥")


_OP = urllib.request.build_opener(urllib.request.ProxyHandler({"https": PROXY, "http": PROXY}))


def http(method, url, body=None, timeout=300, raw=False):
    h = {"Authorization": "Bearer " + key(), "User-Agent": "curl/7.88.1"}
    data = None
    if body is not None:
        h["Content-Type"] = "application/json"; data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with _OP.open(req, timeout=timeout) as r:
        b = r.read()
    return b if raw else json.loads(b)


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/7.88.1"})
    with _OP.open(req, timeout=300) as r:
        open(dest, "wb").write(r.read())


def retry(fn, tries=3, wait=8, what=""):
    last = None
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read().decode(errors='ignore')[:200]}"
            time.sleep(wait * (3 if e.code == 429 else 1) * (i + 1))
        except Exception as e:
            last = str(e)[:200]; time.sleep(wait * (i + 1))
    raise RuntimeError(f"{what} 连续失败：{last}")


def img_uri(path, side=1536, q=92):
    from PIL import Image
    im = Image.open(path).convert("RGB"); im.thumbnail((side, side))
    b = io.BytesIO(); im.save(b, "JPEG", quality=q)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def _stream(body, timeout):
    """流式接收：长文本生成超过 100 秒时，非流式请求会被 CPA 前面的 Cloudflare 以 524 掐断。"""
    body = dict(body, stream=True)
    h = {"Authorization": "Bearer " + key(), "User-Agent": "curl/7.88.1", "Content-Type": "application/json", "Accept": "text/event-stream"}
    req = urllib.request.Request(f"{CPA}/chat/completions", data=json.dumps(body).encode(), headers=h)
    out = []
    with _OP.open(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                d = json.loads(data)
                out.append((d.get("choices") or [{}])[0].get("delta", {}).get("content") or "")
            except ValueError:
                pass
    return "".join(out)


def chat(messages, model=None, json_out=False, timeout=300, stream=False):
    def go():
        body = {"model": model or TEXT_MODEL, "messages": messages, "temperature": 0.7}
        if stream:
            return _stream(body, max(timeout, 600))
        r = http("POST", f"{CPA}/chat/completions", body, timeout)
        return r["choices"][0]["message"]["content"] or ""
    txt = retry(go, what="文本模型")
    if not json_out:
        return txt
    m = re.search(r"\{.*\}|\[.*\]", txt, re.S)
    if not m:
        raise RuntimeError("模型没有返回 JSON：" + txt[:200])
    return json.loads(m.group(0))


def vision(prompt, images, json_out=True):
    content = [{"type": "text", "text": prompt}] + [{"type": "image_url", "image_url": {"url": img_uri(p, 768, 85)}} for p in images]
    return chat([{"role": "user", "content": content}], json_out=json_out)


def _multipart(fields, files):
    import uuid
    b = uuid.uuid4().hex; body = b""
    for k, v in fields.items():
        body += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, fn, data in files:
        body += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\nContent-Type: image/png\r\n\r\n".encode() + data + b"\r\n"
    return body + f"--{b}--\r\n".encode(), b


def _png(path, side=1536):
    from PIL import Image
    im = Image.open(path).convert("RGB"); im.thumbnail((side, side))
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


IMAGE_CHAIN = f"{HOME}/.config/vidflow/image_providers.json"   # 出图上游按顺序排（不含密钥，只写密钥文件路径）
_img_cool = {}                                                    # 额度用完的上游：名字 -> 冷却到的时间戳


def image_providers():
    """出图上游列表。设了 VIDFLOW_IMAGE_BASE/MODEL 就只用那一个（临时指定）；否则读 image_providers.json；都没有就只用 CPA。"""
    if os.environ.get("VIDFLOW_IMAGE_BASE") or os.environ.get("VIDFLOW_IMAGE_MODEL"):
        return [{"name": "指定", "base": IMAGE_BASE, "model": IMAGE_MODEL,
                 "keyfile": None if IMAGE_BASE == CPA else "~/.config/vidflow/image_api.key"}]
    if os.path.exists(IMAGE_CHAIN):
        return json.load(open(IMAGE_CHAIN, encoding="utf-8"))
    return [{"name": "cpa", "base": CPA, "model": "gpt-image-2.5", "keyfile": None}]


def _pkey(pv):
    return key() if not pv.get("keyfile") else open(os.path.expanduser(pv["keyfile"]), encoding="utf-8").read().strip()


def _quota_out(code, text):
    t = text.lower()
    return code in (401, 402, 403) or (code == 429 and any(k in t for k in ("usage_limit", "cooldown", "quota", "insufficient", "额度", "余额")))


def gen_image(prompt, refs, dest, size="1024x1536"):
    """有参考图时走 /images/edits（multipart + input_fidelity=high）——10-17 实测：/images/generations 的 images/image 字段、
    grok 的 image_urls 都会被静默忽略，画出来的是无关的人；只有 edits 真正照参考图画。
    上游按 image_providers.json 的顺序用：某个额度用完（或密钥无效）就冷却 30 分钟换下一个；超时/偶发错误在同一个上游重试 3 次再换。"""
    import sys

    def save(r):
        it = r["data"][0]
        if it.get("b64_json"):
            open(dest, "wb").write(base64.b64decode(it["b64_json"]))
        else:
            download(it["url"], dest)
        return dest

    def request(pv):
        h = {"Authorization": "Bearer " + _pkey(pv), "User-Agent": "curl/7.88.1"}
        if refs:
            body, b = _multipart({"model": pv["model"], "prompt": prompt, "size": size, "input_fidelity": "high", "n": "1"},
                                 [("image[]", f"ref{i}.png", _png(p)) for i, p in enumerate(refs[:5])])
            h["Content-Type"] = f"multipart/form-data; boundary={b}"
            req = urllib.request.Request(f"{pv['base']}/images/edits", data=body, headers=h)
        else:
            h["Content-Type"] = "application/json"
            req = urllib.request.Request(f"{pv['base']}/images/generations", method="POST", headers=h,
                                         data=json.dumps({"model": pv["model"], "prompt": prompt, "n": 1, "size": size}).encode())
        with _OP.open(req, timeout=400) as r:
            return save(json.loads(r.read()))

    errs = []
    for pv in image_providers():
        if _img_cool.get(pv["name"], 0) > time.time():
            continue
        for i in range(3):
            try:
                out = request(pv)
                print(f"[出图] {os.path.basename(dest)} ← {pv['name']}（{pv['model']}）", file=sys.stderr, flush=True)   # 每张图记下是哪家出的
                return out
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="ignore")[:300]
                errs.append(f"{pv['name']} HTTP {e.code} {text[:120]}")
                if _quota_out(e.code, text):
                    _img_cool[pv["name"]] = time.time() + 1800
                    print(f"[出图] {pv['name']}（{pv['model']}）额度用完或密钥无效，换下一个上游", file=sys.stderr, flush=True)
                    break
            except Exception as e:
                errs.append(f"{pv['name']} {str(e)[:120]}")
            time.sleep(8 * (i + 1))
    raise RuntimeError("照参考图生图 连续失败：" + " | ".join(errs[-3:]))


_last_submit = [0.0]


def gen_video(prompt, image, duration, dest, refs=None):
    """grok-imagine-video-1.5，720p 竖屏。CPA 限速每秒 2 个请求，这里每次提交间隔 ≥1.2 秒。"""
    body = {"model": VIDEO_MODEL, "prompt": prompt, "duration": int(max(1, min(15, round(duration)))),
            "resolution": "720p", "aspect_ratio": "9:16"}
    if image:
        body["image"] = {"url": img_uri(image, 1280, 95)}
    elif refs:
        body["reference_image_urls"] = [img_uri(p, 1024, 90) for p in refs[:7]]
    def submit():
        gap = time.time() - _last_submit[0]
        if gap < 1.2:
            time.sleep(1.2 - gap)
        _last_submit[0] = time.time()
        return http("POST", f"{CPA}/videos/generations", body, 120)["request_id"]
    rid = retry(submit, tries=5, wait=10, what="提交视频")
    for _ in range(240):
        time.sleep(6)
        try:
            p = http("GET", f"{CPA}/videos/{rid}", timeout=60)
        except Exception:
            continue
        st = p.get("status")
        if st in ("done", "completed"):
            download((p.get("video") or {}).get("url"), dest); return dest
        if st in ("failed", "error", "expired"):
            raise RuntimeError(f"视频生成失败：{json.dumps(p, ensure_ascii=False)[:200]}")
    raise RuntimeError("视频生成超时")


def sh(*a, check=False):
    r = subprocess.run(a, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{a[0]} 失败：{r.stderr[-300:]}")
    return r


def probe(path):
    out = sh("ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", path).stdout
    d = json.loads(out or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    return {"w": v.get("width", 0), "h": v.get("height", 0), "dur": float(d.get("format", {}).get("duration", 0) or 0),
            "audio": any(s.get("codec_type") == "audio" for s in d.get("streams", []))}


class Project:
    def __init__(self, name):
        self.name = name
        self.dir = name if os.path.isabs(name) else os.path.join(PROD, name)
        self.state_path = os.path.join(self.dir, "state.json")

    def p(self, *a):
        return os.path.join(self.dir, *a)

    def load(self, f):
        return json.load(open(self.p(f), encoding="utf-8"))

    def save(self, f, obj):
        os.makedirs(os.path.dirname(self.p(f)), exist_ok=True)
        json.dump(obj, open(self.p(f), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def state(self):
        return json.load(open(self.state_path)) if os.path.exists(self.state_path) else {}

    def set(self, stage, status, report=None):
        s = self.state()
        s[stage] = {"status": status, "at": dt.datetime.now().isoformat(timespec="seconds"), "report": report or {}}
        json.dump(s, open(self.state_path, "w"), ensure_ascii=False, indent=2)
        try:
            write_handoff(self)
        except Exception as e:
            print(f"（进度说明没写成：{e}）", flush=True)

    def require(self, stage, need="passed"):
        st = self.state().get(stage, {}).get("status")
        ok = st == "approved" if need == "approved" else st in ("passed", "approved")
        if not ok:
            raise SystemExit(f"关卡未过：上一步「{stage}」状态是 {st or '没跑'}，需要 {'你确认（vf approve）' if need == 'approved' else '检查通过'} 才能往下走。")

    def log(self, msg):
        line = f"{dt.datetime.now():%H:%M:%S} {msg}"
        print(line, flush=True)
        with open(self.p("vidflow.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")



STAGE_ZH = {"story": "故事方案", "screenplay": "剧本", "script": "分镜表", "cast": "角色/场景/道具设定卡", "voices": "角色音色（试音）",
            "boards": "分镜首帧", "voice": "录全部台词", "clips": "生成视频片段", "sync": "对口型", "mix": "合成成片", "qc": "成片检查"}
STATUS_ZH = {"approved": "已确认", "passed": "已通过检查", "awaiting_approval": "等确认", "failed": "没过", "running": "进行中"}
REVIEW_GATES = "音色、新角色卡/场景卡/道具卡、分镜首帧（按剧情顺序连起来看）、生成好的视频和配音、成片"


def write_handoff(pj):
    """每次状态变化都重写项目文件夹里的「进度说明.md」：新会话（Claude Code 或 Hermes）进来先读它，不用用户从头讲。
    手写的决定和备忘放在同目录 notes.md，会原样带进来。"""
    st = pj.state()
    b = pj.load("brief.json") if os.path.exists(pj.p("brief.json")) else {}
    sc = pj.load("script/script.json") if os.path.exists(pj.p("script/script.json")) else {}
    L = [f"# {b.get('title') or pj.name} · 进度说明", "",
         f"> 程序自动生成，最后更新 {dt.datetime.now():%m-%d %H:%M}。不要手改；要记的决定写进同目录 notes.md。", "",
         "## 这一集讲什么", b.get("episode") or b.get("story_core") or sc.get("logline") or "（未写）", ""]
    if sc.get("theme"):
        L += [f"想让观众感受到：{sc['theme']}", ""]
    if b.get("source"):
        L += ["## 原作依据", b["source"], ""]
    L += ["## 各步状态", "| 步骤 | 状态 | 时间 | 备注 |", "|---|---|---|---|"]
    nxt = None
    for k in STAGES:
        v = st.get(k)
        if not v:
            nxt = nxt or k; L.append(f"| {STAGE_ZH.get(k, k)} | 没做 | | |"); continue
        rep = v.get("report") or {}
        note = []
        if rep.get("override"):
            note.append("人工放行：" + str(rep["override"].get("reason", ""))[:80])
        if rep.get("fails"):
            note.append("问题：" + "、".join(map(str, rep["fails"]))[:80])
        if rep.get("note"):
            note.append(str(rep["note"])[:80])
        L.append(f"| {STAGE_ZH.get(k, k)} | {STATUS_ZH.get(v['status'], v['status'])} | {v.get('at', '')[5:16].replace('T', ' ')} | {'；'.join(note)} |")
        if v["status"] not in ("passed", "approved"):
            nxt = nxt or k
    L += ["", "## 下一步", (f"「{STAGE_ZH.get(nxt, nxt)}」：`vf {nxt} {pj.name}`（后台跑用 `vf bg {pj.name} {nxt}`）" if nxt else "全部完成"), ""]
    if sc.get("shots"):
        nd = sum(1 for s in sc["shots"] if s.get("dialogue"))
        L += ["## 分镜概况", f"{len(sc['shots'])} 镜，约 {sum(float(s.get('duration') or 0) for s in sc['shots']):.0f} 秒，{nd} 句台词；角色：{'、'.join(c['name'] for c in sc.get('characters', []))}；"
              f"场景：{'、'.join(x['name'] for x in sc.get('scenes', []))}；道具：{'、'.join(x['name'] for x in sc.get('props', []))}", ""]
    files = [("分镜表（可读版）", "script/script.md"), ("分镜表（程序用）", "script/script.json"), ("设定卡/音色", "cast/"), ("分镜首帧总览", "boards/storyboard.jpg"),
             ("台词录音", "audio/lines.json"), ("视频片段挑选", "clips/picks.json"), ("成片", f"final/{os.path.basename(pj.dir)}.mp4"), ("运行日志", "vidflow.log")]
    L += ["## 关键文件"] + [f"- {n}：`{pj.p(f)}`" for n, f in files if os.path.exists(pj.p(f))] + [""]
    L += ["## 规矩（换会话必读）", f"- 用户只做业务方：内核、剧本、台词由导演（agent）判断定稿，但这些产物必须先发用户过一遍，他点头才进下一环：{REVIEW_GATES}。",
          "- 检查标准只有 Claude Code 能改；Hermes 卡住先报用户，不许自己放宽标准。人工放行用 `vf approve <项目> <步骤> --override \"理由\"`。",
          "- 改动 vidflow 代码要在 ~/apps/vidflow 提交 git。", ""]
    if os.path.exists(pj.p("notes.md")):
        L += ["## 备忘（notes.md）", open(pj.p("notes.md"), encoding="utf-8").read().strip(), ""]
    open(pj.p("进度说明.md"), "w", encoding="utf-8").write("\n".join(L))
