"""Evaluate installed loopback Ollama models on synthetic candidate facts."""
import json
import math
import statistics
import time

import requests

MODELS = ["qwen3:4b", "qwen3.5:4b-q4_K_M", "qwen2.5:3b-instruct-q4_K_M"]
CASES = [(12, 210), (25, 350), (40, 480)]

def measure(model):
    times = []
    valid = grounded = errors = 0
    for minutes, sodium in CASES:
        prompt = (f"후보 candidate_1의 조리 시간은 {minutes}분, 나트륨은 {sodium}mg이다. "
                  'candidate_code와 reason 두 문자열만 가진 JSON 객체로 답하라. '
                  'reason에는 제공된 두 수치와 단위를 한국어 한 문장으로 설명하라.')
        start = time.perf_counter()
        try:
            response = requests.post("http://127.0.0.1:11434/api/chat", timeout=90,
                allow_redirects=False, json={"model":model,"stream":False,"think":False,
                "format":"json","messages":[{"role":"user","content":prompt}],
                "options":{"temperature":0,"seed":7,"num_ctx":2048,"num_predict":100}})
            response.raise_for_status()
            data = json.loads(response.json()["message"]["content"])
            if (isinstance(data, dict) and set(data)=={"candidate_code","reason"} and
                    data["candidate_code"]=="candidate_1" and isinstance(data["reason"],str)):
                valid += 1
                reason = data["reason"].replace(" ", "")
                grounded += int(f"{minutes}분" in reason and f"{sodium}mg" in reason)
        except (requests.RequestException, ValueError, KeyError):
            errors += 1
        times.append(time.perf_counter()-start)
    return {"model":model,"samples":len(times),"contract":valid,"both_facts":grounded,
            "errors":errors,"mean_s":round(statistics.mean(times),3),
            "p95_s":round(sorted(times)[math.ceil(.95*len(times))-1],3),"api_cost_usd":0}

if __name__=="__main__":
    for model in MODELS:
        print(json.dumps(measure(model)), flush=True)
