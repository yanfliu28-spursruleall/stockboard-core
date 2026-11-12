"""口令门：PBKDF2-SHA256(30万次) 派生 AES-256-GCM 密钥，密文 = 12 字节 IV + 密文(含 tag)，明文是 gzip(完整页面)。浏览器端用 WebCrypto 解密。

登录状态：解锁成功后把“派生出的密钥”(不是口令)存进浏览器 ——
  · 默认存 sessionStorage：刷新页面不必重输，关闭标签页即清除；
  · 勾选“在此设备记住 30 天”才存 localStorage；
  · 盐必须在多次构建间保持不变，否则每次定时刷新后已保存的密钥都会失效（见 stable_salt）。
更换口令后，已保存的旧密钥会解密失败 → 页面自动清除并要求重新输入。"""
import base64, gzip, os
from pathlib import Path
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def stable_salt(path) -> bytes:
    """盐只生成一次并保存在本机(secrets/salt.bin，已被 .gitignore 忽略)。盐不是秘密，固定它不影响安全；每次加密的 IV 仍是随机的。"""
    p = Path(path)
    if p.exists() and p.stat().st_size == 16:
        return p.read_bytes()
    p.parent.mkdir(parents=True, exist_ok=True)
    s = os.urandom(16)
    p.write_bytes(s)
    return s


def encrypt(html: str, passphrase: str, iters: int, salt: bytes) -> bytes:
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters).derive(passphrase.encode())
    iv = os.urandom(12)
    return iv + AESGCM(key).encrypt(iv, gzip.compress(html.encode("utf-8"), 9), None)


GATE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0b0f14">
<meta name="robots" content="noindex,nofollow"><title>US Equity Board · 受保护</title>
<style>body{margin:0;background:#0b0f14;color:#e8eef5;font:14px system-ui,"Microsoft YaHei";display:grid;place-items:center;min-height:100vh}
.c{box-sizing:border-box;width:min(380px,calc(100vw - 32px));background:#131b24;border:1px solid #243040;border-radius:16px;padding:32px 26px;text-align:center}
h1{font-size:18px;margin:0 0 6px}p{color:#8a9aab;font-size:12.5px;margin:0 0 18px}
input[type=password]{width:100%;box-sizing:border-box;padding:12px;font-size:16px;border-radius:9px;border:1px solid #243040;background:#0f151c;color:#e8eef5;margin-bottom:10px}
button{width:100%;padding:13px;font-size:16px;border:0;border-radius:9px;background:#1d9bf0;color:#fff;font-weight:700;cursor:pointer}button:disabled{opacity:.6}
label{display:block;text-align:left;color:#8a9aab;font-size:12.5px;margin:2px 0 12px;cursor:pointer}
#m{margin-top:12px;min-height:18px;font-size:12.5px;color:#f85149}#m.ok{color:#3fb950}</style></head><body><div class="c">
<h1>🔒 US Equity Board</h1><p>页面内容已加密，请输入访问口令。授权访问，请勿外传。</p>
<input id="pw" type="password" placeholder="访问口令" autofocus autocomplete="current-password">
<label><input id="rm" type="checkbox"> 在此设备记住 30 天（公用电脑请不要勾选）</label>
<button id="go">进入</button><div id="m"></div></div>
<script>
var SALT=Uint8Array.from(atob('__SALT__'),function(c){return c.charCodeAt(0);}),ITER=__ITER__,SK='usboard_k1',DAYS=30;
function b64(u){var s='';for(var i=0;i<u.length;i++)s+=String.fromCharCode(u[i]);return btoa(s);}
function unb64(s){return Uint8Array.from(atob(s),function(c){return c.charCodeAt(0);});}
function msg(t,ok){var m=document.getElementById('m');m.className=ok?'ok':'';m.textContent=t||'';}
function render(h){document.open();document.write(h);document.close();}
async function fetchBin(){var r=await fetch('app.bin',{cache:'no-store'});if(!r.ok)throw new Error('HTTP '+r.status);return new Uint8Array(await r.arrayBuffer());}
async function decrypt(key,ab){
  var pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:ab.slice(0,12)},key,ab.slice(12));
  var ds=new DecompressionStream('gzip'),w=ds.writable.getWriter();w.write(new Uint8Array(pt));w.close();
  return new Response(ds.readable).text();
}
async function withPw(pw){
  var km=await crypto.subtle.importKey('raw',new TextEncoder().encode(pw),'PBKDF2',false,['deriveKey']);
  var key=await crypto.subtle.deriveKey({name:'PBKDF2',salt:SALT,iterations:ITER,hash:'SHA-256'},km,{name:'AES-GCM',length:256},true,['decrypt']);
  var html=await decrypt(key,await fetchBin());
  return {html:html,raw:b64(new Uint8Array(await crypto.subtle.exportKey('raw',key)))};
}
async function withRaw(k){
  var key=await crypto.subtle.importKey('raw',unb64(k),{name:'AES-GCM'},false,['decrypt']);
  return decrypt(key,await fetchBin());
}
function stored(){try{var s=sessionStorage.getItem(SK);if(s)return s;var l=JSON.parse(localStorage.getItem(SK)||'null');if(l&&l.exp>Date.now())return l.k;}catch(e){}return null;}
function clearStored(){try{sessionStorage.removeItem(SK);localStorage.removeItem(SK);}catch(e){}}
function save(k,remember){try{sessionStorage.setItem(SK,k);if(remember)localStorage.setItem(SK,JSON.stringify({k:k,exp:Date.now()+DAYS*864e5}));}catch(e){}}
var go=document.getElementById('go'),pwi=document.getElementById('pw');
go.onclick=async function(){
  var pw=pwi.value.trim();if(!pw){msg('请输入口令');return;}
  go.disabled=true;msg('正在解密…',true);
  try{var r=await withPw(pw);save(r.raw,document.getElementById('rm').checked);render(r.html);}
  catch(e){go.disabled=false;msg(e&&e.name==='OperationError'?'口令不正确':'加载失败：'+(e&&e.message||e));}
};
pwi.addEventListener('keydown',function(e){if(e.key==='Enter')go.click();});
(async function(){   // 自动登录：刷新页面时用已保存的密钥直接解密
  var k=stored();if(!k)return;go.disabled=true;msg('正在验证已保存的登录…',true);
  try{render(await withRaw(k));}
  catch(e){go.disabled=false;
    if(e&&e.name==='OperationError'){clearStored();msg('登录已失效（口令可能已更换），请重新输入');}   // 密钥不对才清除；网络错误则保留
    else msg('网络异常，请稍后刷新或手动输入口令');}
})();
</script></body></html>"""


def gate_html(salt: bytes, iters: int) -> str:
    return GATE.replace("__SALT__", base64.b64encode(salt).decode()).replace("__ITER__", str(iters))
