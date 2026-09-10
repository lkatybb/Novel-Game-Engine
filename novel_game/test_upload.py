"""快速测试上传API"""
import urllib.request
import json
import uuid

boundary = uuid.uuid4().hex
with open("data/novels/西游记-样本.txt", "rb") as f:
    file_data = f.read()

header = (
    "--" + boundary + "\r\n"
    'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
    "Content-Type: text/plain\r\n\r\n"
).encode()
footer = ("\r\n--" + boundary + "--\r\n").encode()
body = header + file_data + footer

req = urllib.request.Request(
    "http://127.0.0.1:8000/api/novel/upload",
    data=body,
    headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
)
with urllib.request.urlopen(req, timeout=120) as resp:
    result = json.loads(resp.read())
print("结果:", result)
