"""粗略的静态检查（本机无 Node）：反引号配对、括号平衡、顶层标识符重名。"""
import re, sys, collections

sys.stdout.reconfigure(encoding="utf-8")
h = open(sys.argv[1], encoding="utf-8").read()
js = re.search(r"<script>(.*)</script>", h, re.S).group(1).replace("/[&<>\"]/g", "/X/g")
BS = chr(92)
print("反引号数量(应为偶数):", js.count("`"))


def scan(src):
    st, tpl, mode = [], [], ["code"]
    pairs = {")": "(", "]": "[", "}": "{"}
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if mode[-1] == "code":
            if c in "([{":
                st.append(c)
            elif c in ")]}":
                if c == "}" and tpl and len(st) == tpl[-1] + 1:
                    st.pop(); tpl.pop(); mode.pop(); i += 1; continue
                if not st or st.pop() != pairs[c]:
                    return f"括号不平衡 @ {i}: ...{src[max(0, i - 70):i + 20]!r}"
            elif c == "`":
                mode.append("tpl")
            elif c in "'\"":
                q = c; i += 1
                while i < n and src[i] != q:
                    if src[i] == BS:
                        i += 1
                    i += 1
            elif c == "/" and src[i + 1:i + 2] == "/" and src[i - 1:i] != ":":
                while i < n and src[i] != "\n":
                    i += 1
        else:
            if c == BS:
                i += 1
            elif c == "`":
                mode.pop()
            elif c == "$" and src[i + 1:i + 2] == "{":
                tpl.append(len(st)); st.append("{"); mode.append("code"); i += 1
        i += 1
    return f"OK 剩余栈={st} 模板栈={tpl} 模式={mode}"


print(scan(js))
tops = re.findall(r"^(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)", js, re.M)
print("顶层重名:", [k for k, v in collections.Counter(tops).items() if v > 1])
