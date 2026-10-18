"""配音：选角（试音，等用户确认）+ 按语气生成台词。全部走 MiMo（api.xiaomimimo.com）：
- 音色设计 mimo-v2.5-tts-voicedesign：按文字描述造一个声音，先出一段试音；
- 克隆 mimo-v2.5-tts-voiceclone：之后每句都用「确认过的试音」当参考，保证全片同一个声音；
- 每句台词带表演指导（语气、情绪、语速），不是平读。"""
import base64, json, os, time, urllib.request

HOME = os.path.expanduser("~")
API = "https://api.xiaomimimo.com/v1/chat/completions"
_OP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _key():
    for l in open(f"{HOME}/.hermes/.env", encoding="utf-8"):
        if l.startswith("MIMO_API_KEY="):
            return l.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("缺少 MIMO_API_KEY")


def _call(model, messages, voice, dest, tries=4):
    fmt = "wav" if dest.endswith(".wav") else "mp3"   # 要 wav 就拿无损的（10-18：mp3 输出明显发闷，Hermes 用 wav 的克隆质量好得多）
    body = {"model": model, "messages": messages, "audio": {"format": fmt}}
    if voice:
        body["audio"]["voice"] = voice
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(API, json.dumps(body).encode(), {"Authorization": "Bearer " + _key(), "Content-Type": "application/json"})
            with _OP.open(req, timeout=90) as r:
                d = json.loads(r.read())
            open(dest, "wb").write(base64.b64decode(d["choices"][0]["message"]["audio"]["data"]))
            return dest
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.read().decode(errors='ignore')[:200]}"
        except Exception as e:
            last = str(e)[:200]
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"MiMo 配音失败：{last}")


def design(desc, text, dest):
    """按文字描述造声音，念 text 当试音。"""
    return _call("mimo-v2.5-tts-voicedesign", [{"role": "user", "content": desc}, {"role": "assistant", "content": text}], None, dest)


def clone(ref_audio, direction, text, dest):
    """用参考音的声音念 text；direction 是这句的表演指导。"""
    b64 = base64.b64encode(open(ref_audio, "rb").read()).decode()
    mime = "audio/wav" if ref_audio.endswith(".wav") else "audio/mpeg" if ref_audio.endswith(".mp3") else "audio/mp3"
    return _call("mimo-v2.5-tts-voiceclone", [{"role": "user", "content": direction}, {"role": "assistant", "content": text}],
                 f"data:{mime};base64,{b64}", dest)


def preset(name, direction, text, dest):
    """MiMo 自带固定音色（苏打、白桦、冰糖、茉莉、mimo_default…），direction 是表演指导。"""
    return _call("mimo-v2.5-tts", [{"role": "user", "content": direction}, {"role": "assistant", "content": text}], name, dest)
