"""vidflow 公共部分：项目目录、状态、模型调用（CPA：Gemini / gpt-image-2.5 / grok-imagine-video-1.5）。"""
import base64, datetime as dt, io, json, os, re, subprocess, time, urllib.error, urllib.request

HOME = os.path.expanduser("~")
PROD = os.environ.get("VIDFLOW_PROD", f"{HOME}/production")
CPA = "https://cpa.modelverseapi.com/v1"
PROXY = os.environ.get("VIDFLOW_PROXY", "http://127.0.0.1:10819")
TEXT_MODEL = os.environ.get("VIDFLOW_TEXT_MODEL", "gemini-3.8-flash-high")
IMAGE_MODEL = "gpt-image-2.5"
VIDEO_MODEL = "grok-imagine-video-1.5"
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


def gen_image(prompt, refs, dest, size="1024x1536"):
    """有参考图时走 /images/edits（multipart + input_fidelity=high）——10-17 实测：/images/generations 的 images/image 字段、
    grok 的 image_urls 都会被静默忽略，画出来的是无关的人；只有 edits 真正照参考图画。"""
    def save(r):
        it = r["data"][0]
        if it.get("b64_json"):
            open(dest, "wb").write(base64.b64decode(it["b64_json"]))
        else:
            download(it["url"], dest)
        return dest
    if refs:
        def go():
            body, b = _multipart({"model": IMAGE_MODEL, "prompt": prompt, "size": size, "input_fidelity": "high", "n": "1"},
                                 [("image[]", f"ref{i}.png", _png(p)) for i, p in enumerate(refs[:5])])
            req = urllib.request.Request(f"{CPA}/images/edits", data=body, headers={
                "Authorization": "Bearer " + key(), "User-Agent": "curl/7.88.1", "Content-Type": f"multipart/form-data; boundary={b}"})
            with _OP.open(req, timeout=400) as r:
                return save(json.loads(r.read()))
        return retry(go, what="照参考图生图")
    return retry(lambda: save(http("POST", f"{CPA}/images/generations", {"model": IMAGE_MODEL, "prompt": prompt, "n": 1, "size": size}, 300)), what="生图")


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
