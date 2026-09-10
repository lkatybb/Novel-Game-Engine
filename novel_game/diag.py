"""诊断上传问题"""
import requests

url = "http://127.0.0.1:8000/api/novel/upload"
files = {"file": ("test.txt", open("data/novels/西游记-样本.txt", "rb"), "text/plain")}
print("上传中...")
resp = requests.post(url, files=files, timeout=120)
print(f"状态码: {resp.status_code}")
print(f"响应: {resp.text[:500]}")
