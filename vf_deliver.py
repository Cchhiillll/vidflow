"""飞书交付：用 Hermes 的飞书机器人私信用户。
  vf deliver <项目> [--file 文件] [--note 附言] [--phone]
不写 --file 就发成片（要先过成片检查）；final/文案.txt 有就一起发。
≤30MB 直接私信文件；更大的走云空间分片上传 + 给用户开权限 + 私信链接；云空间不通（应用权限没发布等）
就压一版 ≤30MB 的手机版私信过去，并说明原画质没发出去的原因。--phone 只发手机版。
"""
import json, os, sys, urllib.error, urllib.request, zlib
from vf_common import *

LIMIT = 30 * 1024 * 1024
_OPF = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # 飞书走直连（不走本机代理）


class Feishu:
    def __init__(self):
        sys.path.insert(0, os.path.expanduser("~/.hermes/scripts"))
        from feishu_card import load_feishu_config, get_tenant_access_token
        app_id, app_secret, users = load_feishu_config()
        self.user = users.split(",")[0].strip()
        self.h = {"Authorization": "Bearer " + get_tenant_access_token(app_id, app_secret)}

    def call(self, method, url, body=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = dict(self.h, **({"Content-Type": ctype} if data is not None else {}))
        try:
            d = json.loads(_OPF.open(urllib.request.Request("https://open.feishu.cn/open-apis" + url, data=data, headers=h, method=method), timeout=300).read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{url} HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}")
        if d.get("code") != 0:
            raise RuntimeError(f"{url} → {d.get('code')} {d.get('msg')}")
        return d.get("data", {})

    def send(self, msg_type, content):
        self.call("POST", "/im/v1/messages?receive_id_type=open_id",
                  {"receive_id": self.user, "msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)})

    def text(self, t):
        self.send("text", {"text": t})

    @staticmethod
    def _form(fields, fname, data):
        b = "----vfd" + os.urandom(6).hex()
        out = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields)
        out += f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + data + f"\r\n--{b}--\r\n".encode()
        return out, f"multipart/form-data; boundary={b}"

    def file(self, path):
        """≤30MB：传到消息文件再私信。"""
        name = os.path.basename(path)
        body, ct = self._form([("file_type", "stream"), ("file_name", name)], name, open(path, "rb").read())
        key = self.call("POST", "/im/v1/files", raw=body, ctype=ct)["file_key"]
        self.send("file", {"file_key": key})

    def drive(self, path, note):
        """大文件：云空间分片上传（要应用身份权限 drive:file:upload，且应用新版本已发布），给用户开权限，私信链接。"""
        name, size = os.path.basename(path), os.path.getsize(path)
        root = self.call("GET", "/drive/explorer/v2/root_folder/meta")["token"]
        prep = self.call("POST", "/drive/v1/files/upload_prepare", {"file_name": name, "parent_type": "explorer", "parent_node": root, "size": size})
        uid, bs, n = prep["upload_id"], prep["block_size"], prep["block_num"]
        with open(path, "rb") as f:
            for seq in range(n):
                chunk = f.read(bs)
                body, ct = self._form([("upload_id", uid), ("seq", seq), ("size", len(chunk)), ("checksum", zlib.adler32(chunk))], name, chunk)
                self.call("POST", "/drive/v1/files/upload_part", raw=body, ctype=ct)
        tok = self.call("POST", "/drive/v1/files/upload_finish", {"upload_id": uid, "block_num": n})["file_token"]
        self.call("POST", f"/drive/v1/permissions/{tok}/members?type=file&need_notification=false",
                  {"member_type": "openid", "member_id": self.user, "perm": "full_access"})
        link = f"https://www.feishu.cn/file/{tok}"
        self.text(f"{note}\n{name}（{size / 1048576:.0f}MB 原画质）\n{link}")
        return link


def phone_version(src, dest, target=28 * 1024 * 1024):
    """压一版手机看的（≤30MB）：按片长算码率，两遍编码，分辨率不变。"""
    dur = max(1.0, probe(src)["dur"])
    vbit = int(target * 8 / dur - 128_000)
    if vbit < 300_000:
        raise RuntimeError(f"片子 {dur:.0f} 秒太长，压到 30MB 以内画质会太差")
    log = dest + ".2pass"
    sh("ffmpeg", "-v", "error", "-y", "-i", src, "-c:v", "libx264", "-b:v", str(vbit), "-pass", "1", "-passlogfile", log, "-an", "-f", "mp4", "/dev/null", check=True)
    sh("ffmpeg", "-v", "error", "-y", "-i", src, "-c:v", "libx264", "-b:v", str(vbit), "-pass", "2", "-passlogfile", log,
       "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", dest, check=True)
    for f in os.listdir(os.path.dirname(dest)):
        if f.startswith(os.path.basename(log)):
            os.remove(os.path.join(os.path.dirname(dest), f))
    return dest


def cmd_deliver(pj, args):
    path = args.get("file") if isinstance(args.get("file"), str) else None
    if not path:
        pj.require("qc")
        path = pj.state()["mix"]["report"].get("file") or pj.p("final", f"{os.path.basename(pj.dir)}.mp4")
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        raise SystemExit(f"没有这个文件：{path}")
    note = args.get("note") if isinstance(args.get("note"), str) else f"《{pj.load('brief.json').get('title') or pj.name}》"
    fs = Feishu()
    copy = pj.p("final", "文案.txt")
    fs.text(note + (("\n\n" + open(copy, encoding="utf-8").read().strip()) if os.path.exists(copy) and not args.get("file") else ""))
    size = os.path.getsize(path)
    if size <= LIMIT and not args.get("phone"):
        fs.file(path)
        pj.log(f"飞书交付：{os.path.basename(path)}（{size / 1048576:.1f}MB）直接私信")
        print(f"✅ 已私信 {os.path.basename(path)}（{size / 1048576:.1f}MB）"); return
    why = ""
    if not args.get("phone"):
        try:
            link = fs.drive(path, note)
            pj.log(f"飞书交付：{os.path.basename(path)}（{size / 1048576:.0f}MB）走云空间 {link}")
            print(f"✅ 原画质 {size / 1048576:.0f}MB 已传云空间并私信链接：{link}"); return
        except Exception as e:
            why = str(e)
            pj.log(f"飞书云空间上传失败：{why[:200]}")
    small = os.path.splitext(path)[0] + "_手机版.mp4"
    phone_version(path, small)
    fs.file(small)
    if why:
        fs.text(f"原画质 {size / 1048576:.0f}MB 这次没传上（飞书云空间上传权限还没生效），先发了手机版。")
    pj.log(f"飞书交付：手机版 {os.path.basename(small)}（{os.path.getsize(small) / 1048576:.1f}MB）")
    print(f"✅ 已私信手机版 {os.path.basename(small)}（{os.path.getsize(small) / 1048576:.1f}MB）"
          + (f"\n⚠️ 原画质没发出去：{why[:300]}\n（多半是飞书应用加了云空间上传权限 drive:file:upload 但新版本还没发布；发布后重跑 vf deliver）" if why else ""))
