import urllib.request
import json
import os
import sys

# Ensure stdout prints UTF-8 characters properly
sys.stdout.reconfigure(encoding='utf-8')

# Helper to load .env manually checking current and parent directories
def load_env():
    env = {}
    paths = [".env", "../.env"]
    for p in paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        env[key.strip()] = val.strip()
            break
    return env

def main():
    env = load_env()
    token = env.get("QIWEI_TOKEN")
    guid = env.get("QIWEI_GUID")
    target_user_id = "1688855528918944"
    
    if not token or not guid:
        print("❌ 错误: .env 中未找到 QIWEI_TOKEN 或 QIWEI_GUID!")
        sys.exit(1)
    
    print("=" * 60)
    print("           QiWe 开放平台自动化测试校验开始           ")
    print("=" * 60)
    print(f"Token: {token[:8]}...{token[-8:]}")
    print(f"GUID:  {guid}")
    print(f"目标接收人 (自己): {target_user_id}")
    print("-" * 60)

    headers = {
        "X-QIWEI-TOKEN": token,
        "Content-Type": "application/json"
    }

    # 1. 定义接口请求函数
    def call_api(method, params):
        url = "http://manager.qiweapi.com/qiwe/api/qw/doApi"
        payload = json.dumps({"method": method, "params": params}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as res:
                return json.loads(res.read().decode('utf-8'))
        except Exception as e:
            return {"code": -1, "msg": f"请求异常: {str(e)}"}

    # 2. 获取个人信息测试 (/user/getProfile)
    print("\n🔍 步骤 1: 正在调用 [/user/getProfile] 查询微信实例基本信息...")
    profile_res = call_api("/user/getProfile", {"guid": guid})
    
    if profile_res.get("code") == 0:
        print("✅ 获取个人信息成功！API 响应正文如下:")
        print(json.dumps(profile_res, indent=4, ensure_ascii=False))
    else:
        print(f"❌ 获取个人信息失败: {profile_res.get('msg')}")
        print("完整响应:", json.dumps(profile_res, indent=4, ensure_ascii=False))
        return

    # 3. 发送消息测试 (/msg/sendText)
    print(f"\n🚀 步骤 2: 正在调用 [/msg/sendText] 向自身账户 ({target_user_id}) 发送测试消息...")
    message_content = "【燃AI 1.0 测试】您好！这是一条来自 Antigravity 智能助手的 QiWe API 独立功能测试消息。收到此消息说明您的接口连接完全正常！"
    send_res = call_api("/msg/sendText", {
        "guid": guid,
        "told": target_user_id,
        "content": message_content
    })

    if send_res.get("code") == 0:
        print("✅ 消息发送成功！API 响应正文:")
        print(json.dumps(send_res, indent=4, ensure_ascii=False))
        print("\n🎉 恭喜！您现在可以查看手机或电脑端企业微信，应该已经收到了这条测试消息！")
    else:
        print(f"❌ 消息发送失败: {send_res.get('msg')}")
        print("完整响应:", json.dumps(send_res, indent=4, ensure_ascii=False))

    print("=" * 60)

if __name__ == "__main__":
    main()
