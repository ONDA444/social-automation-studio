import urllib.request, urllib.parse, sys

base = "http://127.0.0.1:8000/media?path="
root = r"D:/Users/onda/Desktop/SITEMA YUT"
tests = [
    ("parent-traversal-to-source", root + "/output/../backend/config.py"),
    ("sibling-prefix-bypass", root + "-evil/secret.txt"),
    ("output-dir-itself", root + "/output"),
]
for label, p in tests:
    url = base + urllib.parse.quote(p, safe="")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read(200)
            print(label, "->", r.status, "len", len(body))
    except urllib.error.HTTPError as e:
        print(label, "-> HTTP", e.code, e.reason)
    except Exception as e:
        print(label, "-> ERR", type(e).__name__, e)
